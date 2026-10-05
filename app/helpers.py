import json
import os
import re
import secrets
from functools import wraps
from datetime import datetime

from flask import session, redirect, url_for, flash, request
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from app import db, FOTO_FOLDER, ALLOWED_IMG_EXTENSIONS


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
# sessão/impersonation) de um None explícito passado pelo caller.
_LOG_EMPRESA_DA_SESSAO = object()


def registrar_log(acao: str, detalhe: str, empresa_id=_LOG_EMPRESA_DA_SESSAO):
    """Registra uma linha no log de auditoria. Silencia falhas para não quebrar o fluxo.

    O try/except amplo aqui é intencional: se o log falhar (banco fora do ar,
    sessão corrompida, etc.), a operação de negócio que chamou registrar_log
    não pode ser interrompida por isso. O erro vai para stdout onde pode ser
    capturado pelo supervisor/systemd, mas não propaga para o usuário.

    empresa_id opcional: quem sabe a que trilha o evento pertence pode carimbar
    explicitamente (o console de empresas usa — ações de PLATAFORMA não podem
    cair na trilha do tenant que o Owner estiver impersonando). Sem o argumento,
    vale a empresa efetiva da sessão (impersonada, se houver — T4c).
    """
    try:
        from app.models import LogAuditoria
        if empresa_id is _LOG_EMPRESA_DA_SESSAO:
            # T3 — o log nasce carimbado com a empresa da sessão. A empresa vê só
            # o seu; o Owner vê tudo (a tela de auditoria filtra por empresa_id).
            # empresa_id None (ex: log de sistema fora de request) é aceitável —
            # o backfill do seed_empresa o vincula à MB no boot seguinte.
            # T4c — Owner impersonando carimba a empresa impersonada (a ação
            # pertence à trilha daquele tenant), não a MB do Owner.
            empresa_id = _impersonada_id() or session.get('empresa_id')
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

def _impersonada_id():
    """Empresa que o Owner está impersonando nesta sessão (T4c), ou None.

    Chave PRÓPRIA `session['empresa_impersonada']` — o _sync_permissoes reescreve
    empresa_id/is_owner a cada request, mas NÃO toca nesta chave, então a
    impersonation sobrevive ao hook. Só vale para o Owner (is_owner): para
    qualquer outro é ignorada, mesmo se a chave estiver setada (tampering) —
    fail-closed. Os helpers de isolamento consultam isto ANTES do atalho is_owner,
    fazendo o Owner "dentro" da empresa agir como um GLOBAL só daquele tenant.
    """
    if not session.get('is_owner'):
        return None
    return session.get('empresa_impersonada')


def get_empresa_id():
    """Retorna a empresa_id efetiva da sessão — a âncora do isolamento multi-tenant.

    T4c — o Owner impersonando devolve o id da empresa impersonada (age como ela).
    None é EXCLUSIVO do Owner SEM impersonation (is_owner): significa "todas as
    empresas". Um usuário comum sempre tem uma empresa; se por algum motivo a
    sessão não a carregar, devolve None mas o chamador (get_filtro_localidade,
    guards de serving) trata isso como fail-closed via is_owner — nunca "vê tudo".

    Não confundir os dois Nones: o do Owner (autorizado a tudo) e o de uma
    sessão comum quebrada. Sempre cheque is_owner ANTES de interpretar None.
    """
    imp = _impersonada_id()
    if imp is not None:
        return imp
    if session.get('is_owner'):
        return None
    return session.get('empresa_id')


def empresa_visivel(empresa_id_dono) -> bool:
    """True se a empresa dona de um recurso é visível para a sessão atual.

    Base dos guards de serving de arquivo (T3): o Owner enxerga qualquer
    empresa; o usuário comum só a própria. Recurso órfão (empresa_id_dono None,
    ex.: localidade legada sem empresa) só é visível ao Owner — não dá para
    provar que pertence ao tenant da sessão, então fail-closed.

    T4c — o Owner impersonando enxerga SÓ a empresa impersonada (nem as outras,
    nem os órfãos): a checagem da impersonation vem antes do atalho is_owner.
    """
    imp = _impersonada_id()
    if imp is not None:
        return empresa_id_dono == imp
    if session.get('is_owner'):
        return True
    emp = session.get('empresa_id')
    return emp is not None and empresa_id_dono == emp


