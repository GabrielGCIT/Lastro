"""
Blueprint de autenticação do sistema.

Responsável pelo ciclo de vida da sessão do usuário: login, logout, recuperação
de senha ("esqueci minha senha") e um utilitário de bootstrapping para
recuperação de acesso administrativo.

T2 — Identidade multi-empresa:
    A credencial de login é o E-MAIL (único global, app-level); o RE virou
    matrícula interna, única por empresa. A sessão carrega empresa_id e
    is_owner, e o login recusa usuários de empresa suspensa (Empresa.ativa=False)
    — exceto o Owner da plataforma.

Nota sobre o modelo de permissões em sessão:
    As permissões do grupo são carregadas como lista de strings na sessão no
    momento do login. Isso é um cache intencional — evita uma query extra por
    request. O before_request em app/__init__.py re-sincroniza esse cache a
    cada request, garantindo que remoção de permissões no banco seja refletida
    sem precisar forçar re-login.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import Usuario
from app.helpers import registrar_log, normalizar_email
from app.mailer import enviar_email

auth_bp = Blueprint('auth', __name__)

# ---------------------------------------------------------------------------
# Política de lockout (proteção de brute force) e de token de reset.
# Valores conservadores de mercado: 5 falhas dentro de uma janela de 15 min
# bloqueiam a conta por 15 min. O bloqueio vale mesmo com a senha correta —
# senão o atacante só precisa acertar na 6ª tentativa.
# ---------------------------------------------------------------------------
MAX_TENTATIVAS_LOGIN      = 5
JANELA_TENTATIVAS_MIN     = 15
BLOQUEIO_LOGIN_MIN        = 15
RESET_TOKEN_VALIDADE_MIN  = 60

# Hash de sacrifício conferido quando o e-mail não existe: iguala o tempo de
# resposta ao de uma senha errada em conta real, fechando o timing-oracle que
# permitiria enumerar quais e-mails têm cadastro.
_DUMMY_HASH = generate_password_hash('mbassets-timing-equalizer', method='scrypt')


def _hash_token(token: str) -> str:
    """sha256 hex do token de reset — só o hash toca o banco, nunca o token."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def gerar_token_reset(user) -> str:
    """Gera e grava (no objeto, SEM commit) um token de reset; devolve-o em claro.

    Compartilhado entre 'esqueci-senha' (T2) e o onboarding do T4b — o admin de
    uma empresa nova nasce com hash inutilizável e recebe a senha por este mesmo
    link de redefinição. Só o sha256 toca o banco (uso único, validade de
    RESET_TOKEN_VALIDADE_MIN); o caller decide se envia por e-mail ou exibe o
    link na tela, e é quem faz o commit.
    """
    token = secrets.token_urlsafe(32)
    user.reset_token_hash   = _hash_token(token)
    user.reset_token_expira = datetime.now() + timedelta(minutes=RESET_TOKEN_VALIDADE_MIN)
    return token


