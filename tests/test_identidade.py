"""
Testes do T2 — Identidade multi-empresa.

Cobrem: login por e-mail (substituindo RE como credencial), lockout de brute
force (contador + janela + bloqueio), fluxo "esqueci minha senha" (token
hasheado, expirável, uso único, envio nunca bloqueia), papel Owner e sessão
carregando empresa_id — incluindo o bloqueio de empresa suspensa.
"""
import re as re_mod
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from app.models import Usuario, Empresa, Grupo

ADMIN_EMAIL = 'admin@mbassets.local'
ADMIN_SENHA = 'admin123'


def _login(client, email, senha):
    return client.post('/login', data={'email': email, 'senha': senha})


def _criar_usuario(email='ana@empresa.com', senha='segredo1', re='4321', nome='Ana Analista'):
    """Usuário de tenant comum (grupo ANALISTA, empresa MB) apto a logar."""
    grupo = Grupo.query.filter_by(nome='ANALISTA').first()
    mb    = Empresa.query.filter_by(nome='Martin Brower').first()
    u = Usuario(nome=nome, re=re, email=email,
                senha_hash=generate_password_hash(senha, method='scrypt'),
                grupo_id=grupo.id, empresa_id=mb.id, nivel_acesso='GLOBAL')
    db.session.add(u)
    db.session.commit()
    return u


# ---------------------------------------------------------------------------
# Login por e-mail
# ---------------------------------------------------------------------------

def test_login_por_email_ok(client):
    resp = _login(client, ADMIN_EMAIL, ADMIN_SENHA)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/')


def test_sessao_carrega_empresa_id_e_owner(client):
    _login(client, ADMIN_EMAIL, ADMIN_SENHA)
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    with client.session_transaction() as s:
        assert s['empresa_id'] == mb.id
        assert s['is_owner'] is True


def test_login_email_case_insensitive_e_com_espacos(client):
    resp = _login(client, '  Admin@MBASSETS.Local  ', ADMIN_SENHA)
    assert resp.status_code == 302


def test_login_por_re_nao_funciona_mais(client):
    """RE deixou de ser credencial — virou só matrícula interna."""
    resp = client.post('/login', data={'email': 'admin', 'senha': ADMIN_SENHA})
    assert resp.status_code == 200
    assert 'Credenciais inv'.encode('utf-8') in resp.data
    with client.session_transaction() as s:
        assert 'user_id' not in s


def test_login_senha_errada_recusado(client):
    resp = _login(client, ADMIN_EMAIL, 'senha-errada')
    assert resp.status_code == 200
    assert 'Credenciais inv'.encode('utf-8') in resp.data


def test_usuario_sem_email_nao_tem_como_logar(client):
    """Transição: quem não tem e-mail (OPERADOR) simplesmente não loga."""
    grupo = Grupo.query.filter_by(nome='OPERADOR').first()
    db.session.add(Usuario(nome='Sem Email', re='7777', grupo_id=grupo.id,
                           senha_hash=generate_password_hash('x', method='scrypt')))
    db.session.commit()
    resp = client.post('/login', data={'email': '', 'senha': 'x'})
    assert resp.status_code == 200
    with client.session_transaction() as s:
        assert 'user_id' not in s


# ---------------------------------------------------------------------------
# Lockout de brute force
# ---------------------------------------------------------------------------

def test_lockout_bloqueia_apos_5_falhas(client):
    user = _criar_usuario()
    for _ in range(5):
        _login(client, user.email, 'errada')
    db.session.refresh(user)
    assert user.login_bloqueado_ate is not None
    # Mesmo com a senha CORRETA, conta bloqueada não entra.
    resp = _login(client, user.email, 'segredo1')
    assert resp.status_code == 200
    assert 'bloqueada'.encode('utf-8') in resp.data
    with client.session_transaction() as s:
        assert 'user_id' not in s