def criterio_empresa(coluna):
    """Critério SQL fail-closed para escopar uma LEITURA por empresa (T4a).

    Padroniza os ~14 filtros que antes faziam `coluna == session['empresa_id']`
    inline. O ganho não é só menos boilerplate: `coluna == None` (não-owner com
    sessão quebrada, sem empresa_id) gerava `coluna IS NULL`, que casava com os
    registros ÓRFÃOS em vez de zerar o resultado — um vazamento fail-OPEN. Aqui
    o mesmo caso devolve `db.false()`: zero linhas, sempre.

    Contrato (espelha get_filtro_localidade):
        Owner            → db.true()  (sem filtro; enxerga todas as empresas)
        sessão sem empresa → db.false() (não-owner sem tenant: fecha)
        senão            → coluna == empresa_id

    Usar sempre como `Model.query.filter(criterio_empresa(Model.empresa_id))`.
    T4c — a impersonation entra AQUI (ponto único das 14 leituras): o Owner
    "dentro" de uma empresa passa a filtrar por ela como se fosse o tenant.
    """
    imp = _impersonada_id()
    if imp is not None:
        return coluna == imp
    if session.get('is_owner'):
        return db.true()
    empresa_id = session.get('empresa_id')
    if not empresa_id:
        return db.false()
    return coluna == empresa_id


