"""
Blueprint de autenticação do sistema.

Ciclo de vida do acesso: primeiro acesso (cria o administrador), login, troca
obrigatória de senha provisória e logout.

Identidade:
    A credencial de login é o E-MAIL (único); o RE é a matrícula interna.

Recuperação de senha:
    Não há servidor de e-mail na instalação, então não existe link "enviado por
    e-mail". A TI redefine a senha pela tela de usuários — a senha nova sai
    provisória e o dono troca no próximo login. Se o único TI esquecer a dele, o
    caminho é o comando `python manage.py redefinir-senha`, que exige acesso à
    máquina do servidor.

Nota sobre o modelo de permissões em sessão:
    As permissões do perfil são carregadas como lista de strings na sessão no
    momento do login. O before_request em app/__init__.py re-sincroniza esse
    cache a cada request.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import Usuario
from app import termos as _termos
from app.helpers import (registrar_log, normalizar_email, email_valido, login_required,
                         problema_na_senha, SENHA_MINIMA, instalacao_sem_usuario,
                         token_primeiro_acesso_confere, criar_administrador)

auth_bp = Blueprint('auth', __name__)

# ---------------------------------------------------------------------------
# Política de lockout (proteção de brute force).
# 5 falhas dentro de uma janela de 15 min bloqueiam a conta por 15 min. O
# bloqueio vale mesmo com a senha correta — senão o atacante só precisa acertar
# na 6ª tentativa.
# ---------------------------------------------------------------------------
MAX_TENTATIVAS_LOGIN  = 5
JANELA_TENTATIVAS_MIN = 15
BLOQUEIO_LOGIN_MIN    = 15

# Hash de sacrifício conferido quando o e-mail não existe: iguala o tempo de
# resposta ao de uma senha errada em conta real, fechando o timing-oracle que
# permitiria enumerar quais e-mails têm cadastro.
_DUMMY_HASH = generate_password_hash('lastro-timing-equalizer', method='scrypt')


def _buscar_por_email(email: str):
    """Localiza usuário pelo e-mail normalizado, tolerando maiúsculas legadas."""
    if not email:
        return None
    return Usuario.query.filter(func.lower(Usuario.email) == email).first()


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Autentica o usuário por E-MAIL e senha.

    Ordem das verificações (segurança antes de conveniência):
      1. e-mail existe? (hash dummy se não — anti timing-oracle, msg genérica)
      2. conta bloqueada por lockout? (vale mesmo com senha correta)
      3. senha confere? (falha alimenta o contador da janela de lockout)

    Senha provisória entra, mas só enxerga a tela de troca (ver o hook
    _exigir_troca_de_senha em app/__init__.py).
    """
    # Instalação sem nenhum usuário: o login não tem quem autenticar. A tela diz
    # onde está o caminho (o endereço do primeiro acesso, no log do servidor) em
    # vez de deixar a TI tentando senhas que não existem.
    if instalacao_sem_usuario():
        return render_template('login.html', sem_usuario=True)

    if request.method == 'POST':
        email = normalizar_email(request.form.get('email'))
        senha = request.form.get('senha', '')
        user  = _buscar_por_email(email)

        if not user:
            check_password_hash(_DUMMY_HASH, senha)  # iguala o tempo de resposta
            flash('Credenciais inválidas.', 'danger')
            return render_template('login.html')

        agora = datetime.now()

        # Bloqueio vigente — recusa antes de sequer conferir a senha.
        if user.login_bloqueado_ate and agora < user.login_bloqueado_ate:
            registrar_log('LOGIN_BLOQUEADO',
                          f'Tentativa de login em conta bloqueada ({user.re}/{email}).')
            flash('Conta temporariamente bloqueada por excesso de tentativas. '
                  'Tente novamente em alguns minutos.', 'danger')
            return render_template('login.html')

        # Bloqueio antigo expirado ou janela de tentativas vencida → contador zera.
        if user.login_bloqueado_ate and agora >= user.login_bloqueado_ate:
            user.login_bloqueado_ate = None
            user.login_tentativas    = 0
        if (user.login_ultima_tentativa and
                agora - user.login_ultima_tentativa > timedelta(minutes=JANELA_TENTATIVAS_MIN)):
            user.login_tentativas = 0

        if not check_password_hash(user.senha_hash, senha):
            user.login_tentativas       += 1
            user.login_ultima_tentativa  = agora
            if user.login_tentativas >= MAX_TENTATIVAS_LOGIN:
                user.login_bloqueado_ate = agora + timedelta(minutes=BLOQUEIO_LOGIN_MIN)
                user.login_tentativas    = 0
                registrar_log('LOGIN_LOCKOUT',
                              f'Conta {user.re} ({email}) bloqueada por '
                              f'{BLOQUEIO_LOGIN_MIN} min após {MAX_TENTATIVAS_LOGIN} falhas.')
            db.session.commit()
            flash('Credenciais inválidas.', 'danger')
            return render_template('login.html')

        # Sucesso: lockout zerado e sessão montada.
        user.login_tentativas       = 0
        user.login_ultima_tentativa = None
        user.login_bloqueado_ate    = None
        db.session.commit()

        permissoes = []
        grupo_nome = None
        if user.grupo:
            grupo_nome = user.grupo.nome
            permissoes = [p.codigo for p in user.grupo.permissoes]

        session.clear()
        session['user_id']    = user.id
        session['nome']       = user.nome
        session['re']         = user.re
        session['grupo']      = grupo_nome
        session['permissoes'] = permissoes
        session['empresa_id'] = user.empresa_id
        session['senha_provisoria'] = bool(user.senha_provisoria)
        # UX Tokens — preferências visuais na sessão (usadas no anti-flash e no acento)
        session['tema']       = user.tema or 'light'
        session['cor_acento'] = user.cor_acento
        registrar_log('LOGIN', f'Acesso de {user.nome} (perfil: {grupo_nome})')
        if user.senha_provisoria:
            return redirect(url_for('auth.trocar_senha'))
        return redirect(url_for('dashboard.index'))
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    """Encerra a sessão limpando todos os dados e redireciona para o login."""
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/esqueci-senha')
def esqueci_senha():
    """Explica o caminho que existe: pedir à TI. Ver a docstring do módulo."""
    return render_template('esqueci_senha.html')