def _buscar_por_email(email: str):
    """Localiza usuário pelo e-mail normalizado, tolerando maiúsculas legadas."""
    if not email:
        return None
    return Usuario.query.filter(func.lower(Usuario.email) == email).first()


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Autentica o usuário por E-MAIL e senha (T2 — antes era por RE).

    Ordem das verificações (segurança antes de conveniência):
      1. e-mail existe? (hash dummy se não — anti timing-oracle, msg genérica)
      2. conta bloqueada por lockout? (vale mesmo com senha correta)
      3. senha confere? (falha alimenta o contador da janela de lockout)
      4. empresa ativa? (tenant suspenso não entra — Owner ignora o bloqueio)

    No login bem-sucedido, carrega na sessão: user_id, nome, RE, empresa_id,
    is_owner, nome do grupo e lista de códigos de permissão. Essa carga em
    bloco é intencional — o before_request cuida de manter esse cache
    sincronizado com o banco a cada request.
    """
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

        # Senha correta — mas empresa suspensa não entra (T1 previu o bloqueio
        # aqui). O Owner fica acima das empresas e nunca é barrado por tenant.
        if user.empresa and not user.empresa.ativa and not user.is_owner:
            registrar_log('LOGIN_EMPRESA_INATIVA',
                          f'Login recusado: empresa {user.empresa.nome} suspensa ({email}).')
            flash('O acesso da sua empresa está suspenso. Contate o suporte.', 'danger')
            return render_template('login.html')

        # Sucesso: lockout zerado e sessão montada.
        user.login_tentativas       = 0
        user.login_ultima_tentativa = None
        user.login_bloqueado_ate    = None
        db.session.commit()

        # Usuário pode existir sem grupo atribuído — trata os dois campos
        # como opcionais e inicializa como vazio para não quebrar
        # verificações de permissão downstream.
        permissoes = []
        grupo_nome = None
        if user.grupo:
            grupo_nome = user.grupo.nome
            permissoes = [p.codigo for p in user.grupo.permissoes]

        # T4c — impersonation NÃO sobrevive a um novo login: sem este pop, um
        # Owner que re-autentica sem passar pelo logout (sessão velha no mesmo
        # navegador) retomaria silenciosamente a empresa impersonada anterior —
        # a chave é própria e nenhum outro hook a reescreve.
        session.pop('empresa_impersonada', None)
        session['user_id']    = user.id
        session['nome']       = user.nome
        session['re']         = user.re
        session['grupo']      = grupo_nome
        session['permissoes'] = permissoes
        # T2 — tenant e papel de plataforma na sessão (re-sincronizados a cada
        # request pelo _sync_permissoes, como os demais campos).
        session['empresa_id'] = user.empresa_id
        session['is_owner']   = bool(user.is_owner)
        # UX Tokens — preferências visuais na sessão (usadas no anti-flash e no acento)
        session['tema']       = user.tema or 'light'
        session['cor_acento'] = user.cor_acento
        registrar_log('LOGIN', f'Acesso de {user.nome} (grupo: {grupo_nome})')
        return redirect(url_for('dashboard.index'))
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    """Encerra a sessão limpando todos os dados e redireciona para o login."""
    session.clear()
    return redirect(url_for('auth.login'))


# ---------------------------------------------------------------------------
# ESQUECI MINHA SENHA (T2)
# ---------------------------------------------------------------------------

@auth_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    """
    Solicita o link de redefinição de senha por e-mail.

    Anti-enumeração: a resposta é IDÊNTICA exista ou não o e-mail — quem
    pergunta não descobre quais endereços têm cadastro. O token vai em claro
    apenas no e-mail; o banco guarda só o sha256 com validade de
    RESET_TOKEN_VALIDADE_MIN minutos, uso único.

    Falha de envio NUNCA bloqueia o fluxo (o mailer engole exceções e o
    try/except aqui é o cinto extra): o token fica válido no banco e o
    usuário pode solicitar de novo.
    """
    if request.method == 'POST':
        email = normalizar_email(request.form.get('email'))
        user  = _buscar_por_email(email)
        if user:
            token = gerar_token_reset(user)
            db.session.commit()

            link = url_for('auth.redefinir_senha', token=token, _external=True)
            corpo = (
                f'Olá, {user.nome}.\n\n'
                f'Recebemos um pedido de redefinição de senha da sua conta no MBAssets.\n'
                f'Acesse o link abaixo para definir uma nova senha '
                f'(válido por {RESET_TOKEN_VALIDADE_MIN} minutos, uso único):\n\n'
                f'{link}\n\n'
                f'Se você não fez esse pedido, ignore este e-mail — sua senha continua a mesma.'
            )
            try:
                enviar_email(email, 'MBAssets — Redefinição de senha', corpo)
            except Exception as exc:
                # O mailer já não propaga, mas o fluxo do usuário jamais pode
                # quebrar por causa do provedor de e-mail.
                print(f'[MAIL ERROR] Reset de senha ({email}): {exc}')
            registrar_log('SENHA_RESET_SOLICITADO',
                          f'Token de reset gerado para {user.re}.')
        flash('Se o e-mail estiver cadastrado, você receberá as instruções '
              'de redefinição em instantes.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('esqueci_senha.html')


@auth_bp.route('/redefinir-senha/<token>', methods=['GET', 'POST'])
def redefinir_senha(token):
    """
    Define a nova senha a partir do token recebido por e-mail.

    O token é válido se o sha256 dele bater com reset_token_hash de algum
    usuário E a expiração não tiver passado. Ao redefinir: token consumido
    (campos limpos — uso único) e lockout zerado, senão o dono legítimo
    recuperaria a senha mas continuaria bloqueado pelo brute force alheio.
    """
    user = Usuario.query.filter_by(reset_token_hash=_hash_token(token)).first()
    if not user or not user.reset_token_expira or datetime.now() > user.reset_token_expira:
        flash('Link de redefinição inválido ou expirado. Solicite um novo.', 'danger')
        return redirect(url_for('auth.esqueci_senha'))

    if request.method == 'POST':
        senha_nova = request.form.get('senha_nova', '')
        senha_conf = request.form.get('senha_confirmacao', '')
        # Mesma política mínima da troca de senha do perfil (endurecimento de
        # política de senha é item do Bloco 2 — muda nos dois lugares juntos).
        if len(senha_nova) < 6:
            flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
            return render_template('redefinir_senha.html', token=token)
        if senha_nova != senha_conf:
            flash('As senhas não coincidem.', 'danger')
            return render_template('redefinir_senha.html', token=token)

        user.senha_hash          = generate_password_hash(senha_nova, method='scrypt')
        user.reset_token_hash    = None
        user.reset_token_expira  = None
        user.login_tentativas    = 0
        user.login_bloqueado_ate = None
        db.session.commit()
        registrar_log('SENHA_RESET', f'Senha redefinida via token por {user.re}.')
        flash('Senha redefinida com sucesso. Faça login com a nova senha.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('redefinir_senha.html', token=token)


@auth_bp.route('/fix-admin')
def fix_admin():
    """
    Utilitário de emergência para recuperação de acesso administrativo.

    Força o vínculo do usuário 'admin' ao grupo TI_MASTER e atualiza a sessão
    corrente sem precisar de re-login. Útil quando o admin perde o grupo por
    uma migration mal aplicada ou exclusão acidental.

    Segurança:
        - Desativado fora do modo debug (FLASK_DEBUG=1) — não exposto em produção.
        - Só funciona se a sessão atual pertence ao RE 'admin' — qualquer
          outra tentativa é bloqueada com 403 e registrada como alerta de
          segurança no log de auditoria.
        - Toda execução bem-sucedida também é registrada para rastreabilidade.

    Imports locais são intencionais: evitam import circular com o módulo app
    durante a inicialização da aplicação.
    """
    import os
    from app.models import Grupo

    # Bloqueio de produção — endpoint só disponível em ambiente de desenvolvimento.
    if not os.environ.get('FLASK_DEBUG', '0') == '1':
        registrar_log('SECURITY_ALERT', f'Acesso a /fix-admin bloqueado (produção) por RE:{session.get("re", "anonimo")}.')
        return "Não disponível.", 404

    if session.get('re') != 'admin':
        # Registra a tentativa antes de recusar — útil para identificar
        # varredura de endpoints sensíveis.
        registrar_log('SECURITY_ALERT', f'Acesso não autorizado a /fix-admin por RE:{session.get("re", "anonimo")}.')
        return "Acesso negado.", 403

    admin = Usuario.query.filter_by(re='admin').first()
    grupo = Grupo.query.filter_by(nome='TI_MASTER').first()
    if admin and grupo:
        admin.grupo_id = grupo.id
        db.session.commit()
        # Atualiza também a sessão ativa para evitar que o admin precise
        # fazer logout/login para enxergar as novas permissões.
        session['grupo']      = grupo.nome
        session['permissoes'] = [p.codigo for p in grupo.permissoes]
        registrar_log('FIX_ADMIN', 'Grupo TI_MASTER forçado no admin via /fix-admin.')
        return "Admin atualizado para TI_MASTER."
    return "Admin ou grupo TI_MASTER não encontrado.", 404