def _escopo_geografico():
    """Conjunto de ids de Localidade permitido pelo nível/contexto GEOGRÁFICO.

    Independe da empresa — o recorte por tenant é aplicado por
    get_filtro_localidade, que intersecta este conjunto com as localidades da
    empresa da sessão. Retorna:
        None    — sem restrição geográfica (GLOBAL sem contexto): "toda a empresa"
        set()   — sem escopo (ex.: CD sem localidade) → resultado final vazio
        {ids}   — localidades do contexto/nível ativo
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

    if ctx and ctx.startswith('PAIS:'):
        # Query direta: 1 JOIN ao invés de carregar o objeto Pais + iterar localidades
        sigla = ctx.split(':')[1]
        from app.models import Localidade, Pais
        rows = db.session.query(Localidade.id).join(Pais, Localidade.pais_id == Pais.id).filter(Pais.sigla == sigla).all()
        return {r[0] for r in rows}

    if ctx and ctx.startswith('AMERICA:'):
        # 1 query com 2 JOINs ao invés de carregar America → paises[] → localidades[]
        sigla = ctx.split(':')[1]
        from app.models import Localidade, Pais, America
        rows = (db.session.query(Localidade.id)
                .join(Pais,    Localidade.pais_id   == Pais.id)
                .join(America, Pais.america_id       == America.id)
                .filter(America.sigla == sigla)
                .all())
        return {r[0] for r in rows}

    # ctx ausente ou incompatível com o nível — usa escopo base do usuário
    if nivel == 'PAIS':
        pais_id = session.get('pais_id')
        if pais_id:
            from app.models import Localidade
            rows = db.session.query(Localidade.id).filter(Localidade.pais_id == pais_id).all()
            return {r[0] for r in rows}
        return set()
    if nivel == 'AMERICA':
        america_id = session.get('america_id')
        if america_id:
            from app.models import Localidade, Pais
            rows = (db.session.query(Localidade.id)
                    .join(Pais, Localidade.pais_id == Pais.id)
                    .filter(Pais.america_id == america_id)
                    .all())
            return {r[0] for r in rows}
        return set()
    # CD sem localidade configurada
    loc_id = session.get('localidade_id')
    return {loc_id} if loc_id else set()


def get_filtro_localidade():
    """IDs de Localidade visíveis para a sessão — fail-closed por construção (T3).

    Contrato:
        None  — EXCLUSIVO do Owner (is_owner): sem filtro, enxerga todas as
                empresas e até localidades órfãs (empresa_id NULL).
        [ids] — localidades permitidas, SEMPRE dentro da empresa da sessão.
        []    — sem acesso: ZERO resultados em TODOS os módulos.

    O usuário comum NUNCA recebe None. O conjunto base são as localidades da
    empresa da sessão; o nível/contexto (CD/PAIS/AMERICA/GLOBAL) só ESTREITA
    dentro desse base set — GLOBAL de empresa = todas as localidades da empresa
    dele, jamais do banco inteiro. Uma sessão comum sem empresa (estado
    inválido) cai em [] — fail-closed, nunca "vê tudo".

    Os callers seguem o padrão: None → sem filtro; [ids] → `.in_(ids)`;
    [] → filtro que zera o resultado (`filter(False)`).

    T4c — o Owner impersonando NÃO recebe None: o base set vira as localidades da
    empresa impersonada (ele vira um GLOBAL só daquele tenant). O view_context é
    limpo ao entrar, então _escopo_geografico devolve None (toda a empresa).
    """
    imp = _impersonada_id()
    if imp is None:
        if session.get('is_owner'):
            return None
        empresa_id = session.get('empresa_id')
        if not empresa_id:
            # Não-owner sem empresa carregada — não dá para provar escopo. Fecha.
            return []
    else:
        empresa_id = imp

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
    """Empresa (tenant) que deve carimbar um registro criado nesta sessão.

    Promoção do _empresa_para_categorias (ativos.py): get_empresa_id() devolve
    None para o Owner, que então cai na empresa da própria sessão (o admin da MB
    é MB). A impersonation do T4 troca session['empresa_id'] e entra por aqui.

    Devolve None só quando não há tenant algum na sessão — Owner de bootstrap
    sem contexto, ou sessão quebrada. O chamador BLOQUEIA a criação pedindo
    contexto nesse caso; nunca grava um registro órfão (decisão de produto do
    kickoff 15/07, mesmo padrão de categoria_criar).
    """
    return get_empresa_id() or session.get('empresa_id')


def localidade_no_escopo(loc_id) -> bool:
    """True se a Localidade loc_id é gravável/visível para a sessão atual.

    Predicado sobre o mesmo conjunto de get_filtro_localidade: o Owner (None)
    enxerga qualquer localidade, inclusive órfãs; o usuário comum só as da sua
    empresa. Registro sem localidade (loc_id None) só é do Owner — fail-closed.
    Base da validação de FK das famílias E (coletor/ativo por localidade).
    """
    permitidas = get_filtro_localidade()
    if permitidas is None:
        return True
    return loc_id is not None and loc_id in permitidas


def localidade_para_escrita(loc_id):
    """Valida um localidade_id vindo de FORM contra o escopo de escrita (família E).

    Uma localidade de OUTRA empresa (ou inexistente) é tratada como se não
    existisse — o chamador rejeita a operação, nunca devolve 403 (mesmo padrão
    "inexistente" do T3). O Owner grava em qualquer localidade existente.

    Retorna a tupla (ok, loc_id):
        (True,  None)  — loc_id vazio: ausência legítima de localidade.
        (True,  int)   — dentro do escopo (ou Owner + localidade existe): pode gravar.
        (False, None)  — fora do escopo/inexistente: rejeitar a operação.
    """
    if not loc_id:
        return True, None
    permitidas = get_filtro_localidade()
    if permitidas is None:
        # Owner — qualquer localidade existente serve; confere para não gravar FK quebrada.
        from app.models import Localidade
        return (True, loc_id) if db.session.get(Localidade, loc_id) else (False, None)
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
    argumento resolve a empresa da sessão (mesmo critério de
    empresa_para_escrita: Owner impersonando aplica a política do tenant
    impersonado; sem tenant nenhum, cai direto no default).
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
# INICIALIZAÇÃO
# ---------------------------------------------------------------------------

def _senha_do_admin():
    """Senha do admin de bootstrap: do ambiente, ou sorteada e anunciada no log.

    Nunca fixa no código: uma senha publicada no fonte é a senha de toda
    instalação que alguém esqueceu de trocar.

    Sem `MBASSETS_ADMIN_SENHA` a senha é sorteada, e aí ela PRECISA aparecer no
    log — é a única via de entrada num banco recém-criado. Fica em uma linha
    isolada e berrante de propósito: quem instala lê o log do primeiro boot.
    """
    import os
    import secrets

    do_ambiente = os.environ.get('MBASSETS_ADMIN_SENHA')
    if do_ambiente:
        return do_ambiente

    sorteada = secrets.token_urlsafe(12)
    print('=' * 70)
    print(f'[SEED] Senha do admin de bootstrap (admin@mbassets.local): {sorteada}')
    print('[SEED] Anote agora — ela nao volta a ser exibida. Troque no primeiro acesso.')
    print('=' * 70)
    return sorteada


def cria_usuario_admin():
    """Cria usuário admin padrão (TI_MASTER) se o banco estiver vazio.

    Chamada uma única vez no bootstrap da aplicação (run.py ou seed). Só age
    se não houver nenhum usuário cadastrado, então é seguro chamar em todo start.

    O guard explícito para TI_MASTER é importante: se a seed de grupos não rodou,
    não faz sentido criar um admin sem grupo — ele ficaria sem permissões e
    causaria comportamento indefinido no sistema de RBAC. Abortar com log é mais
    seguro do que criar um usuário órfão.
    """
    from app.models import Usuario, Grupo
    if not Usuario.query.first():
        grupo_ti_master = Grupo.query.filter_by(nome='TI_MASTER').first()
        if not grupo_ti_master:
            # Se chegou aqui sem o grupo TI_MASTER, a seed de permissões não rodou.
            # Não criamos admin sem grupo — seria um estado inválido no sistema.
            print("[ERRO] cria_usuario_admin: grupo TI_MASTER não encontrado. Seed não rodou corretamente.")
            return
        admin = Usuario(
            nome='Administrador TI',
            re='admin',
            senha_hash=generate_password_hash(_senha_do_admin(), method='scrypt'),
            grupo_id=grupo_ti_master.id,
            # GLOBAL: o admin de bootstrap precisa enxergar tudo. Sem isso ele cai
            # no default 'CD' sem localidade → get_filtro_localidade() devolve [] e
            # os módulos que tratam [] como "sem acesso" (coletores) mostram zero.
            nivel_acesso='GLOBAL',
            # T2 — login é por e-mail: sem um endereço de bootstrap ninguém
            # consegue entrar num banco novo.
            email='admin@mbassets.local',
            is_owner=True,
        )
        db.session.add(admin)
        db.session.commit()
        print("[INIT] Usuário admin criado com grupo TI_MASTER.")
