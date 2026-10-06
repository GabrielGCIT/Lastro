import json
import os
import re
import secrets
from functools import wraps
from datetime import datetime

from flask import session, redirect, url_for, flash, request
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from app import db, FOTO_FOLDER, INSTANCE_FOLDER, ALLOWED_IMG_EXTENSIONS


# ---------------------------------------------------------------------------
# PREFERÊNCIAS DE UX — TEMA E COR DE ACENTO (patch UX Tokens)
# ---------------------------------------------------------------------------

# Temas suportados. 'auto' segue prefers-color-scheme do sistema operacional.
TEMAS_VALIDOS = ('light', 'dark', 'auto')

# Paleta CURADA de cores de acento. Cada cor foi escolhida para garantir
# contraste WCAG AA (>= 4.5:1) de texto BRANCO sobre ela — por isso a UI não
# deixa o usuário digitar um hex livre: uma cor clara quebraria a legibilidade
# dos botões/badges/avatares que usam --accent-contrast branco.
# A ordem é a de exibição na paleta do perfil.
CORES_ACENTO = (
    '#7AC143',  # verde da marca (default explícito — mas branco falha AA nele;
                # ver nota abaixo: o verde da marca é o estado NULO, não um swatch)
    '#2E7D32',  # verde escuro
    '#1565C0',  # azul
    '#00695C',  # teal
    '#6A1B9A',  # roxo
    '#AD1457',  # magenta
    '#C62828',  # vermelho
    '#37474F',  # ardósia (neutro)
)

# Swatches efetivamente selecionáveis na UI. O verde da marca (#7AC143) é claro
# demais para texto branco passar AA, então ele NÃO é um swatch: é o estado
# "padrão" (cor_acento = NULL) restaurado pelo botão de reset. Os 7 abaixo
# passam AA com texto branco (--accent-contrast: #fff).
CORES_ACENTO_SELECIONAVEIS = (
    '#2E7D32', '#1565C0', '#00695C', '#6A1B9A',
    '#AD1457', '#C62828', '#37474F',
)

_RE_HEX_COR = re.compile(r'^#[0-9a-fA-F]{6}$')


def cor_acento_segura(valor):
    """Valida um hex de cor de acento para injeção segura em CSS (anti CSS-injection).

    Defesa de RENDER exigida pelo patch UX Tokens: só devolve o valor se casar
    EXATAMENTE com ^#[0-9a-fA-F]{6}$. Qualquer outra coisa (None, string vazia,
    payload com ';' ou '}') vira None → o template cai no verde padrão da marca.
    Independente da validação de SAVE — protege mesmo que um valor inválido
    tenha entrado no banco por outro caminho.
    """
    if valor and _RE_HEX_COR.match(valor):
        return valor
    return None


def tema_seguro(valor):
    """Normaliza o tema para um dos valores suportados; devolve None se inválido."""
    return valor if valor in TEMAS_VALIDOS else None


# ---------------------------------------------------------------------------
# E-MAIL COMO CREDENCIAL (T2)
# ---------------------------------------------------------------------------

# Validação estrutural mínima (algo@algo.tld) — o teste real de um e-mail é
# ele receber mensagem; aqui só barramos lixo óbvio sem recusar endereços
# válidos exóticos. Não usar regex "completa" de RFC 5322: falso-negativo
# em endereço legítimo é pior que aceitar um formato estranho.
_RE_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalizar_email(valor):
    """Normaliza e-mail para persistência/lookup: strip + lowercase, ou None se vazio."""
    v = (valor or '').strip().lower()
    return v or None


def email_valido(email: str) -> bool:
    """True se o e-mail (já normalizado) tem formato estrutural plausível."""
    return bool(email and _RE_EMAIL.match(email))


def email_em_uso(email: str, excluir_id=None) -> bool:
    """True se o e-mail já pertence a outro usuário (unicidade GLOBAL, app-level).

    Case-insensitive via lower() para cobrir e-mails legados gravados antes da
    normalização. App-level como numero_serie: unique de banco conflitaria com
    múltiplos NULLs no SQL Server (OPERADOR não tem e-mail).
    """
    from sqlalchemy import func
    from app.models import Usuario
    if not email:
        return False
    q = Usuario.query.filter(func.lower(Usuario.email) == email)
    if excluir_id is not None:
        q = q.filter(Usuario.id != excluir_id)
    return q.first() is not None


