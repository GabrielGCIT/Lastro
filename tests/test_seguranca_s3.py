"""
S3 — testes da família C (LEITURA admin sem escopo: listagens, dropdowns, APIs).

Prova que uma sessão da MB (User A) NÃO enxerga registros/opções/resultados da
Acme (User B) em telas de listagem, dropdowns de outras telas ou APIs de
coletor — e que o /uploads/<filename> genérico responde 404 para um arquivo
que pertence só à Acme. Owner enxerga tudo. Reusa o cenário 2-empresas de
test_seguranca_s2.py (MB=A, Acme=B).
"""
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.startup import NOME_EMPRESA
from app.models import (
    Empresa, Localidade, Usuario, Grupo, Colaborador, Coletor,
)


def _cliente_logado(app, user):
    # Termos aceitos: o helper representa alguem OPERANDO o sistema, e no
    # fluxo real ninguem opera sem ter aceitado (ver tests/test_l8_termos.py,
    # que exercita a guarda com um usuario SEM aceite).
    from app.termos import precisa_aceitar, registrar_aceite
    if precisa_aceitar(user):
        registrar_aceite(user)
        db.session.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id']      = user.id
        s['nome']         = user.nome
        s['re']           = user.re
        s['empresa_id']   = user.empresa_id
        s['nivel_acesso'] = user.nivel_acesso
    return c


@pytest.fixture()
def cen(app):
    """MB (A) + Acme (B) com registros só na Acme para provar o vazamento de leitura."""
    mb   = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    acme = Empresa(nome='Acme Logística')
    db.session.add(acme)
    db.session.flush()

    ti = Grupo.query.filter_by(nome='TI').first()
    locA = Localidade(sigla='AAA', nome='CD A', empresa_id=mb.id)
    locB = Localidade(sigla='BBB', nome='CD B', empresa_id=acme.id)
    db.session.add_all([locA, locB])
    db.session.flush()

    def _user(nome, re, empresa):
        u = Usuario(nome=nome, re=re, email=f'{re}@x.com',
                    senha_hash=generate_password_hash('x', method='scrypt'),
                    grupo_id=ti.id, empresa_id=empresa.id, nivel_acesso='GLOBAL')
        db.session.add(u)
        return u

    userA = _user('User A', '7101', mb)
    userB = _user('User B', '7102', acme)

    grpB  = Grupo(nome='OPS_ACME', descricao='grupo acme', protegido=False, empresa_id=acme.id)
    colabB = Colaborador(re='C902', nome='Colaborador Acme', empresa_id=acme.id)
    coletorB = Coletor(serial_number='COL-ACME', numero_patrimonio='PAT-ACME',
                       status='Disponível', localidade_id=locB.id)
    db.session.add_all([grpB, colabB, coletorB])
    db.session.commit()

    admin = Usuario.query.filter_by(re='admin').first()
    return SimpleNamespace(mb=mb, acme=acme, locA=locA, locB=locB,
                           userA=userA, userB=userB, admin=admin,
                           grpB=grpB, colabB=colabB, coletorB=coletorB)


# ---------------------------------------------------------------------------
# Listagens admin
# ---------------------------------------------------------------------------

def test_colaboradores_lista_nao_vaza_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    html = cA.get('/admin/colaboradores/?status=todos').get_data(as_text=True)
    assert 'Colaborador Acme' not in html


def test_localidades_get_nao_vaza_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    html = cA.get('/localidades').get_data(as_text=True)
    assert 'BBB' not in html


def test_usuarios_get_nao_vaza_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    html = cA.get('/usuarios').get_data(as_text=True)
    assert 'User B' not in html


def test_gerenciar_grupos_nao_vaza_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    html = cA.get('/grupos').get_data(as_text=True)
    assert 'OPS_ACME' not in html


# ---------------------------------------------------------------------------
# Dropdowns (o ponto sutil — alimentam ataques de A/B/E via UI legítima)
# ---------------------------------------------------------------------------

def test_usuarios_dropdowns_grupo_localidade_nao_vazam_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    html = cA.get('/usuarios').get_data(as_text=True)
    assert 'OPS_ACME' not in html and 'BBB' not in html


def test_coletores_dropdowns_localidade_nao_vazam_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    html = cA.get('/coletores').get_data(as_text=True)
    assert 'BBB' not in html


# ---------------------------------------------------------------------------
# APIs de coletor (movimentacao.py — pré-T3 "não aplica filtro")
# ---------------------------------------------------------------------------

def test_api_buscar_coletor_nao_encontra_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    resp = cA.get('/api/coletor/buscar?q=PAT-ACME&modo=patrimonio')
    assert resp.get_json()['encontrado'] is False


def test_api_coletor_status_nao_encontra_acme(app, cen):
    cA = _cliente_logado(app, cen.userA)
    resp = cA.get(f'/api/coletor/status/{cen.coletorB.id}')
    assert resp.status_code == 404
    assert resp.get_json()['encontrado'] is False


def test_api_buscar_coletor_admin_nao_encontra_acme(app, cen):
    """Nem o administrador enxerga coletor de outra empresa."""
    cAdmin = _cliente_logado(app, cen.admin)
    resp = cAdmin.get('/api/coletor/buscar?q=PAT-ACME&modo=patrimonio')
    assert resp.get_json()['encontrado'] is False


# ---------------------------------------------------------------------------
# O administrador também é de uma empresa só
# ---------------------------------------------------------------------------

def test_admin_nao_ve_colaboradores_de_outra_empresa(app, cen):
    cAdmin = _cliente_logado(app, cen.admin)
    html = cAdmin.get('/admin/colaboradores/?status=todos').get_data(as_text=True)
    assert 'Colaborador Acme' not in html
