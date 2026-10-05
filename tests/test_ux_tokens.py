"""
Testes do patch UX Tokens — preferências de tema/cor de acento por usuário.

Cobrem: persistência das preferências, rejeição de cor malformada (save e
render), a rota de troca de tema e o render nos dois temas com a injeção
segura da cor de acento.
"""
from app import db
from app.models import Usuario
from app.helpers import cor_acento_segura, tema_seguro, CORES_ACENTO_SELECIONAVEIS


def _admin():
    return Usuario.query.filter_by(re='admin').first()


# ---------------------------------------------------------------------------
# Validadores puros
# ---------------------------------------------------------------------------

def test_cor_acento_segura_aceita_hex_valido(app):
    assert cor_acento_segura('#1565C0') == '#1565C0'
    assert cor_acento_segura('#abcdef') == '#abcdef'


def test_cor_acento_segura_rejeita_malformada(app):
    for ruim in (None, '', 'red', '#abc', '#12345', '#1234567',
                 '#12345g', 'javascript:x', '#000;}body{display:none}'):
        assert cor_acento_segura(ruim) is None, ruim


def test_tema_seguro(app):
    assert tema_seguro('light') == 'light'
    assert tema_seguro('dark') == 'dark'
    assert tema_seguro('auto') == 'auto'
    assert tema_seguro('rainbow') is None
    assert tema_seguro(None) is None


def test_paleta_curada_passa_o_proprio_regex(app):
    # Toda cor selecionável precisa ser um hex válido — senão a injeção falharia.
    for cor in CORES_ACENTO_SELECIONAVEIS:
        assert cor_acento_segura(cor) == cor


# ---------------------------------------------------------------------------
# Migração / modelo
# ---------------------------------------------------------------------------

def test_colunas_prefs_existem(app):
    u = _admin()
    u.tema = 'dark'
    u.cor_acento = '#1565C0'
    db.session.commit()
    u2 = _admin()
    assert u2.tema == 'dark'
    assert u2.cor_acento == '#1565C0'


# ---------------------------------------------------------------------------
# Persistência via perfil
# ---------------------------------------------------------------------------

def test_salvar_preferencias_persiste(admin_client, app):
    r = admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'dark', 'cor_acento': '#1565C0',
    }, follow_redirects=True)
    assert r.status_code == 200
    u = _admin()
    assert u.tema == 'dark'
    assert u.cor_acento == '#1565C0'


def test_reset_volta_ao_padrao(admin_client, app):
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'light', 'cor_acento': '#6A1B9A',
    })
    assert _admin().cor_acento == '#6A1B9A'
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'light', 'cor_acento': 'reset',
    })
    assert _admin().cor_acento is None


def test_cor_fora_da_paleta_rejeitada(admin_client, app):
    # Fixa uma cor válida primeiro.
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'light', 'cor_acento': '#1565C0',
    })
    # Hex bem-formado mas fora da paleta curada → recusado, valor anterior intacto.
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'light', 'cor_acento': '#ffffff',
    }, follow_redirects=True)
    assert _admin().cor_acento == '#1565C0'
    # Hex malformado → também recusado.
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'light', 'cor_acento': '#zzz',
    }, follow_redirects=True)
    assert _admin().cor_acento == '#1565C0'


def test_trocar_tema_route(admin_client, app):
    r = admin_client.post('/tema/trocar', data={'tema': 'dark'})
    assert r.status_code == 204
    assert _admin().tema == 'dark'
    # Valor inválido é ignorado (mantém o anterior).
    admin_client.post('/tema/trocar', data={'tema': 'bogus'})
    assert _admin().tema == 'dark'


# ---------------------------------------------------------------------------
# Render nos dois temas + injeção segura
# ---------------------------------------------------------------------------

def test_render_injeta_acento_e_tema(admin_client, app):
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'dark', 'cor_acento': '#6A1B9A',
    })
    html = admin_client.get('/perfil').get_data(as_text=True)
    # Injeção presente com contraste branco (assinatura exclusiva da injeção).
    assert ':root { --accent: #6A1B9A; --accent-contrast: #ffffff; }' in html
    # Anti-flash recebe a preferência de tema do servidor.
    assert '"dark"' in html


def test_render_sem_acento_nao_injeta(admin_client, app):
    admin_client.post('/perfil', data={
        'acao': 'preferencias', 'tema': 'light', 'cor_acento': 'reset',
    })
    html = admin_client.get('/perfil').get_data(as_text=True)
    # Sem preferência → nada injetado (o padrão do :root usa contraste escuro).
    assert '--accent-contrast: #ffffff' not in html


def test_render_ignora_cor_invalida_no_banco(admin_client, app):
    # Simula valor inválido persistido por outro caminho: o render NÃO pode injetá-lo.
    u = _admin()
    u.cor_acento = '#12345'   # falha o regex, cabe na coluna VARCHAR(7)
    db.session.commit()
    html = admin_client.get('/perfil').get_data(as_text=True)
    assert '--accent: #12345' not in html
    assert '--accent-contrast: #ffffff' not in html