# ---------------------------------------------------------------------------
# AUDITORIA
# ---------------------------------------------------------------------------

# Sentinela do registrar_log: distingue "empresa não informada" (usa a da
# sessão) de um None explícito passado pelo caller.
_LOG_EMPRESA_DA_SESSAO = object()


def registrar_log(acao: str, detalhe: str, empresa_id=_LOG_EMPRESA_DA_SESSAO):
    """Registra uma linha no log de auditoria. Silencia falhas para não quebrar o fluxo.

    O try/except amplo aqui é intencional: se o log falhar (banco fora do ar,
    sessão corrompida, etc.), a operação de negócio que chamou registrar_log
    não pode ser interrompida por isso. O erro vai para stdout onde pode ser
    capturado pelo supervisor/systemd, mas não propaga para o usuário.

    empresa_id opcional: quem sabe a que trilha o evento pertence pode carimbar
    explicitamente. Sem o argumento, vale a empresa da sessão.
    """
    try:
        from app.models import LogAuditoria
        if empresa_id is _LOG_EMPRESA_DA_SESSAO:
            # O log nasce carimbado com a empresa da sessão (a tela de auditoria
            # filtra por empresa_id). empresa_id None (log fora de request) é
            # aceitável — o backfill do seed_empresa o vincula no boot seguinte.
            empresa_id = session.get('empresa_id')
        log = LogAuditoria(
            usuario=session.get('nome', 'Sistema/Anônimo'),
            user_re=session.get('re'),
            acao=acao,
            detalhe=detalhe,
            empresa_id=empresa_id,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as exc:
        print(f"[AUDIT ERROR] {exc}")


# ---------------------------------------------------------------------------
# PERMISSÕES — RBAC
# ---------------------------------------------------------------------------

def has_permissao(codigo: str) -> bool:
    """Verifica se o usuário logado possui a permissão informada.

    Lê direto da sessão — que é re-sincronizada a cada request pelo hook
    _sync_permissoes em __init__.py — então não precisa bater no banco.
    O default `[]` evita KeyError em sessões stale ou não autenticadas,
    onde a chave 'permissoes' pode simplesmente não existir.
    """
    return codigo in session.get('permissoes', [])


def login_required(f):
    """Decorator simples: garante que existe uma sessão ativa antes de prosseguir."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def permissao_required(*codigos):
    """Verifica login e restringe à(s) permissão(ões) informada(s).

    Aceita um ou mais códigos — lógica OR: passa se o usuário tiver QUALQUER
    um deles. Isso permite que uma rota seja acessível por perfis diferentes
    sem precisar criar permissões redundantes.

    Registra tentativas negadas no log de auditoria para rastreabilidade.

    Uso:
        @permissao_required('admin.usuarios')
        @permissao_required('admin.usuarios', 'admin.grupos')   # OU lógico
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('auth.login'))
            perms = session.get('permissoes', [])
            if not any(c in perms for c in codigos):
                flash('Acesso negado: permissão insuficiente.', 'danger')
                registrar_log(
                    'ACESSO_NEGADO',
                    f'Tentativa de acesso restrito por {session.get("nome")} '
                    f'(requer: {", ".join(codigos)})',
                )
                return redirect(url_for('dashboard.index'))
            return f(*args, **kwargs)
        return decorated
    return decorator


# ---------------------------------------------------------------------------
# MULTI-TENANCY — FILTRO POR CONTEXTO DE LOCALIDADE
# ---------------------------------------------------------------------------

def get_empresa_id():
    """A empresa da sessão — a âncora do isolamento. None = sessão sem empresa."""
    return session.get('empresa_id')


def empresa_visivel(empresa_id_dono) -> bool:
    """True se a empresa dona de um recurso é a da sessão.

    Recurso órfão (empresa_id_dono None) nunca é visível: não dá para provar que
    pertence à empresa da sessão, então fail-closed. Sessão sem empresa também
    não enxerga nada.
    """
    emp = session.get('empresa_id')
    return emp is not None and empresa_id_dono == emp


def criterio_empresa(coluna):
    """Critério SQL fail-closed para escopar uma LEITURA por empresa (T4a).

    `coluna == session['empresa_id']` inline viraria `coluna IS NULL` numa sessão
    sem empresa — casando com os registros ÓRFÃOS em vez de zerar o resultado,
    um vazamento fail-OPEN. Aqui o mesmo caso devolve `db.false()`.

    Usar sempre como `Model.query.filter(criterio_empresa(Model.empresa_id))`.
    """
    empresa_id = session.get('empresa_id')
    if not empresa_id:
        return db.false()
    return coluna == empresa_id