@auth_bp.route('/trocar-senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    """Troca da senha provisória — a única tela aberta enquanto ela existir.

    Não pede a senha atual: quem chegou aqui acabou de autenticar com ela, e a
    pergunta que importa ("é você?") é respondida por escolher uma senha que só
    o dono sabe.
    """
    user = db.session.get(Usuario, session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))
    if not user.senha_provisoria:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        senha_nova = request.form.get('senha_nova', '')
        problema = problema_na_senha(senha_nova, request.form.get('senha_confirmacao', ''))
        if problema:
            flash(problema, 'danger')
            return render_template('trocar_senha.html', senha_minima=SENHA_MINIMA)
        if check_password_hash(user.senha_hash, senha_nova):
            flash('A senha nova precisa ser diferente da provisória.', 'danger')
            return render_template('trocar_senha.html', senha_minima=SENHA_MINIMA)

        user.senha_hash       = generate_password_hash(senha_nova, method='scrypt')
        user.senha_provisoria = False
        db.session.commit()
        session['senha_provisoria'] = False
        registrar_log('SENHA_TROCADA', f'{user.re} trocou a senha provisória.')
        flash('Senha definida. Bem-vindo!', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('trocar_senha.html', senha_minima=SENHA_MINIMA)


@auth_bp.route('/primeiro-acesso/<token>', methods=['GET', 'POST'])
def primeiro_acesso(token):
    """Cria o administrador da instalação. Só existe enquanto não há usuário.

    Token errado e instalação que já tem usuário respondem igual — 404 —, para a
    rota não confirmar que existe a quem só está varrendo endereços.
    """
    if not token_primeiro_acesso_confere(token):
        abort(404)

    if request.method == 'POST':
        nome  = request.form.get('nome', '').strip()
        email = normalizar_email(request.form.get('email'))
        senha = request.form.get('senha', '')
        problema = None
        if not nome:
            problema = 'Informe o nome.'
        elif not email_valido(email):
            problema = 'Informe um e-mail válido — é com ele que você vai entrar.'
        else:
            problema = problema_na_senha(senha, request.form.get('senha_confirmacao', ''))
        if problema:
            flash(problema, 'danger')
            return render_template('primeiro_acesso.html', token=token,
                                   senha_minima=SENHA_MINIMA, t_mod=_termos)

        # 🔴 O servidor não acredita na tela: o botão do modal só acende
        # depois de rolar, mas um POST montado à mão pularia isso. Sem aceite,
        # não há administrador — e o sistema continua sem ninguém dentro.
        if request.form.get('aceito') != 'sim':
            flash('É preciso aceitar os termos de uso para criar o administrador.',
                  'warning')
            return render_template('primeiro_acesso.html', token=token,
                                   senha_minima=SENHA_MINIMA, t_mod=_termos)

        admin = criar_administrador(nome, email, senha,
                                    nome_empresa=request.form.get('empresa'))
        # Mesma transação da criação: se fossem dois passos, uma queda entre
        # eles deixaria o administrador existindo sem aceite nenhum.
        _termos.registrar_aceite(admin)
        session.clear()
        session['nome'] = admin.nome
        session['re']   = admin.re
        session['empresa_id'] = admin.empresa_id
        registrar_log('PRIMEIRO_ACESSO',
                      f'Administrador {admin.nome} ({email}) criado. '
                      f'Termos de uso versao {_termos.VERSAO} aceitos.')
        session.clear()
        flash('Administrador criado. Entre com o e-mail e a senha que você escolheu.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('primeiro_acesso.html', token=token,
                           senha_minima=SENHA_MINIMA, t_mod=_termos)
