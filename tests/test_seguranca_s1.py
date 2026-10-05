"""
S1 — testes do LADO DA ESCRITA multi-empresa (pacote Segurança pré-T4).

Cobre as três famílias que o S1 fecha:
    A — criações carimbam empresa_id (nunca nascem órfãs; Owner sem tenant bloqueia)
    D — unicidade recortada por empresa (RE, nome de grupo, sigla de localidade)
    E — FK vinda de form/lookup validada contra o tenant (localidade, coletor)

Reusa o padrão de 2 empresas do test_isolamento_empresa (T3), agora provando
que a ESCRITA respeita a fronteira. O portão paramétrico completo é do T6.
"""
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Empresa, Localidade, Usuario, Grupo, Colaborador, Coletor,
)


def _cliente_logado(app, user):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id']      = user.id
        s['nome']         = user.nome
        s['re']           = user.re
        s['empresa_id']   = user.empresa_id
        s['is_owner']     = bool(user.is_owner)
        s['nivel_acesso'] = user.nivel_acesso
    return c


@pytest.fixture()
def cenario(app):
    """MB (A) + Acme (B), cada uma com localidade e um TI_MASTER GLOBAL não-owner."""
    mb   = Empresa.query.filter_by(nome='Martin Brower').first()
    acme = Empresa(nome='Acme Logística', ativa=True)
    db.session.add(acme)
    db.session.flush()

    ti = Grupo.query.filter_by(nome='TI_MASTER').first()

    locA = Localidade(sigla='AAA', nome='CD A', empresa_id=mb.id)
    locB = Localidade(sigla='BBB', nome='CD B', empresa_id=acme.id)
    db.session.add_all([locA, locB])
    db.session.flush()

    def _user(nome, re, email, empresa, owner=False):
        u = Usuario(nome=nome, re=re, email=email,
                    senha_hash=generate_password_hash('x', method='scrypt'),
                    grupo_id=ti.id, empresa_id=empresa.id if empresa else None,
                    nivel_acesso='GLOBAL', is_owner=owner)
        db.session.add(u)
        return u

    userA   = _user('User A', '7001', 'a@a.com', mb)
    userB   = _user('User B', '7002', 'b@b.com', acme)
    semTen  = _user('Sem Tenant', '7003', 's@s.com', None)   # não-owner sem empresa
    db.session.commit()

    admin = Usuario.query.filter_by(re='admin').first()      # Owner de bootstrap (MB)
    return SimpleNamespace(mb=mb, acme=acme, locA=locA, locB=locB,
                           userA=userA, userB=userB, semTen=semTen, admin=admin)


# ---------------------------------------------------------------------------
# Helpers do lado da escrita (unitário)
# ---------------------------------------------------------------------------

def test_empresa_para_escrita_owner_cai_na_propria(app, cenario):
    from flask import session
    from app.helpers import empresa_para_escrita
    with app.test_request_context():
        session['is_owner']   = True
        session['empresa_id'] = cenario.mb.id
        assert empresa_para_escrita() == cenario.mb.id       # Owner grava na própria (MB)


def test_empresa_para_escrita_sem_tenant_none(app, cenario):
    from flask import session
    from app.helpers import empresa_para_escrita
    with app.test_request_context():
        session['is_owner']   = False
        session['empresa_id'] = None
        assert empresa_para_escrita() is None                # bloqueia a criação a montante


def test_localidade_para_escrita_recorta_no_tenant(app, cenario):
    from flask import session
    from app.helpers import localidade_para_escrita
    with app.test_request_context():
        session['is_owner']     = False
        session['empresa_id']   = cenario.mb.id
        session['nivel_acesso'] = 'GLOBAL'
        assert localidade_para_escrita(cenario.locA.id) == (True, cenario.locA.id)
        assert localidade_para_escrita(cenario.locB.id) == (False, None)   # outro tenant
        assert localidade_para_escrita(None)            == (True, None)    # ausência ok


def test_localidade_para_escrita_owner_aceita_qualquer(app, cenario):
    from flask import session
    from app.helpers import localidade_para_escrita
    with app.test_request_context():
        session['is_owner'] = True
        assert localidade_para_escrita(cenario.locB.id) == (True, cenario.locB.id)
        assert localidade_para_escrita(999999)          == (False, None)   # inexistente


# ---------------------------------------------------------------------------
# Família A — criações carimbam o tenant
# ---------------------------------------------------------------------------

def test_colaborador_criar_carimba_empresa(app, cenario):
    cB = _cliente_logado(app, cenario.userB)
    cB.post('/admin/colaboradores/novo', data={'re': 'C900', 'nome': 'Colab Acme'})
    c = Colaborador.query.filter_by(re='C900').first()
    assert c is not None and c.empresa_id == cenario.acme.id