def _escopo_geografico():
    """Ids de Localidade permitidos pelo nível e pelo contexto ativo da sessão.

    Independe da empresa — o recorte por tenant é aplicado por
    get_filtro_localidade, que intersecta este conjunto com as localidades da
    empresa da sessão. Retorna:
        None    — sem restrição: todos os CDs da empresa
        set()   — sem escopo (ex.: nível CD sem localidade) → resultado vazio
        {ids}   — o CD do contexto escolhido na barra, ou o CD do usuário

    Fail-closed por construção: nível ou contexto que não reconhecemos cai no CD
    do próprio usuário, nunca em "vê tudo". Isso importa mais depois da L2, com
    só dois níveis — um valor de banco fora de NIVEIS_ACESSO fecha, não abre.
    """
    nivel = session.get('nivel_acesso', 'CD')
    ctx   = session.get('view_context')

    if nivel == 'CD':
        loc_id = session.get('localidade_id')
        return {loc_id} if loc_id else set()

    if nivel == 'GLOBAL' and (ctx == 'GLOBAL' or not ctx):
        return None

    if ctx and ctx.startswith('CD:'):
        try:
            return {int(ctx.split(':')[1])}
        except (ValueError, IndexError):
            return set()

    # Nível fora da lista, ou contexto que não sabemos ler: escopo do próprio CD.
    loc_id = session.get('localidade_id')
    return {loc_id} if loc_id else set()


def get_filtro_localidade():
    """IDs de Localidade visíveis para a sessão — fail-closed por construção (T3).

    Contrato:
        [ids] — localidades permitidas, SEMPRE dentro da empresa da sessão.
        []    — sem acesso: ZERO resultados em TODOS os módulos.

    Nunca devolve None: não existe mais quem enxergue "tudo do banco". Os
    callers ainda tratam None como "sem filtro" por herança — o ramo ficou morto
    e inofensivo.

    O conjunto base são as localidades da empresa da sessão; o nível/contexto
    (CD/PAIS/AMERICA/GLOBAL) só ESTREITA dentro dele. Sessão sem empresa (estado
    inválido) cai em [].
    """
    empresa_id = session.get('empresa_id')
    if not empresa_id:
        return []

    from app.models import Localidade
    base = {
        r[0] for r in db.session.query(Localidade.id)
        .filter(Localidade.empresa_id == empresa_id).all()
    }
    if not base:
        return []

    geo = _escopo_geografico()
    if geo is None:
        # GLOBAL da empresa — toda a empresa, nunca além dela.
        return list(base)
    # Interseção: o nível/contexto só pode estreitar dentro da empresa.
    return [i for i in base if i in geo]


# ---------------------------------------------------------------------------
# MULTI-TENANCY — LADO DA ESCRITA (pacote Segurança pré-T4, S1)
# ---------------------------------------------------------------------------
# get_filtro_localidade blinda a LEITURA (T3). Estes helpers blindam a ESCRITA:
# carimbam o tenant nas criações (família A) e validam que uma FK vinda do form
# pertence ao tenant da sessão (família E). Não há interceptor global de query —
# cada rota chama o helper explicitamente, mantendo o fluxo rastreável.


def empresa_para_escrita():
    """Empresa que deve carimbar um registro criado nesta sessão.

    Devolve None só numa sessão quebrada, sem empresa. O chamador BLOQUEIA a
    criação nesse caso — nunca grava um registro órfão.
    """
    return session.get('empresa_id')


def localidade_no_escopo(loc_id) -> bool:
    """True se a Localidade loc_id é gravável/visível para a sessão atual.

    Predicado sobre o mesmo conjunto de get_filtro_localidade. Registro sem
    localidade (loc_id None) não é de ninguém — fail-closed.
    """
    permitidas = get_filtro_localidade()
    if permitidas is None:
        return True
    return loc_id is not None and loc_id in permitidas


