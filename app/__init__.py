import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv

load_dotenv()

# --- INSTÂNCIA GLOBAL DO BANCO ---
# Definida aqui no nível de módulo — e não dentro de create_app() — para que
# models.py e qualquer módulo em routes/ possam fazer `from app import db`
# sem circular import. O bind com o app acontece em create_app() via db.init_app(),
# seguindo o padrão Application Factory do Flask.
db = SQLAlchemy()

# --- CSRF PROTECTION ---
# Instância global — mesma razão que db: importada por routes que precisam
# excluir endpoints específicos via @csrf.exempt (ex: webhooks futuros).
csrf = CSRFProtect()

# --- CAMINHOS ---
_APP_DIR      = os.path.abspath(os.path.dirname(__file__))  # app/
ROOT_DIR      = os.path.abspath(os.path.join(_APP_DIR, '..'))  # raiz do projeto
UPLOAD_FOLDER      = os.path.join(ROOT_DIR, 'uploads')
FOTO_FOLDER        = os.path.join(ROOT_DIR, 'uploads', 'fotos')
CRACHA_FOLDER      = os.path.join(ROOT_DIR, 'uploads', 'crachas')
INSTANCE_FOLDER    = os.path.join(ROOT_DIR, 'instance')

ALLOWED_EXTENSIONS     = {'pdf'}
ALLOWED_IMG_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}

# Garante que os diretórios de upload e o instance/ existam antes do primeiro request.
# exist_ok=True evita erro se já existirem — seguro rodar sempre.
for _folder in [UPLOAD_FOLDER, FOTO_FOLDER, CRACHA_FOLDER, INSTANCE_FOLDER]:
    os.makedirs(_folder, exist_ok=True)


# --- BANCO PADRÃO ---
# Um arquivo dentro de instance/: sem serviço para subir antes do app e sem
# senha. O backup do sistema é a cópia deste arquivo.
BANCO_PADRAO = 'sqlite:///' + os.path.join(INSTANCE_FOLDER, 'mbassets.db').replace('\\', '/')
SECRET_KEY_ARQUIVO = os.path.join(INSTANCE_FOLDER, 'secret_key')


def _secret_key():
    """SECRET_KEY do ambiente, ou a guardada em instance/, ou uma nova guardada lá.

    Gerar uma chave nova a cada boot derrubaria a sessão de todo mundo sempre que
    o serviço reiniciasse. Persistida no instance/, ela sobrevive ao restart e
    nunca vai para o repositório.
    """
    do_ambiente = os.environ.get('SECRET_KEY')
    if do_ambiente:
        return do_ambiente
    if os.path.exists(SECRET_KEY_ARQUIVO):
        with open(SECRET_KEY_ARQUIVO, encoding='utf-8') as fh:
            guardada = fh.read().strip()
        if guardada:
            return guardada
    import secrets
    nova = secrets.token_hex(32)
    with open(SECRET_KEY_ARQUIVO, 'w', encoding='utf-8') as fh:
        fh.write(nova)
    return nova


def _configurar_sqlite(app):
    """Pragmas por conexão: o SQLite não guarda nenhum deles no arquivo.

    foreign_keys — sem ele o SQLite aceita FK apontando para o nada.
    journal_mode=WAL — leitura não bloqueia escrita: o balcão grava enquanto o
        dashboard lê.
    busy_timeout — dois postos gravando ao mesmo tempo esperam a vez em vez de
        receber "database is locked" na cara.
    """
    from sqlalchemy import event

    with app.app_context():
        engine = db.engine
    if engine.dialect.name != 'sqlite':
        return

    @event.listens_for(engine, 'connect')
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA foreign_keys=ON')
        if engine.url.database not in (None, '', ':memory:'):
            cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()


