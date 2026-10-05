"""
Fixtures da suíte de testes.

IMPORTANTE: as variáveis de ambiente são definidas ANTES de importar `app` —
load_dotenv() não sobrescreve variáveis já existentes, então isso garante que
os testes NUNCA tocam o banco de uma instalação: rodam sempre em SQLite em
memória.
"""
import os

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['SECRET_KEY']   = 'test-secret'

# O seed sorteia a senha do admin quando ninguém diz qual é. A suíte precisa de
# uma senha CONHECIDA para exercitar o login, então declara a dela aqui — em vez
# de a senha de produção ficar fixa no código para servir aos testes.
os.environ.setdefault('MBASSETS_ADMIN_SENHA', 'admin123')

import pytest  # noqa: E402

from app import create_app, db          # noqa: E402
from app.startup import executar_startup  # noqa: E402


@pytest.fixture()
def app():
    """App Flask com banco SQLite em memória, schema e seeds completos.

    Function-scoped: cada teste recebe um banco zerado — isolamento total
    ao custo de ~0,5s de startup por teste (aceitável para o volume atual).
    """
    application = create_app()
    application.config['TESTING'] = True
    application.config['WTF_CSRF_ENABLED'] = False
    with application.app_context():
        executar_startup()
        yield application
        db.session.remove()


@pytest.fixture()
def client(app):
    """Cliente HTTP anônimo (sem sessão)."""
    return app.test_client()


@pytest.fixture()
def admin_client(app):
    """Cliente autenticado como admin (grupo TI_MASTER — todas as permissões).

    Só user_id/nome/re entram na sessão: o hook _sync_permissoes preenche
    grupo e permissões a partir do banco a cada request, como em produção.
    """
    from app.models import Usuario
    admin = Usuario.query.filter_by(re='admin').first()
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = admin.id
        s['nome']    = 'Admin'
        s['re']      = 'admin'
    return c


@pytest.fixture()
def loc(app):
    """Localidade padrão, ancorada na empresa da instalação (T3).

    empresa_id explícito para que o recorte por tenant (get_filtro_localidade)
    enxergue esta localidade como da empresa — o backfill do seed só roda no
    boot, antes desta criação.
    """
    from app.models import Localidade, Empresa
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    localidade = Localidade(sigla='GR', nome='Guarulhos',
                            empresa_id=mb.id if mb else None)
    db.session.add(localidade)
    db.session.commit()
    return localidade


@pytest.fixture()
def cliente_logado(app):
    """Fábrica de test_client com a sessão montada como o login (T2) faz.

    Para rotas não-/api/ o _sync_permissoes re-sincroniza do banco; os campos de
    tenant abaixo cobrem também as rotas sob /api/ (que o hook ignora). Uso:
    `c = cliente_logado(du.userA)`.
    """
    def _make(user):
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id']      = user.id
            s['nome']         = user.nome
            s['re']           = user.re
            s['empresa_id']   = user.empresa_id
            s['is_owner']     = bool(user.is_owner)
            s['nivel_acesso'] = user.nivel_acesso
        return c
    return _make


@pytest.fixture()
def duas_empresas(app):
    """Cenário canônico de isolamento multi-empresa — MB (A) e Acme (B).

    O `empresa_id` continua em todas as leituras, então o isolamento entre
    empresas segue sendo testado mesmo com uma empresa só em produção: é ele que
    prova que o filtro de leitura não vaza. Duas empresas com dados espelhados
    (localidade, usuário GLOBAL não-owner, colaborador) mais o Owner de
    bootstrap.
    """
    from types import SimpleNamespace
    from werkzeug.security import generate_password_hash
    from app.models import Empresa, Localidade, Usuario, Grupo, Colaborador

    mb   = Empresa.query.filter_by(nome='Martin Brower').first()
    acme = Empresa(nome='Acme Logística', ativa=True)
    db.session.add(acme)
    db.session.flush()

    # TI_MASTER carrega todas as permissões — o isolamento aqui é por tenant
    # (empresa_id/localidade), não por RBAC; reusar o grupo evita montar um
    # segundo conjunto de permissões só para o teste.
    ti = Grupo.query.filter_by(nome='TI_MASTER').first()

    locA = Localidade(sigla='AAA', nome='CD A', empresa_id=mb.id)
    locB = Localidade(sigla='BBB', nome='CD B', empresa_id=acme.id)
    db.session.add_all([locA, locB])
    db.session.flush()

    def _user(nome, re, email, empresa):
        u = Usuario(nome=nome, re=re, email=email,
                    senha_hash=generate_password_hash('x', method='scrypt'),
                    grupo_id=ti.id, empresa_id=empresa.id, nivel_acesso='GLOBAL')
        db.session.add(u)
        return u

    userA = _user('User A', '7001', 'a@a.com', mb)
    userB = _user('User B', '7002', 'b@b.com', acme)
    db.session.flush()
    userA.foto_perfil = 'iso_perfil_a.png'
    userB.foto_perfil = 'iso_perfil_b.png'

    colA  = Colaborador(re='C001', nome='Colab A', empresa_id=mb.id)
    colB  = Colaborador(re='C002', nome='Colab B', empresa_id=acme.id)
    db.session.add_all([colA, colB])
    db.session.commit()

    admin = Usuario.query.filter_by(re='admin').first()  # Owner de bootstrap
    return SimpleNamespace(mb=mb, acme=acme, locA=locA, locB=locB,
                           userA=userA, userB=userB, admin=admin,
                           colA=colA, colB=colB)