def localidade_para_escrita(loc_id):
    """Valida um localidade_id vindo de FORM contra o escopo de escrita (família E).

    Uma localidade de OUTRA empresa (ou inexistente) é tratada como se não
    existisse — o chamador rejeita a operação, nunca devolve 403 (mesmo padrão
    "inexistente" do T3).

    Retorna a tupla (ok, loc_id):
        (True,  None)  — loc_id vazio: ausência legítima de localidade.
        (True,  int)   — dentro do escopo: pode gravar.
        (False, None)  — fora do escopo/inexistente: rejeitar a operação.
    """
    if not loc_id:
        return True, None
    permitidas = get_filtro_localidade()
    return (True, loc_id) if loc_id in permitidas else (False, None)


def re_colaborador_em_uso(re, empresa_id, excluir_id=None) -> bool:
    """True se o RE já pertence a outro colaborador da MESMA empresa (família D).

    A unicidade do RE passa a ser POR EMPRESA (constraint uq_colaboradores_empresa_re):
    dois tenants podem ter o mesmo RE sem conflito. empresa_id falsy (órfão/sessão
    quebrada) desliga a checagem — devolve False, mesmo critério de numero_serie_em_uso.
    """
    if not re or not empresa_id:
        return False
    from app.models import Colaborador
    q = Colaborador.query.filter(Colaborador.re == re,
                                 Colaborador.empresa_id == empresa_id)
    if excluir_id is not None:
        q = q.filter(Colaborador.id != excluir_id)
    return q.first() is not None


def re_usuario_em_uso(re, empresa_id, excluir_id=None) -> bool:
    """True se o RE (matrícula) já pertence a outro usuário da MESMA empresa (família D).

    RE vira matrícula interna única por empresa (constraint uq_usuarios_empresa_re);
    o login global é por e-mail (T2, ver email_em_uso). empresa_id falsy desliga a
    checagem — mesmo critério de re_colaborador_em_uso.
    """
    if not re or not empresa_id:
        return False
    from app.models import Usuario
    q = Usuario.query.filter(Usuario.re == re, Usuario.empresa_id == empresa_id)
    if excluir_id is not None:
        q = q.filter(Usuario.id != excluir_id)
    return q.first() is not None


# ---------------------------------------------------------------------------
# POLÍTICAS POR EMPRESA
# ---------------------------------------------------------------------------

def _cast_politica(valor_str: str, tipo):
    """Converte o valor String armazenado pro tipo declarado no registry.

    bool não usa bool(str) direto — bool('0') é True em Python (string não
    vazia), o que inverteria toda política booleana gravada como '0'.
    """
    if tipo is bool:
        return valor_str in ('1', 'true', 'True')
    return tipo(valor_str)


def politica_empresa(chave: str, empresa_id=None):
    """Lê uma política de UMA empresa, com fallback fail-safe pro default do registry.

    Nunca explode o fluxo de negócio: chave fora de POLITICAS_PLATAFORMA
    devolve None; empresa sem linha gravada, OU valor que falha o cast do tipo
    declarado, degradam pro default do registry. empresa_id opcional — sem
    argumento resolve a empresa da sessão; sem empresa, cai direto no default.
    """
    from app.models import EmpresaPolitica, POLITICAS_PLATAFORMA
    if chave not in POLITICAS_PLATAFORMA:
        return None
    default_str, tipo = POLITICAS_PLATAFORMA[chave]

    eid = empresa_id if empresa_id is not None else empresa_para_escrita()
    valor_str = default_str
    if eid:
        row = EmpresaPolitica.query.filter_by(empresa_id=eid, chave=chave).first()
        if row:
            valor_str = row.valor

    try:
        return _cast_politica(valor_str, tipo)
    except (ValueError, TypeError):
        return _cast_politica(default_str, tipo)


# ---------------------------------------------------------------------------
# ARQUIVOS
# ---------------------------------------------------------------------------

_IMG_MAGIC   = {
    b'\xff\xd8\xff',             # JPEG
    b'\x89PNG\r\n\x1a\n',        # PNG
}
_WEBP_PREFIX = b'RIFF'
_WEBP_MARKER = b'WEBP'