def test_lockout_expira_e_login_volta(client):
    user = _criar_usuario()
    for _ in range(5):
        _login(client, user.email, 'errada')
    user.login_bloqueado_ate = datetime.now() - timedelta(minutes=1)
    db.session.commit()
    resp = _login(client, user.email, 'segredo1')
    assert resp.status_code == 302
    db.session.refresh(user)
    assert user.login_tentativas == 0
    assert user.login_bloqueado_ate is None


def test_janela_temporal_zera_contador(client):
    """Falhas antigas (fora da janela de 15 min) não acumulam para o bloqueio."""
    user = _criar_usuario()
    for _ in range(4):
        _login(client, user.email, 'errada')
    user.login_ultima_tentativa = datetime.now() - timedelta(minutes=16)
    db.session.commit()
    _login(client, user.email, 'errada')
    db.session.refresh(user)
    assert user.login_tentativas == 1
    assert user.login_bloqueado_ate is None


# ---------------------------------------------------------------------------
# Empresa suspensa (Empresa.ativa=False)
# ---------------------------------------------------------------------------

def test_empresa_inativa_bloqueia_login(client):
    user = _criar_usuario()
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    mb.ativa = False
    db.session.commit()
    resp = _login(client, user.email, 'segredo1')
    assert resp.status_code == 200
    assert 'suspenso'.encode('utf-8') in resp.data
    with client.session_transaction() as s:
        assert 'user_id' not in s


def test_owner_ignora_empresa_inativa(client):
    """Owner é da plataforma — tenant suspenso não barra o dono do sistema."""
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    mb.ativa = False
    db.session.commit()
    resp = _login(client, ADMIN_EMAIL, ADMIN_SENHA)
    assert resp.status_code == 302


def test_empresa_inativa_derruba_sessao_ativa(client):
    user = _criar_usuario()
    assert _login(client, user.email, 'segredo1').status_code == 302
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    mb.ativa = False
    db.session.commit()
    resp = client.get('/')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']
    with client.session_transaction() as s:
        assert 'user_id' not in s


# ---------------------------------------------------------------------------
# Owner (seed)
# ---------------------------------------------------------------------------

def test_admin_de_bootstrap_e_owner_com_email(app):
    admin = Usuario.query.filter_by(re='admin').first()
    assert admin.is_owner is True
    assert admin.email == ADMIN_EMAIL


def test_seed_owner_idempotente(app):
    from app.startup import seed_owner
    seed_owner()
    seed_owner()
    assert Usuario.query.filter_by(is_owner=True).count() == 1


# ---------------------------------------------------------------------------
# Esqueci minha senha
# ---------------------------------------------------------------------------

def _solicitar_reset(client, monkeypatch, email):
    """POST /esqueci-senha capturando o e-mail 'enviado'; devolve o token do link."""
    enviados = []
    monkeypatch.setattr('app.routes.auth.enviar_email',
                        lambda dest, assunto, corpo: enviados.append(corpo) or True)
    resp = client.post('/esqueci-senha', data={'email': email})
    token = None
    if enviados:
        m = re_mod.search(r'/redefinir-senha/([A-Za-z0-9_\-]+)', enviados[0])
        token = m.group(1) if m else None
    return resp, token


def test_esqueci_senha_gera_token_hasheado(client, monkeypatch):
    user = _criar_usuario()
    resp, token = _solicitar_reset(client, monkeypatch, user.email)
    assert resp.status_code == 302
    assert token is not None
    db.session.refresh(user)
    assert user.reset_token_hash is not None
    assert user.reset_token_hash != token          # banco guarda o hash, nunca o token
    assert user.reset_token_expira > datetime.now()


def test_esqueci_senha_nao_revela_emails(client, monkeypatch):
    """Anti-enumeração: resposta idêntica exista ou não o e-mail."""
    user = _criar_usuario()
    r1, _ = _solicitar_reset(client, monkeypatch, user.email)
    r2, token2 = _solicitar_reset(client, monkeypatch, 'ninguem@nada.com')
    d1 = client.get(r1.headers['Location']).data
    assert r1.status_code == r2.status_code == 302
    assert token2 is None
    assert 'estiver cadastrado'.encode('utf-8') in d1