# --- FACTORY ---
def create_app(database_url=None):
    """Constrói e configura a aplicação Flask.

    Segue o padrão Application Factory, o que facilita testes e múltiplas
    instâncias com configurações diferentes. Registra blueprints, hooks de
    request, context processors e handlers de erro.

    Sem nenhuma variável de ambiente o app sobe sozinho: banco em
    instance/mbassets.db e SECRET_KEY guardada em instance/secret_key.
    """
    app = Flask(
        __name__,
        template_folder=os.path.join(ROOT_DIR, 'templates'),
        static_folder=os.path.join(ROOT_DIR, 'static'),
    )

    app.secret_key = _secret_key()
    app.config['UPLOAD_FOLDER']              = UPLOAD_FOLDER
    # MAX_CONTENT_LENGTH é a defesa na camada do Flask contra uploads abusivos.
    # O Werkzeug rejeita a requisição antes mesmo de ela chegar à view — 413 automático.
    app.config['MAX_CONTENT_LENGTH']         = 10 * 1024 * 1024  # 10 MB
    app.config['SQLALCHEMY_DATABASE_URI'] = (database_url or os.environ.get('DATABASE_URL')
                                             or BANCO_PADRAO)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # 28800s = 8h (duração de um turno) — token CSRF expira ao fim do turno.
    # Operadores em terminais compartilhados não deixam token válido indefinidamente.
    # O token também é invalidado imediatamente no logout (sessão Flask limpa).
    app.config['WTF_CSRF_TIME_LIMIT'] = 28800

    # --- HARDENING DE COOKIES DE SESSÃO ---
    # SESSION_COOKIE_SECURE só liga com SESSION_COOKIE_SECURE=1, quando o sistema
    # estiver atrás de HTTPS. Sem a env fica False, senão o cookie nunca seria
    # enviado sobre http:// e o login quebraria. SAMESITE='Lax' mitiga CSRF em
    # navegação cross-site sem quebrar redirects de login normais.
    app.config['SESSION_COOKIE_SECURE']   = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True

    db.init_app(app)
    csrf.init_app(app)
    _configurar_sqlite(app)

    # Injeta constantes de domínio em todos os templates sem precisar passá-las
    # manualmente em cada render_template(). As constantes vivem em models.py
    # porque são dados de domínio ligados ao fluxo de negócio — não configuração.
    from app.models import DIAGNOSTICO_MAPA

    # has_permissao exposto como global Jinja para que os templates possam
    # renderizar condicionalmente botões e seções protegidas por permissão.
    from app.helpers import has_permissao, file_url, t, carregar_traducoes
    app.jinja_env.globals['has_permissao'] = has_permissao
    app.jinja_env.globals['file_url'] = file_url
    app.jinja_env.globals['t'] = t
    # H4 — a MESMA frase de falta em inventário, dashboard e histórico. Descrever
    # a pendência de jeitos diferentes em cada tela é o que faz ninguém conferir.
    from app.complementos import descrever_pendencia, tempo_fora
    app.jinja_env.globals['descrever_pendencia'] = descrever_pendencia
    # H5 — o tempo fora sempre no mesmo formato: quem compara linhas de cobrança
    # compara com o olho, e dois formatos diferentes na mesma tela atrapalham.
    app.jinja_env.globals['tempo_fora'] = tempo_fora
    # B2 — a cor do tipo de bateria sai de uma lista branca, nunca do formulário:
    # ela é escrita dentro de `style`, e texto livre ali seria CSS injetado.
    from app.models import cor_de_bateria
    app.jinja_env.globals['cor_de_bateria'] = cor_de_bateria
    carregar_traducoes()

    @app.before_request
    def _sync_permissoes():
        """Re-sincroniza grupo e permissões do usuário a cada request.

        Garante que alterações de grupo feitas por um admin tomem efeito
        imediatamente, sem exigir logout do usuário afetado. A sessão Flask
        é um cookie — sem essa sincronização, o usuário continuaria com as
        permissões antigas até expirar a sessão manualmente.

        Rotas de autenticação e assets estáticos são ignoradas para não
        disparar query de banco em cada carregamento de CSS/JS.
        """
        from flask import request as _r, session as _s
        from sqlalchemy.orm import joinedload
        from app.models import Usuario, Grupo
        if _r.endpoint in ('static', 'auth.login', 'auth.logout',
                           'auth.esqueci_senha', 'auth.primeiro_acesso', None):
            return
        # Endpoints de API são chamados em alta frequência (polling, debounce de lookup).
        # A sessão já foi sincronizada no request de navegação anterior — não é necessário
        # re-sincronizar em cada chamada de API, que não altera estado de sessão.
        if _r.path.startswith('/api/'):
            return
        user_id = _s.get('user_id')
        if not user_id:
            return
        # joinedload: 1 query com JOIN em vez de 3 queries lazy (Usuario → grupo → permissoes).
        # joinedload é correto para many-to-one (grupo) e many-to-many (permissoes via tabela pivot).
        u = (db.session.query(Usuario)
             .options(joinedload(Usuario.grupo).joinedload(Grupo.permissoes))
             .filter(Usuario.id == user_id)
             .first())
        if not u:
            # Usuário foi removido do banco mas ainda tem sessão ativa — limpa tudo.
            _s.clear()
            return
        _s['grupo']      = u.grupo.nome if u.grupo else None
        _s['permissoes'] = [p.codigo for p in u.grupo.permissoes] if u.grupo else []
        _s['empresa_id'] = u.empresa_id
        _s['senha_provisoria'] = bool(u.senha_provisoria)
        # P7 — sincronizar campos de multi-tenancy a cada request (como grupo/permissoes)
        _nivel_anterior     = _s.get('nivel_acesso')
        _s['nivel_acesso']  = u.nivel_acesso or 'CD'
        _s['idioma']        = u.idioma or 'pt'
        # UX Tokens — mantém tema/acento sincronizados como grupo/idioma
        _s['tema']          = u.tema or 'light'
        _s['cor_acento']    = u.cor_acento
        _s['localidade_id'] = u.localidade_id
        # Se o nível mudou (admin alterou o usuário), descarta view_context antigo
        # para que o bloco abaixo o reinicialize com o escopo correto.
        if _nivel_anterior is not None and _nivel_anterior != _s['nivel_acesso']:
            _s.pop('view_context', None)
        if 'view_context' not in _s:
            if u.nivel_acesso == 'CD':
                _s['view_context'] = f'CD:{u.localidade_id}' if u.localidade_id else None
            else:
                _s['view_context'] = 'GLOBAL'

    @app.before_request
    def _exigir_troca_de_senha():
        """Com senha provisória, a única coisa que o usuário faz é trocá-la.

        A senha foi definida pela TI: até o dono escolher a dele, quem a conhece
        poderia agir em nome dele. Vale também para /api/ (o _sync_permissoes
        pula essas rotas, então a marca lida aqui é a que o login gravou).
        """
        from flask import request as _r, session as _s, redirect as _redirect, url_for as _url_for
        if not _s.get('senha_provisoria'):
            return
        if _r.endpoint in ('static', 'auth.trocar_senha', 'auth.logout', None):
            return
        if _r.path.startswith('/api/'):
            from flask import jsonify as _jsonify
            return _jsonify({'ok': False, 'erro': 'Troque a senha provisória antes de continuar.'}), 403
        return _redirect(_url_for('auth.trocar_senha'))

    @app.context_processor
    def inject_globals():
        """Injeta variáveis globais disponíveis em todos os templates Jinja.

        Centraliza o acesso a constantes de domínio e à foto de perfil do
        usuário logado, evitando duplicação nas views.
        """
        from flask import session
        from app.models import Usuario, Localidade

        # Foto de perfil do usuário logado — alimenta o avatar na navbar
        foto_perfil_url = None
        user_id = session.get('user_id')
        if user_id:
            u = db.session.get(Usuario, user_id)
            if u and u.foto_perfil:
                foto_perfil_url = f'/uploads/perfis/{u.foto_perfil}'

        # CDs da barra de contexto. Só para quem alcança mais de um (GLOBAL) —
        # para quem é de um CD a barra não aparece, e a query seria desperdício
        # em todo request. O escopo é a empresa da sessão, nunca o banco inteiro.
        cds_contexto = []
        if user_id and session.get('nivel_acesso') == 'GLOBAL':
            empresa_id = session.get('empresa_id')
            if empresa_id:
                cds_contexto = (Localidade.query
                                .filter(Localidade.empresa_id == empresa_id)
                                .order_by(Localidade.sigla).all())

        from app.helpers import t as _t

        # Traduz os labels do mapa de diagnóstico para o idioma ativo.
        # Os sub-itens são renderizados via JS (tojson), então a tradução
        # precisa acontecer aqui, antes da serialização, e não no template.
        _DIAG_TKEYS = {
            'FISICO': {
                'GATILHO':      'diagnostico.fisico.gatilho',
                'TELA_QUEBRADA':'diagnostico.fisico.tela_quebrada',
                'TECLADO':      'diagnostico.fisico.teclado',
                'CARCACA':      'diagnostico.fisico.carcaca',
            },
            'HARDWARE': {
                'LASER':        'diagnostico.hardware.laser',
                'TOUCH_CALIB':  'diagnostico.hardware.touch_calib',
                'BATERIA_INT':  'diagnostico.hardware.bateria_int',
                'WIFI_HW':      'diagnostico.hardware.wifi_hw',
            },
            'SISTEMA': {
                'BOOT_LOOP':    'diagnostico.sistema.boot_loop',
                'APP_CRASH':    'diagnostico.sistema.app_crash',
                'WIFI_CFG':     'diagnostico.sistema.wifi_cfg',
                'SLOW':         'diagnostico.sistema.slow',
            },
        }

        def _translate_diagnostico():
            result = {}
            for cat, items in DIAGNOSTICO_MAPA.items():
                cat_keys = _DIAG_TKEYS.get(cat, {})
                translated = []
                for code, label_pt, ref in items:
                    tkey  = cat_keys.get(code)
                    label = _t(tkey) if tkey else None
                    translated.append((code, label if (label and label != tkey) else label_pt, ref))
                result[cat] = translated
            return result

        mapa_diagnostico_i18n = _translate_diagnostico()

        # UX Tokens — cor_acento revalidada no RENDER (anti CSS-injection) e tema
        # resolvido para o anti-flash. cor_acento_seguro é None se o valor no banco
        # não casar o regex, caindo no verde padrão da marca.
        from app.helpers import cor_acento_segura as _cor_segura
        cor_acento_seguro = _cor_segura(session.get('cor_acento'))
        tema_pref = session.get('tema') or 'light'

        # Qual seção do manual o "?" desta tela abre (None esconde o botão).
        from app.ajuda import secao_da_tela
        from flask import request as _req

        return dict(
            ajuda_desta_tela=secao_da_tela(_req.endpoint),
            mapa_diagnostico=DIAGNOSTICO_MAPA,
            mapa_diagnostico_i18n=mapa_diagnostico_i18n,
            foto_perfil_url=foto_perfil_url,
            cds_contexto=cds_contexto,
            cor_acento_seguro=cor_acento_seguro,
            tema_pref=tema_pref,
        )

    # Resposta amigável para uploads que excedem MAX_CONTENT_LENGTH.
    # O Werkzeug lança RequestEntityTooLarge automaticamente — sem esse handler,
    # o usuário receberia uma página de erro genérica sem contexto.
    # A detecção de JSON/API garante que clientes mobile recebam JSON em vez de redirect.
    from werkzeug.exceptions import RequestEntityTooLarge
    from flask import jsonify as _jsonify, request as _req, flash as _flash, redirect as _redir

    @app.errorhandler(RequestEntityTooLarge)
    def handle_upload_too_large(e):
        """Trata o erro 413 com resposta adequada ao tipo de cliente (JSON ou web)."""
        if _req.is_json or _req.path.startswith('/api/'):
            return _jsonify({'ok': False, 'erro': 'Arquivo muito grande. Máximo permitido: 10 MB.'}), 413
        _flash('Arquivo muito grande. O limite é 10 MB.', 'danger')
        return _redir(_req.referrer or '/'), 413

    # Blueprints — cada módulo de rotas é registrado aqui.
    # A importação tardia (dentro da factory) é intencional: evita que os blueprints
    # sejam importados antes de db estar configurado.
    from app.routes.auth           import auth_bp
    from app.routes.coletores      import coletores_bp
    from app.routes.movimentacao   import movimentacao_bp
    from app.routes.dashboard      import dashboard_bp
    from app.routes.suspensos      import suspensos_bp
    from app.routes.colaboradores  import colaboradores_bp
    from app.routes.complementos    import complementos_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(coletores_bp)
    app.register_blueprint(movimentacao_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(suspensos_bp)
    app.register_blueprint(colaboradores_bp)
    app.register_blueprint(complementos_bp)

    return app