def allowed_image(filename: str, stream=None) -> bool:
    """Valida extensão de imagem e, se stream fornecido, verifica magic bytes reais."""
    if not ('.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMG_EXTENSIONS):
        return False
    if stream is None:
        return True
    header = stream.read(12)
    stream.seek(0)
    if any(header[:len(sig)] == sig for sig in _IMG_MAGIC):
        return True
    # WEBP: começa com RIFF....WEBP
    if header[:4] == _WEBP_PREFIX and header[8:12] == _WEBP_MARKER:
        return True
    return False


def file_url(filename: str | None, kind: str = 'perfil') -> str | None:
    """Retorna a URL de acesso a um arquivo do sistema.

    Roteia para a rota Flask correta conforme o tipo de arquivo. Ponto único:
    nenhum template monta caminho de upload na mão.

    Args:
        filename: Nome do arquivo (sem path). None retorna None.
        kind: Categoria do arquivo. Determina a rota Flask usada.
              'perfil'   → /uploads/perfis/<filename>     (fotos de perfil)
              'cracha'   → /uploads/crachas/<filename>    (fotos de crachá)
              'foto'     → /uploads/fotos/<filename>      (fotos operacionais)
    """
    if not filename:
        return None
    from flask import url_for
    _rotas = {
        'perfil':     ('dashboard.foto_perfil_file', 'filename'),
        'cracha':     ('dashboard.foto_cracha_file', 'filename'),
        'foto':       ('movimentacao.foto_file',     'filename'),
    }
    endpoint, param = _rotas.get(kind, _rotas['perfil'])
    return url_for(endpoint, **{param: filename})


def salvar_foto(arquivo, prefixo: str):
    """Salva imagem em uploads/fotos/ e retorna o nome do arquivo ou None.

    Sufixo aleatório além do timestamp: o prefixo (ex.: 'RET_{busca_valor}') se repete entre requisições diferentes para o MESMO
    coletor, então dois uploads no MESMO segundo (duplo clique, duas
    abas) colidiriam no nome — sem lógica de "apagar o anterior" aqui, a
    colisão vira sobrescrita silenciosa: o segundo save() apaga os bytes do
    primeiro, e os dois registros no banco ficam apontando pro mesmo arquivo,
    que só tem o conteúdo de um deles.
    secure_filename sanitiza o nome original para prevenir path traversal.
    """
    if not arquivo or not allowed_image(arquivo.filename, arquivo.stream):
        return None
    ext = arquivo.filename.rsplit('.', 1)[1].lower()
    filename = secure_filename(
        f"{prefixo}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}.{ext}")
    arquivo.save(os.path.join(FOTO_FOLDER, filename))
    return filename


# ---------------------------------------------------------------------------
# PARSE DE FORM — robusto contra input vazio/malformado (tampering → None, não 500)
# ---------------------------------------------------------------------------

def voltar_seguro(padrao, campo='voltar'):
    """Destino do redirect depois de uma ação em lote, vindo do FORMULÁRIO.

    🔴 O valor chega do cliente, e `redirect()` obedece a qualquer URL —
    inclusive `https://outro-site`. Uma tela que aceita esse destino cru vira
    trampolim para fora do sistema, com a credencial do usuário na mão.

    Só passa caminho relativo: começa com "/" e NÃO com "//", que o navegador
    lê como outro host (`//evil.com/x` é URL absoluta com protocolo herdado).
    Qualquer outra coisa cai no padrão — fail-closed, como o resto do T3.

    Nasceu na B4, em `routes/complementos.py`, e subiu para cá quando a mesma
    classe foi encontrada no `mover_em_lote` da U1: guarda de segurança
    duplicada é guarda que diverge no dia em que só uma for corrigida.
    """
    destino = (request.form.get(campo) or '').strip()
    if destino.startswith('/') and not destino.startswith('//'):
        return destino
    return padrao


def parse_int(valor):
    """Converte um valor de form em int, ou None se vazio/inválido (blindagem contra tampering)."""
    if valor is None:
        return None
    s = str(valor).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# MULTI-IDIOMA
# ---------------------------------------------------------------------------

_traducoes: dict = {}
_TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'translations')


def carregar_traducoes():
    """Carrega todos os arquivos JSON de tradução para memória na inicialização."""
    global _traducoes
    for lang in ['pt', 'en', 'es']:
        path = os.path.join(_TRANSLATIONS_DIR, f'{lang}.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                _traducoes[lang] = json.load(f)
        except FileNotFoundError:
            _traducoes[lang] = {}


def t(chave: str, lang: str = None, **kwargs) -> str:
    """Retorna tradução da chave no idioma ativo da sessão.

    Fallback em cascata: idioma solicitado → PT-BR → chave literal.
    Nunca quebra a UI: se a chave não existir em nenhum idioma, retorna a própria chave.

    Kwargs opcionais são substituídos via str.format — ex: t('msg', total=42)
    com a string "Total: {total}" retorna "Total: 42".
    """
    idioma = lang or session.get('idioma', 'pt')
    text = (
        _traducoes.get(idioma, {}).get(chave)
        or _traducoes.get('pt', {}).get(chave)
        or chave
    )
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError, AttributeError):
            pass
    return text


