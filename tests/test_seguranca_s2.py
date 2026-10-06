"""
S2 — testes da família B (IDOR de ESCRITA) + decisões de produto do pacote.

Prova que uma sessão da MB (User A) NÃO consegue mutar registros da Acme (User B)
por id: o alvo é tratado como inexistente e nada muda no banco. Cobre os dois
mecanismos de guard — empresa_visivel (empresa_id direto) e escopo por localidade
(Coletor).
"""
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Empresa, Localidade, Usuario, Grupo, Colaborador, Coletor,
    ReativacaoIdentificacao,
)


def _cliente_logado(app, user):
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
    """MB (A) + Acme (B) com registros ESPELHADOS na Acme para o cross-mutate."""
    mb   = Empresa.query.filter_by(nome='Martin Brower').first()
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

    userA = _user('User A', '7001', mb)
    userB = _user('User B', '7002', acme)

    # Registros da Acme (alvos do cross-mutate)
    grpB  = Grupo(nome='OPS_B', descricao='grupo acme', protegido=False, empresa_id=acme.id)
    colabB = Colaborador(re='C002', nome='Colab B', empresa_id=acme.id)
    coletorB = Coletor(serial_number='COLB', numero_patrimonio='PB', status='Suspenso', localidade_id=locB.id)
    db.session.add_all([grpB, colabB, coletorB])
    db.session.commit()

    admin = Usuario.query.filter_by(re='admin').first()
    return SimpleNamespace(mb=mb, acme=acme, locA=locA, locB=locB,
                           userA=userA, userB=userB, admin=admin,
                           grpB=grpB, colabB=colabB, coletorB=coletorB)


# ---------------------------------------------------------------------------
# Guard por empresa_id direto (empresa_visivel)
# ---------------------------------------------------------------------------

def test_colaborador_editar_cross_tenant_bloqueado(app, cen):
    cA = _cliente_logado(app, cen.userA)
    cA.post(f'/admin/colaboradores/{cen.colabB.id}/editar',
            data={'re': 'C002', 'nome': 'HACKED'})
    db.session.refresh(cen.colabB)
    assert cen.colabB.nome == 'Colab B'


def test_colaborador_toggle_cross_tenant_bloqueado(app, cen):
    cA = _cliente_logado(app, cen.userA)
    estado = cen.colabB.ativo
    cA.post(f'/admin/colaboradores/{cen.colabB.id}/toggle')
    db.session.refresh(cen.colabB)
    assert cen.colabB.ativo == estado


def test_usuario_editar_cross_tenant_bloqueado(app, cen):
    cA = _cliente_logado(app, cen.userA)
    cA.post('/usuarios', data={'id': cen.userB.id, 'nome': 'HACKED', 're': '7002',
                               'grupo_id': cen.userB.grupo_id, 'nivel_acesso': 'GLOBAL'})
    db.session.refresh(cen.userB)
    assert cen.userB.nome == 'User B'


def test_perfil_nao_se_exclui_nem_se_edita_por_rota(app, cen):
    """Os perfis são fixos: as rotas de criar, excluir e editar permissões
    deixaram de existir — nem o próprio TI as alcança."""
    from app.models import Usuario
    cAdmin = _cliente_logado(app, Usuario.query.filter_by(re='admin').first())
    ti = Grupo.query.filter_by(nome='TI', empresa_id=cen.mb.id).first()
    assert cAdmin.post(f'/grupos/{ti.id}/excluir').status_code == 404
    assert cAdmin.post(f'/grupos/{ti.id}/permissoes').status_code == 404
    assert cAdmin.post('/grupos/criar', data={'nome': 'NOVO'}).status_code == 404
    assert db.session.get(Grupo, ti.id) is not None


def test_localidade_editar_cross_tenant_bloqueado(app, cen):
    cA = _cliente_logado(app, cen.userA)
    cA.post('/localidades', data={'acao': 'editar', 'id': cen.locB.id,
                                  'sigla': 'HACK', 'nome': 'HACKED'})
    db.session.refresh(cen.locB)
    assert cen.locB.sigla == 'BBB'


# ---------------------------------------------------------------------------
# Guard por localidade (Coletor)
# ---------------------------------------------------------------------------

def test_suspenso_reativar_cross_tenant_bloqueado(app, cen):
    cA = _cliente_logado(app, cen.userA)
    cA.post('/suspensos/reativar', data={'coletor_id': cen.coletorB.id, 'declaracao': 'on'})
    assert ReativacaoIdentificacao.query.filter_by(coletor_id=cen.coletorB.id).count() == 0


# ---------------------------------------------------------------------------
# Coletor editado por id precisa estar no escopo (achado na poda da L0)
# ---------------------------------------------------------------------------

def test_coletor_editar_cross_tenant_bloqueado(app, cen):
    """🔴 O cadastro validava só o CD de DESTINO. Bastava mandar o id de um
    coletor alheio com o próprio CD no formulário para puxá-lo para si."""
    cA = _cliente_logado(app, cen.userA)
    cA.post('/coletores', data={'id': cen.coletorB.id, 'serial_number': 'COLB',
                                'status': 'Disponível', 'localidade_id': cen.locA.id})
    db.session.refresh(cen.coletorB)
    assert cen.coletorB.localidade_id == cen.locB.id
    assert cen.coletorB.status == 'Suspenso'


def test_coletor_reportar_cross_tenant_bloqueado(app, cen):
    """A mesma brecha na tela de reportar problema: mudava o status de coletor
    de outro CD pelo id."""
    cA = _cliente_logado(app, cen.userA)
    resp = cA.post('/operacao/inventario', data={'id': cen.coletorB.id,
                                                 'status': 'Manutenção'})
    assert resp.status_code == 404
    db.session.refresh(cen.coletorB)
    assert cen.coletorB.status == 'Suspenso'


def test_coletor_editar_id_inexistente_nao_quebra(app, cen):
    cA = _cliente_logado(app, cen.userA)
    resp = cA.post('/coletores', data={'id': 999999, 'serial_number': 'X',
                                       'status': 'Disponível', 'localidade_id': cen.locA.id})
    assert resp.status_code == 302


def test_status_fora_da_lista_e_recusado(app, cen):
    """Tirar a opção da tela não impede um POST forjado: as duas rotas que
    gravam status só aceitam os status que existem."""
    from app.models import Coletor
    meu = Coletor(serial_number='COLA', numero_patrimonio='PA',
                  status='Disponível', localidade_id=cen.locA.id)
    db.session.add(meu)
    db.session.commit()
    cA = _cliente_logado(app, cen.userA)

    cA.post('/coletores', data={'id': meu.id, 'serial_number': 'COLA',
                                'status': 'Retrabalho', 'localidade_id': cen.locA.id})
    cA.post('/operacao/inventario', data={'id': meu.id, 'status': 'Não Retornou'})

    db.session.refresh(meu)
    assert meu.status == 'Disponível'


def test_dono_do_coletor_continua_editando(app, cen):
    """A guarda não pode travar quem tem direito."""
    from app.models import Coletor
    meu = Coletor(serial_number='COLA2', numero_patrimonio='PA2',
                  status='Disponível', localidade_id=cen.locA.id)
    db.session.add(meu)
    db.session.commit()
    cA = _cliente_logado(app, cen.userA)
    cA.post('/coletores', data={'id': meu.id, 'serial_number': 'COLA2',
                                'status': 'Manutenção', 'localidade_id': cen.locA.id})
    db.session.refresh(meu)
    assert meu.status == 'Manutenção'