def test_falha_de_envio_nao_bloqueia_fluxo(client, monkeypatch):
    """Provedor de e-mail fora do ar não pode quebrar o fluxo do usuário."""
    user = _criar_usuario()

    def _explode(*a, **kw):
        raise RuntimeError('SMTP fora do ar')

    monkeypatch.setattr('app.routes.auth.enviar_email', _explode)
    resp = client.post('/esqueci-senha', data={'email': user.email})
    assert resp.status_code == 302
    db.session.refresh(user)
    assert user.reset_token_hash is not None       # token gravado mesmo sem envio


def test_reset_redefine_senha_e_desbloqueia(client, monkeypatch):
    user = _criar_usuario()
    for _ in range(5):                              # conta bloqueada por brute force
        _login(client, user.email, 'errada')
    _, token = _solicitar_reset(client, monkeypatch, user.email)

    resp = client.post(f'/redefinir-senha/{token}',
                       data={'senha_nova': 'NovaSenha9', 'senha_confirmacao': 'NovaSenha9'})
    assert resp.status_code == 302
    db.session.refresh(user)
    assert check_password_hash(user.senha_hash, 'NovaSenha9')
    assert user.reset_token_hash is None            # uso único: token consumido
    assert user.login_bloqueado_ate is None         # reset também desbloqueia
    assert _login(client, user.email, 'NovaSenha9').status_code == 302


def test_token_uso_unico(client, monkeypatch):
    user = _criar_usuario()
    _, token = _solicitar_reset(client, monkeypatch, user.email)
    client.post(f'/redefinir-senha/{token}',
                data={'senha_nova': 'NovaSenha9', 'senha_confirmacao': 'NovaSenha9'})
    resp = client.get(f'/redefinir-senha/{token}')
    assert resp.status_code == 302
    assert '/esqueci-senha' in resp.headers['Location']


def test_token_expirado_rejeitado(client, monkeypatch):
    user = _criar_usuario()
    _, token = _solicitar_reset(client, monkeypatch, user.email)
    user.reset_token_expira = datetime.now() - timedelta(minutes=1)
    db.session.commit()
    resp = client.post(f'/redefinir-senha/{token}',
                       data={'senha_nova': 'NovaSenha9', 'senha_confirmacao': 'NovaSenha9'})
    assert resp.status_code == 302
    assert '/esqueci-senha' in resp.headers['Location']
    db.session.refresh(user)
    assert not check_password_hash(user.senha_hash, 'NovaSenha9')


def test_reset_senha_curta_rejeitada(client, monkeypatch):
    user = _criar_usuario()
    _, token = _solicitar_reset(client, monkeypatch, user.email)
    resp = client.post(f'/redefinir-senha/{token}',
                       data={'senha_nova': 'abc', 'senha_confirmacao': 'abc'})
    assert resp.status_code == 200
    assert 'pelo menos 6'.encode('utf-8') in resp.data


# ---------------------------------------------------------------------------
# E-mail no CRUD de usuários (transição RE → e-mail)
# ---------------------------------------------------------------------------

def _form_usuario(**extras):
    grupo = Grupo.query.filter_by(nome='ANALISTA').first()
    base = {'nome': 'Novo Usuário', 're': '5555', 'senha': 'senha123',
            'grupo_id': grupo.id, 'nivel_acesso': 'GLOBAL'}
    base.update(extras)
    return base


def test_admin_cadastra_email_e_usuario_loga(admin_client, client):
    resp = admin_client.post('/usuarios', data=_form_usuario(email='novo@empresa.com'))
    assert resp.status_code == 302
    novo = Usuario.query.filter_by(re='5555').first()
    assert novo.email == 'novo@empresa.com'
    assert novo.empresa_id is not None              # nasce no tenant de quem criou
    assert _login(client, 'novo@empresa.com', 'senha123').status_code == 302