# ---------------------------------------------------------------------------
# SENHA E PRIMEIRO ACESSO
# ---------------------------------------------------------------------------

SENHA_MINIMA = 8


def problema_na_senha(senha, confirmacao=None):
    """A frase que explica por que a senha não serve, ou None se ela serve.

    Um lugar só para a política: criação de usuário, troca no perfil, troca
    obrigatória e primeiro acesso. Política espalhada diverge na primeira vez
    que alguém mudar só um dos lugares.
    """
    if not senha or len(senha) < SENHA_MINIMA:
        return f'A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.'
    if confirmacao is not None and senha != confirmacao:
        return 'As senhas não coincidem.'
    return None


# O token do primeiro acesso mora num arquivo dentro de instance/, nunca no
# banco nem no código: ele só existe enquanto a instalação não tem usuário, e
# some no instante em que o administrador é criado.
PRIMEIRO_ACESSO_ARQUIVO = os.path.join(INSTANCE_FOLDER, 'primeiro_acesso.token')


def instalacao_sem_usuario() -> bool:
    from app.models import Usuario
    return Usuario.query.first() is None


def preparar_primeiro_acesso():
    """Garante o token do primeiro acesso e devolve-o — ou None se já há usuário.

    🔴 Por que token e não uma tela aberta: entre ligar o serviço e a TI abrir o
    navegador, qualquer pessoa na rede que chegasse antes criaria o próprio
    administrador. O token só aparece no log do servidor, que só quem instala lê.
    E nenhuma senha passa pelo log: quem abre o endereço escolhe a sua.

    O mesmo token vale até ser usado (reinício não gera outro), para o endereço
    anotado no primeiro boot continuar funcionando.
    """
    if not instalacao_sem_usuario():
        if os.path.exists(PRIMEIRO_ACESSO_ARQUIVO):
            os.remove(PRIMEIRO_ACESSO_ARQUIVO)
        return None
    if os.path.exists(PRIMEIRO_ACESSO_ARQUIVO):
        with open(PRIMEIRO_ACESSO_ARQUIVO, encoding='utf-8') as fh:
            token = fh.read().strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    with open(PRIMEIRO_ACESSO_ARQUIVO, 'w', encoding='utf-8') as fh:
        fh.write(token)
    return token


def token_primeiro_acesso_confere(token) -> bool:
    """O token recebido é o da instalação — e ainda não há usuário nenhum."""
    import hmac
    if not token or not instalacao_sem_usuario():
        return False
    if not os.path.exists(PRIMEIRO_ACESSO_ARQUIVO):
        return False
    with open(PRIMEIRO_ACESSO_ARQUIVO, encoding='utf-8') as fh:
        esperado = fh.read().strip()
    return bool(esperado) and hmac.compare_digest(esperado, token)


def criar_administrador(nome, email, senha, re='admin'):
    """Cria o primeiro usuário: perfil TI, nível GLOBAL, na empresa da instalação.

    A senha foi escolhida pelo próprio dono, então NÃO é provisória. Consome o
    token do primeiro acesso.
    """
    from app.models import Usuario, Grupo, Empresa
    from app.startup import PERFIL_ADMIN
    empresa = Empresa.query.order_by(Empresa.id).first()
    perfil = Grupo.query.filter_by(nome=PERFIL_ADMIN, empresa_id=empresa.id).first()
    admin = Usuario(
        nome=nome, re=re, email=normalizar_email(email),
        senha_hash=generate_password_hash(senha, method='scrypt'),
        grupo_id=perfil.id, empresa_id=empresa.id,
        # GLOBAL: o administrador precisa enxergar todos os CDs. Sem isso ele
        # cairia no default 'CD' sem localidade e veria zero em todo módulo.
        nivel_acesso='GLOBAL',
    )
    db.session.add(admin)
    db.session.commit()
    if os.path.exists(PRIMEIRO_ACESSO_ARQUIVO):
        os.remove(PRIMEIRO_ACESSO_ARQUIVO)
    return admin
