"""
L0 — o app sobe numa máquina limpa, sem variável de ambiente nenhuma.

O critério da fatia é `python run.py` abrir o login sem SQL Server, sem .env e
sem nada configurado. Estes testes prendem as três peças que tornam isso
verdade — banco em arquivo, SECRET_KEY que se gera e PERMANECE, pragmas do
SQLite — e duas consequências da poda que quebrariam longe da vista: a
permissão negada que redirecionava para o portal (que saiu) e as telas de
administração, que eram alcançadas só pelo portal.
"""
import os

from werkzeug.security import generate_password_hash

import app as pacote_app
from app import db


# ---------------------------------------------------------------------------
# banco e chave sem configuração
# ---------------------------------------------------------------------------

def test_sem_database_url_o_banco_e_um_arquivo_em_instance(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    aplicacao = pacote_app.create_app()
    uri = aplicacao.config['SQLALCHEMY_DATABASE_URI']
    assert uri.startswith('sqlite:///')
    assert uri.endswith('instance/mbassets.db')


def test_secret_key_e_gerada_uma_vez_e_reaproveitada(monkeypatch, tmp_path):
    """🔴 Gerar uma chave nova a cada boot derrubaria a sessão de todo mundo
    sempre que o serviço reiniciasse."""
    arquivo = tmp_path / 'secret_key'
    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.setattr(pacote_app, 'SECRET_KEY_ARQUIVO', str(arquivo))

    primeira = pacote_app._secret_key()
    segunda = pacote_app._secret_key()

    assert arquivo.exists(), 'a chave não foi guardada'
    assert primeira == segunda, 'o segundo boot gerou outra chave'
    assert len(primeira) >= 32


def test_secret_key_do_ambiente_manda(monkeypatch, tmp_path):
    monkeypatch.setenv('SECRET_KEY', 'definida-por-quem-instala')
    monkeypatch.setattr(pacote_app, 'SECRET_KEY_ARQUIVO', str(tmp_path / 'secret_key'))
    assert pacote_app._secret_key() == 'definida-por-quem-instala'
    assert not (tmp_path / 'secret_key').exists()


def test_sqlite_cobra_a_chave_estrangeira(app):
    """Sem `PRAGMA foreign_keys=ON` o SQLite aceita FK apontando para o nada."""
    from sqlalchemy import text
    assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1


def test_banco_em_arquivo_usa_wal(tmp_path):
    """Leitura não bloqueia escrita: o balcão grava enquanto o dashboard lê."""
    from sqlalchemy import text
    caminho = (tmp_path / 'teste.db').as_posix()
    aplicacao = pacote_app.create_app(f'sqlite:///{caminho}')
    with aplicacao.app_context():
        modo = db.session.execute(text('PRAGMA journal_mode')).scalar()
        espera = db.session.execute(text('PRAGMA busy_timeout')).scalar()
        db.session.remove()
        db.engine.dispose()
    assert modo == 'wal'
    assert espera >= 1000


# ---------------------------------------------------------------------------
# o que a poda poderia ter quebrado longe da vista
# ---------------------------------------------------------------------------

def _usuario_sem_permissao():
    from app.models import Empresa, Grupo, Usuario
    mb = Empresa.query.first()
    operador = Grupo.query.filter_by(nome='OPERADOR').first()
    u = Usuario(nome='Sem Permissão', re='5500', email='sem@perm.local',
                senha_hash=generate_password_hash('x', method='scrypt'),
                grupo_id=operador.id, empresa_id=mb.id, nivel_acesso='GLOBAL')
    db.session.add(u)
    db.session.commit()
    return u


def test_permissao_negada_volta_ao_dashboard(app, cliente_logado):
    """🔴 O redirect apontava para o portal, que saiu: toda permissão negada
    virava erro 500 em vez de um aviso."""
    c = cliente_logado(_usuario_sem_permissao())
    resp = c.get('/coletores')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/')


def test_administracao_tem_porta_no_menu(admin_client):
    """As telas de cadastro e acesso eram alcançadas pelo card do portal. Sem o
    portal, sem este link elas existiriam e ninguém chegaria nelas."""
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Administração' in html
    assert 'href="/usuarios"' in html


def test_telas_de_administracao_abrem(admin_client):
    for rota in ('/usuarios', '/grupos', '/localidades', '/admin/colaboradores/',
                 '/auditoria'):
        resp = admin_client.get(rota)
        assert resp.status_code == 200, f'{rota} respondeu {resp.status_code}'
        assert 'href="/"' in resp.get_data(as_text=True), f'{rota} sem caminho de volta'


def test_sem_permissao_de_admin_o_menu_nao_mostra_a_porta(app, cliente_logado):
    c = cliente_logado(_usuario_sem_permissao())
    html = c.get('/glossario').get_data(as_text=True)
    assert 'fa-gear' not in html


# ---------------------------------------------------------------------------
# nenhuma tela depende de internet
# ---------------------------------------------------------------------------

def test_templates_nao_carregam_nada_de_fora():
    """Bootstrap, ícones e gráficos são servidos daqui. Um CDN externo deixa a
    tela sem estilo se a rede da planta cair — e é o tipo de coisa que a
    segurança da informação do cliente barra."""
    import glob
    import re
    raiz = os.path.join(os.path.dirname(__file__), '..', 'templates')
    externo = re.compile(r'''(src|href)=["']https?://''')
    achados = []
    for caminho in glob.glob(os.path.join(raiz, '**', '*.html'), recursive=True):
        with open(caminho, encoding='utf-8-sig') as fh:
            for n, linha in enumerate(fh, 1):
                if externo.search(linha):
                    achados.append(f'{os.path.basename(caminho)}:{n}')
    assert not achados, f'recurso externo em: {achados}'


def test_arquivos_estaticos_sao_servidos(client):
    for arquivo in ('vendor/bootstrap-5.3.0/css/bootstrap.min.css',
                    'vendor/bootstrap-5.3.0/js/bootstrap.bundle.min.js',
                    'vendor/fontawesome-6.4.0/css/all.min.css',
                    'vendor/fontawesome-6.4.0/webfonts/fa-solid-900.woff2',
                    'vendor/chartjs-4.4.0/chart.umd.min.js'):
        resp = client.get(f'/static/{arquivo}')
        assert resp.status_code == 200, f'{arquivo} não foi servido'
        resp.close()


def test_login_abre_sem_sessao(client):
    """O critério da L0, do jeito que o usuário vê: a tela de login responde e
    puxa o estilo local."""
    html = client.get('/login').get_data(as_text=True)
    assert 'MBAssets' in html
    assert '/static/vendor/bootstrap-5.3.0/css/bootstrap.min.css' in html


# ---------------------------------------------------------------------------
# o texto das telas não promete o que o produto não tem
# ---------------------------------------------------------------------------

def _texto(html):
    import re
    html = re.sub(r'<script.*?</script>|<style.*?</style>', '', html, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).lower()


_PROMESSAS_DO_QUE_SAIU = ('ativos de ti', 'manuten', 'fornecedor', 'lote',
                          'relatórios gerenciais', 'nota fiscal', 'licença')


def test_login_nao_anuncia_modulo_que_saiu(client):
    """🔴 Achado olhando a tela: a lateral do login vendia "Ativos de TI,
    coletores e manutenções" e "Gestão de manutenções e fornecedores". Os
    testes de fronteira procuram CÓDIGO; texto de vitrine passa por eles."""
    import re
    from app.helpers import t
    for idioma in ('pt', 'en', 'es'):
        texto = ' '.join(t(f'login.{k}', idioma) for k in
                         ('brand_sub', 'brand_headline', 'brand_desc',
                          'feature1', 'feature2', 'feature3'))
        # a sigla, em maiúsculas: "it" minúsculo é pronome em inglês
        assert not re.search(r'(TI|IT)', texto), f'{idioma}: sigla de TI no login'
        for palavra in ('maint', 'manuten', 'mantenim', 'supplier',
                        'fornecedor', 'proveedor'):
            assert palavra not in texto.lower(), f'{idioma}: "{palavra}" no texto do login'
    html = _texto(client.get('/login').get_data(as_text=True))
    for palavra in _PROMESSAS_DO_QUE_SAIU:
        assert palavra not in html, f'login fala em "{palavra}"'


def test_glossario_nao_ensina_o_que_nao_existe(admin_client):
    html = _texto(admin_client.get('/glossario').get_data(as_text=True))
    for palavra in _PROMESSAS_DO_QUE_SAIU:
        assert palavra not in html, f'glossário fala em "{palavra}"'
