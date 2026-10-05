"""
T3 — Núcleo de testes de ISOLAMENTO multi-empresa.

Fixture com 2 empresas populadas (MB = A e Acme = B), cada uma com sua
localidade, um usuário GLOBAL não-owner e um colaborador. Afirma que
cross-access em lista/detalhe/API/arquivo é impossível — 404, zero resultados
ou contexto recusado — e que o Owner enxerga tudo. O T6 expande isto para a
suite completa (portão de todo patch futuro).
"""
import os

import pytest

from app import db, FOTO_FOLDER
from app.models import LogAuditoria


def _cliente_logado(app, user):
    """Cliente com a sessão montada como o login faz (T2): user_id + tenant.

    Para rotas não-/api/ o _sync_permissoes re-sincroniza tudo do banco; os
    campos de tenant abaixo cobrem também as rotas sob /api/ (que o hook ignora).
    """
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
def cenario(duas_empresas):
    """Cenário de isolamento — agora a fixture COMPARTILHADA `duas_empresas`
    (conftest, T6). Mantido como alias para não reescrever os ~20 testes deste
    arquivo que já pedem `cenario`."""
    return duas_empresas


# ---------------------------------------------------------------------------
# get_filtro_localidade — contrato fail-closed
# ---------------------------------------------------------------------------

def test_get_filtro_escopa_na_empresa(app, cenario):
    from flask import session
    from app.helpers import get_filtro_localidade
    with app.test_request_context():
        session['is_owner']     = False
        session['empresa_id']   = cenario.mb.id
        session['nivel_acesso'] = 'GLOBAL'
        ids = get_filtro_localidade()
    assert cenario.locA.id in ids
    assert cenario.locB.id not in ids            # localidade de outro tenant fora


def test_get_filtro_owner_recebe_none(app, cenario):
    from flask import session
    from app.helpers import get_filtro_localidade
    with app.test_request_context():
        session['is_owner'] = True
        assert get_filtro_localidade() is None    # None é exclusivo do Owner


def test_get_filtro_nao_owner_sem_empresa_fecha(app, cenario):
    from flask import session
    from app.helpers import get_filtro_localidade
    with app.test_request_context():
        session['is_owner']   = False
        session['empresa_id'] = None
        assert get_filtro_localidade() == []      # fail-closed, nunca "vê tudo"


# ---------------------------------------------------------------------------
# APIs de busca — não cruzam empresa
# ---------------------------------------------------------------------------

def test_api_colaborador_re_nao_cruza_empresa(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    assert cA.get('/admin/colaboradores/api/re/C001').status_code == 200
    assert cA.get('/admin/colaboradores/api/re/C002').status_code == 404


# ---------------------------------------------------------------------------
# Serving de arquivos — 404 cross-tenant (antes de tocar o disco)
# ---------------------------------------------------------------------------

def test_serving_perfil_cross_tenant_404(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    assert cA.get('/uploads/perfis/iso_perfil_b.png').status_code == 404


def test_serving_perfil_desconhecido_404(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    assert cA.get('/uploads/perfis/naoexiste.png').status_code == 404


def test_serving_perfil_mesma_empresa_ok(app, cenario):
    caminho = os.path.join(FOTO_FOLDER, 'iso_perfil_a.png')
    with open(caminho, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n')
    try:
        cA = _cliente_logado(app, cenario.userA)
        assert cA.get('/uploads/perfis/iso_perfil_a.png').status_code == 200
    finally:
        os.remove(caminho)


# ---------------------------------------------------------------------------
# trocar_contexto — vetor cross-tenant (classe do BUG P8c)
# ---------------------------------------------------------------------------

def test_trocar_contexto_recusa_cd_de_outra_empresa(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    cA.post('/contexto/trocar', data={'contexto': f'CD:{cenario.locB.id}'})
    with cA.session_transaction() as s:
        assert s.get('view_context') != f'CD:{cenario.locB.id}'


def test_trocar_contexto_aceita_cd_da_propria_empresa(app, cenario):
    cA = _cliente_logado(app, cenario.userA)
    cA.post('/contexto/trocar', data={'contexto': f'CD:{cenario.locA.id}'})
    with cA.session_transaction() as s:
        assert s.get('view_context') == f'CD:{cenario.locA.id}'


# ---------------------------------------------------------------------------
# LogAuditoria — carimbo + filtro por empresa
# ---------------------------------------------------------------------------

def test_registrar_log_carimba_empresa(app, cenario):
    from flask import session
    from app.helpers import registrar_log
    with app.test_request_context():
        session['empresa_id'] = cenario.acme.id
        session['nome']       = 'X'
        registrar_log('TESTE_CARIMBO', 'detalhe')
    log = LogAuditoria.query.filter_by(acao='TESTE_CARIMBO').first()
    assert log.empresa_id == cenario.acme.id


def test_auditoria_filtra_por_empresa(app, cenario):
    db.session.add(LogAuditoria(acao='TESTE_ISO', detalhe='log-da-mb',   empresa_id=cenario.mb.id))
    db.session.add(LogAuditoria(acao='TESTE_ISO', detalhe='log-da-acme', empresa_id=cenario.acme.id))
    db.session.commit()
    cA = _cliente_logado(app, cenario.userA)
    body = cA.get('/auditoria').get_data(as_text=True)
    assert 'log-da-mb' in body
    assert 'log-da-acme' not in body


# ---------------------------------------------------------------------------
# Owner enxerga todas as empresas
# ---------------------------------------------------------------------------

def test_owner_confirma_colaborador_de_qualquer_empresa(app, cenario):
    cAdmin = _cliente_logado(app, cenario.admin)
    assert cAdmin.get('/admin/colaboradores/api/re/C001').status_code == 200
    assert cAdmin.get('/admin/colaboradores/api/re/C002').status_code == 200