def test_criacao_sem_tenant_e_bloqueada(app, cenario):
    cS = _cliente_logado(app, cenario.semTen)
    cS.post('/admin/colaboradores/novo', data={'re': 'C901', 'nome': 'Órfão'})
    assert Colaborador.query.filter_by(re='C901').first() is None   # nunca nasce órfão


def test_localidade_criar_carimba_empresa(app, cenario):
    cB = _cliente_logado(app, cenario.userB)
    cB.post('/localidades', data={'acao': 'criar', 'sigla': 'ZZZ', 'nome': 'CD Novo'})
    loc = Localidade.query.filter_by(sigla='ZZZ').first()
    assert loc is not None and loc.empresa_id == cenario.acme.id


def test_grupo_criar_carimba_empresa(app, cenario):
    cB = _cliente_logado(app, cenario.userB)
    cB.post('/grupos/criar', data={'nome': 'OPERACAO_ACME'})
    g = Grupo.query.filter_by(nome='OPERACAO_ACME').first()
    assert g is not None and g.empresa_id == cenario.acme.id


# ---------------------------------------------------------------------------
# Família D — unicidade por empresa (mesmo valor coexiste entre tenants)
# ---------------------------------------------------------------------------

def test_re_colaborador_unico_por_empresa(app, cenario):
    from app.helpers import re_colaborador_em_uso
    db.session.add(Colaborador(re='C500', nome='Dup A', empresa_id=cenario.mb.id))
    db.session.commit()
    assert re_colaborador_em_uso('C500', cenario.mb.id)   is True    # ocupado na MB
    assert re_colaborador_em_uso('C500', cenario.acme.id) is False   # livre na Acme


def test_grupo_mesmo_nome_em_dois_tenants(app, cenario):
    """O mesmo nome de grupo pode existir em empresas diferentes (constraint composta)."""
    cA = _cliente_logado(app, cenario.userA)
    cB = _cliente_logado(app, cenario.userB)
    cA.post('/grupos/criar', data={'nome': 'SUPERVISAO'})
    cB.post('/grupos/criar', data={'nome': 'SUPERVISAO'})
    grupos = Grupo.query.filter_by(nome='SUPERVISAO').all()
    empresas = {g.empresa_id for g in grupos}
    assert {cenario.mb.id, cenario.acme.id} <= empresas


def test_localidade_mesma_sigla_em_dois_tenants(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    cB = _cliente_logado(app, cenario.userB)
    cA.post('/localidades', data={'acao': 'criar', 'sigla': 'DUP', 'nome': 'CD MB'})
    cB.post('/localidades', data={'acao': 'criar', 'sigla': 'DUP', 'nome': 'CD Acme'})
    empresas = {l.empresa_id for l in Localidade.query.filter_by(sigla='DUP').all()}
    assert {cenario.mb.id, cenario.acme.id} <= empresas


# ---------------------------------------------------------------------------
# Família E — FK de outro tenant é rejeitada / tratada como inexistente
# ---------------------------------------------------------------------------

def test_coletor_criar_com_localidade_de_outro_tenant_rejeitado(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    cA.post('/coletores', data={'serial_number': 'SN-ALHEIO', 'status': 'Disponível',
                                'localidade_id': cenario.locB.id})
    assert Coletor.query.filter_by(serial_number='SN-ALHEIO').first() is None


def test_coletor_criar_com_localidade_propria_ok(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    cA.post('/coletores', data={'serial_number': 'SN-OK', 'status': 'Disponível',
                                'localidade_id': cenario.locA.id})
    c = Coletor.query.filter_by(serial_number='SN-OK').first()
    assert c is not None and c.localidade_id == cenario.locA.id


def test_retirada_coletor_de_outro_tenant_nao_encontrado(app, cenario):
    # Coletor e colaborador vivem na Acme; o operador logado é da MB.
    col = Coletor(serial_number='SN-ACME', numero_patrimonio='PAT-ACME',
                  status='Disponível', localidade_id=cenario.locB.id)
    db.session.add(col)
    db.session.add(Colaborador(re='9500', nome='Op Acme', empresa_id=cenario.acme.id))
    db.session.commit()

    cA = _cliente_logado(app, cenario.userA)
    cA.post('/operacao/retirar', data={
        'busca_valor': 'PAT-ACME', 'busca_modo': 'patrimonio',
        're_colaborador': '9500',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
    }, follow_redirects=True)
    db.session.refresh(col)
    assert col.status == 'Disponível'              # não foi retirado por sessão de outro tenant