def test_email_duplicado_recusado_no_crud(admin_client):
    admin_client.post('/usuarios', data=_form_usuario(email='dup@empresa.com'))
    admin_client.post('/usuarios', data=_form_usuario(email='DUP@empresa.com', re='5556'))
    assert Usuario.query.filter_by(re='5556').first() is None


def test_email_invalido_recusado_no_crud(admin_client):
    admin_client.post('/usuarios', data=_form_usuario(email='sem-arroba'))
    assert Usuario.query.filter_by(re='5555').first() is None


def test_email_normalizado_no_save(admin_client):
    admin_client.post('/usuarios', data=_form_usuario(email='  MAIUSCULO@Empresa.COM  '))
    novo = Usuario.query.filter_by(re='5555').first()
    assert novo.email == 'maiusculo@empresa.com'


# ---------------------------------------------------------------------------
# A SENHA DO ADMIN DE BOOTSTRAP
# ---------------------------------------------------------------------------

def test_banco_novo_nao_nasce_com_senha_publicada(monkeypatch, capsys):
    """🔴 Senha fixa no código é a senha de toda instalação nova, e o usuário
    criado aqui tem is_owner=True: quem entra por ela enxerga tudo.

    A suíte define MBASSETS_ADMIN_SENHA no conftest para poder exercitar o login;
    aqui a variável sai de cena, que é a situação de quem instala.
    """
    from werkzeug.security import check_password_hash
    from app.helpers import _senha_do_admin

    monkeypatch.delenv('MBASSETS_ADMIN_SENHA', raising=False)

    senha = _senha_do_admin()

    assert senha != 'admin123'
    assert len(senha) >= 12, 'senha sorteada curta demais para valer de proteção'


def test_o_admin_criado_pelo_seed_recusa_admin123(monkeypatch):
    """O mesmo, pelo caminho real: sobe um banco novo e tenta a senha antiga.

    Vale mais que checar a função isolada — é o seed inteiro, do jeito que roda
    no primeiro boot de um tenant recém-provisionado.
    """
    from werkzeug.security import check_password_hash
    from app import create_app, db
    from app.startup import executar_startup
    from app.models import Usuario

    monkeypatch.delenv('MBASSETS_ADMIN_SENHA', raising=False)

    aplicacao = create_app()
    with aplicacao.app_context():
        executar_startup()
        admin = Usuario.query.filter_by(re='admin').first()
        assert admin is not None, 'o seed deixou de criar o admin'
        assert not check_password_hash(admin.senha_hash, 'admin123'),             'banco novo ainda nasce com a senha publicada no repositorio'
        db.session.remove()


def test_senha_sorteada_aparece_no_log(monkeypatch, capsys):
    """Sem isso o banco novo fica INACESSÍVEL — é a única via de entrada."""
    from app.helpers import _senha_do_admin

    monkeypatch.delenv('MBASSETS_ADMIN_SENHA', raising=False)
    senha = _senha_do_admin()

    assert senha in capsys.readouterr().out, 'quem provisiona não descobre a senha'


def test_duas_instalacoes_nao_compartilham_senha(monkeypatch):
    """Senha sorteada igual para todo mundo seria a mesma falha com outra cara."""
    from app.helpers import _senha_do_admin

    monkeypatch.delenv('MBASSETS_ADMIN_SENHA', raising=False)

    assert _senha_do_admin() != _senha_do_admin()


def test_ambiente_manda_quando_diz_a_senha(monkeypatch):
    """Quem provisiona pode escolher — é como a própria suíte se vira."""
    from app.helpers import _senha_do_admin

    monkeypatch.setenv('MBASSETS_ADMIN_SENHA', 'escolhida-por-quem-instala')

    assert _senha_do_admin() == 'escolhida-por-quem-instala'
