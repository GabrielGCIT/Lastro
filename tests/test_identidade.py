"""
Testes de identidade: login por e-mail, lockout de brute force e o e-mail no
cadastro de usuários. O primeiro acesso, a senha provisória e os perfis têm
arquivo próprio (test_l1_acesso.py).
"""
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app import db
from app.models import Usuario, Empresa, Grupo

ADMIN_EMAIL = 'admin@lastro.local'
ADMIN_SENHA = 'admin123'


def _login(client, email, senha):
    return client.post('/login', data={'email': email, 'senha': senha})


def _criar_usuario(email='ana@empresa.com', senha='segredo12', re='4321', nome='Ana Balcão'):
    """Usuário do perfil Balcão, com senha própria (não provisória)."""
    grupo = Grupo.query.filter_by(nome='BALCAO').first()
    empresa = Empresa.query.first()
    u = Usuario(nome=nome, re=re, email=email,
                senha_hash=generate_password_hash(senha, method='scrypt'),
                grupo_id=grupo.id, empresa_id=empresa.id, nivel_acesso='GLOBAL')
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


def test_sessao_carrega_empresa_id(client):
    _login(client, ADMIN_EMAIL, ADMIN_SENHA)
    empresa = Empresa.query.first()
    with client.session_transaction() as s:
        assert s['empresa_id'] == empresa.id
        assert 'is_owner' not in s


def test_login_email_case_insensitive_e_com_espacos(client):
    resp = _login(client, '  Admin@LASTRO.Local  ', ADMIN_SENHA)
    assert resp.status_code == 302


def test_login_por_re_nao_funciona(client):
    """RE é a matrícula interna, não credencial."""
    resp = client.post('/login', data={'email': 'admin', 'senha': ADMIN_SENHA})
    assert resp.status_code == 200
    assert 'Credenciais inv'.encode('utf-8') in resp.data
    with client.session_transaction() as s:
        assert 'user_id' not in s


def test_login_senha_errada_recusado(client):
    resp = _login(client, ADMIN_EMAIL, 'senha-errada')
    assert resp.status_code == 200
    assert 'Credenciais inv'.encode('utf-8') in resp.data


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
    resp = _login(client, user.email, 'segredo12')
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
    resp = _login(client, user.email, 'segredo12')
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
# E-mail no cadastro de usuários
# ---------------------------------------------------------------------------

def _form_usuario(**extras):
    grupo = Grupo.query.filter_by(nome='BALCAO').first()
    base = {'nome': 'Novo Usuário', 're': '5555', 'senha': 'senha123',
            'grupo_id': grupo.id, 'nivel_acesso': 'GLOBAL',
            'email': 'novo@empresa.com'}
    base.update(extras)
    return base


def test_admin_cadastra_email_e_usuario_entra(admin_client, client):
    resp = admin_client.post('/usuarios', data=_form_usuario())
    assert resp.status_code == 302
    novo = Usuario.query.filter_by(re='5555').first()
    assert novo.email == 'novo@empresa.com'
    assert novo.empresa_id is not None              # nasce na empresa de quem criou
    assert _login(client, 'novo@empresa.com', 'senha123').status_code == 302


def test_email_e_obrigatorio(admin_client):
    """Todo perfil entra no sistema — e entra pelo e-mail."""
    resp = admin_client.post('/usuarios', data=_form_usuario(email=''), follow_redirects=True)
    assert Usuario.query.filter_by(re='5555').first() is None
    assert 'Informe o e-mail' in resp.get_data(as_text=True)


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
