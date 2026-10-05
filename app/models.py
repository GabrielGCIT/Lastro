from datetime import datetime
from app import db


# ---------------------------------------------------------------------------
# CONSTANTES DE DOMÍNIO
# ---------------------------------------------------------------------------
# Ficam aqui em models.py propositalmente: são dados de domínio do sistema,
# não configuração de ambiente. O fluxo de status, as cores do badge e os
# rótulos de ação pertencem ao modelo de negócio — junto com os Models que
# os consomem. Centralizar aqui evita a tentação de duplicar esses valores
# em views, templates e scripts de relatório.

# ---------------------------------------------------------------------------
# MAPA TÉCNICO DE DIAGNÓSTICO
# ---------------------------------------------------------------------------

DIAGNOSTICO_MAPA = {
    "FISICO": [
        ("GATILHO",      "Gatilho Quebrado / Solto",       "GR15"),
        ("TELA_QUEBRADA","Tela Trincada / Quebrada",        "GR3/GR133"),
        ("TECLADO",      "Teclas Arrancadas / Falhando",    "GR14"),
        ("CARCACA",      "Carcaça Danificada / Quebrada",   "Geral"),
    ],
    "HARDWARE": [
        ("LASER",       "Laser não acende / Não lê",        "GR101/G230"),
        ("TOUCH_CALIB", "Toque não calibra / Falha",        "GR128"),
        ("BATERIA_INT", "Não carrega / Falha Energia",      "GR45"),
        ("WIFI_HW",     "Placa de Rede / Antena Queimada",  "Geral"),
    ],
    "SISTEMA": [
        ("BOOT_LOOP", "Travado na tela inicial / Logo",     "SW01"),
        ("APP_CRASH", "Aplicativo fechando sozinho",        "SW02"),
        ("WIFI_CFG",  "Wi-Fi desconectando (Config)",       "SW03"),
        ("SLOW",      "Lentidão Extrema / Travamento",      "SW04"),
    ],
}


# ---------------------------------------------------------------------------
# STATUS DO COLETOR
# ---------------------------------------------------------------------------

# Os únicos status que um coletor pode ter. As rotas que gravam status recusam
# qualquer outro valor: tirar a opção da tela não impede um POST forjado.
STATUS_COLETOR = ['Disponível', 'Em Uso', 'Manutenção', 'Suspenso']

# U2 — "indisponível" não é um status, é um GRUPO. O dashboard conta assim; a
# constante existe para o inventário filtrar exatamente o mesmo conjunto. Duas
# listas iguais em arquivos diferentes divergem na primeira vez que alguém
# acrescentar um status.
STATUS_FORA_DE_OPERACAO = ['Manutenção', 'Suspenso']

# Valor reservado do filtro `?status=` que significa o grupo acima. Minúsculo de
# propósito: os status reais são capitalizados, então não há como colidir.
STATUS_INDISPONIVEIS = 'indisponiveis'

# ---------------------------------------------------------------------------
# B2 — a aparência de cada tipo de bateria, escolhida por quem cadastra
# ---------------------------------------------------------------------------
# 🔴 A primeira versão derivava o ícone da CÂMARA, e estava errado por conceito:
# câmara é regra de COMPATIBILIDADE (onde a bateria aguenta trabalhar); ícone e
# cor são IDENTIFICAÇÃO (qual bateria é esta). Amarrar as duas fez uma bateria
# climatizada cadastrada como "serve em qualquer coletor" aparecer com o ícone da
# comum — que foi exatamente o que o Gabriel viu.
#
# LISTA BRANCA nos dois casos, e não texto livre: estes valores vão para dentro
# de `class` e de `style` no template. Aceitar qualquer string seria deixar o
# formulário escrever CSS e markup na página.
BATERIA_ICONES = [
    ('fa-battery-full',  'Bateria'),
    ('fa-snowflake',     'Floco de neve'),
    ('fa-headphones',    'Headset'),
    ('fa-bolt',          'Raio'),
    ('fa-car-battery',   'Bateria grande'),
    ('fa-plug',          'Tomada'),
    ('fa-microchip',     'Chip'),
    ('fa-cube',          'Cubo'),
]
ICONE_BATERIA_PADRAO = 'fa-battery-full'

# (chave, rótulo, fundo, borda, texto) — as cores saem daqui prontas, nunca do
# formulário, pelo mesmo motivo da lista de ícones.
BATERIA_CORES = [
    ('ambar',   'Âmbar',    '#fbebd8', '#eccca0', '#8a4b0a'),
    ('azul',    'Azul',     '#d9f0f7', '#9fd7e6', '#0b6e85'),
    ('verde',   'Verde',    '#ddefe5', '#a3d3bb', '#1f6b45'),
    ('roxo',    'Roxo',     '#e9e2f8', '#c3b1e8', '#5b3ea8'),
    ('vermelho','Vermelho', '#fbe4e4', '#efb4b4', '#a81e20'),
    ('cinza',   'Cinza',    '#eceff1', '#c6ced2', '#44555e'),
]
COR_BATERIA_PADRAO = 'ambar'


def cor_de_bateria(chave):
    """(fundo, borda, texto) da cor escolhida, com queda para o padrão.

    Nunca levanta: um valor estranho no banco (importação, edição manual) não
    pode derrubar a tela de estoque inteira por causa de um enfeite.
    """
    for ch, _rot, fundo, borda, texto in BATERIA_CORES:
        if ch == chave:
            return fundo, borda, texto
    return BATERIA_CORES[0][2], BATERIA_CORES[0][3], BATERIA_CORES[0][4]

CAMARA_CHOICES = ['SECO', 'RESFRIADO', 'CONGELADO']
TURNO_CHOICES  = ['T1', 'T2', 'T3']

# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# EMPRESA
# ---------------------------------------------------------------------------

class Empresa(db.Model):
    """A empresa dona dos dados — uma só nesta instalação.

    O `empresa_id` continua nas tabelas e em todas as leituras (criterio_empresa,
    get_filtro_localidade). Com uma empresa só ele é invisível; arrancá-lo seria
    mexer no filtro que governa todas as consultas sem ganho para quem usa.

    ativa=False suspende o acesso sem apagar dados.
    """
    __tablename__ = 'empresas'
    __table_args__ = (
        db.UniqueConstraint('nome', name='uq_empresas_nome'),
    )
    id            = db.Column(db.Integer,     primary_key=True)
    nome          = db.Column(db.String(150), nullable=False)
    ativa         = db.Column(db.Boolean,     nullable=False, default=True, server_default='1')
    data_cadastro = db.Column(db.DateTime,    default=datetime.now)


# Registry de POLÍTICAS: cada chave mapeia (default, tipo_python) para o cast e o
# fallback do helper politica_empresa() (app/helpers.py) quando a empresa não tem
# linha própria em EmpresaPolitica.
POLITICAS_PLATAFORMA = {
    # H5 — horas que um complemento pode ficar em campo antes de virar cobrança.
    # 24 porque o turno tem ~9h mas medir por turno é frágil (vira, emenda,
    # atravessa a meia-noite); 1 dia é a régua escolhida. Política e não
    # constante: muda sem mexer no código.
    'complemento_prazo_horas': ('24', int),
}


class EmpresaPolitica(db.Model):
    """Uma política configurável da empresa — par chave/valor.

    valor é sempre String — o cast pro tipo declarado em POLITICAS_PLATAFORMA
    acontece no helper politica_empresa(), nunca aqui (a coluna não sabe nem
    precisa saber o tipo de cada chave).
    """
    __tablename__ = 'empresa_politicas'
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'chave', name='uq_empresa_politicas_empresa_chave'),
    )
    id         = db.Column(db.Integer,     primary_key=True)
    empresa_id = db.Column(db.Integer,     db.ForeignKey('empresas.id'), nullable=False)
    chave      = db.Column(db.String(50),  nullable=False)
    valor      = db.Column(db.String(100), nullable=False)


# ---------------------------------------------------------------------------
# COMPLEMENTOS DO HANDHELD (pacote H) — baterias, headsets, suportes
# ---------------------------------------------------------------------------
# Nasce de três dores que convergem numa peça só: contar baterias que saem e
# voltam, cadastrar categorias de periférico e cobrar a devolução item a item.
# A bateria NÃO é caso especial — é o primeiro item do catálogo (D3), o que evita
# um segundo mecanismo quando o headset e o suporte chegarem.

# Como o item é contado (D4). Dois mundos que a mesma tabela de movimento atende:
# bateria é fungível (levou 2, voltou 1); headset é peça identificada e pareada.
COMPLEMENTO_QUANTIDADE = 'QUANTIDADE'
COMPLEMENTO_UNIDADE    = 'UNIDADE'
COMPLEMENTO_CONTROLES  = [COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE]

# Restrição térmica do complemento (D5). A regra do armazém é ASSIMÉTRICA: a
# bateria de climatizado (resfriado/congelado) tem cor diferente e serve TAMBÉM no
# seco; a do seco só serve no seco. NULL = sem restrição térmica (um suporte
# plástico não se importa com a câmara).
COMPLEMENTO_CAMARA_SECO        = 'SECO'
COMPLEMENTO_CAMARA_CLIMATIZADO = 'CLIMATIZADO'
COMPLEMENTO_CAMARAS            = [COMPLEMENTO_CAMARA_SECO, COMPLEMENTO_CAMARA_CLIMATIZADO]

# De QUAL equipamento a peça é. O sistema tinha uma única dimensão de
# compatibilidade — frio × seco — e nenhuma para o equipamento, então marcar um
# tipo como bateria significava literalmente "ofereça isso quando alguém retirar
# um COLETOR". Quem cadastrasse "BATERIA HEADSET" via o balcão sugeri-la para o
# coletor de pista, e não havia campo nenhum para dizer que não era daquilo.
#
# NULL = coletor: é o que todo tipo já cadastrado é, e assim nenhuma linha
# existente precisa ser atualizada para o comportamento continuar o mesmo.
EQUIPAMENTO_COLETOR = 'COLETOR'
EQUIPAMENTO_OUTRO   = 'OUTRO'
EQUIPAMENTOS        = [EQUIPAMENTO_COLETOR, EQUIPAMENTO_OUTRO]


class CategoriaComplemento(db.Model):
    """Tipo de complemento que a empresa controla — o catálogo do pacote H.

    Cadastrável por empresa, desativável sem apagar histórico, e responde às duas
    perguntas que o handheld faz: COMO se conta (`controle`) e ONDE pode ser
    usado (`camara_atendida`).

    Exemplos reais: "Bateria Coletor Climatizado" (QUANTIDADE, CLIMATIZADO),
    "Bateria Coletor Seco" (QUANTIDADE, SECO), "Bateria Headset" (QUANTIDADE,
    câmara a definir — D9), "Headset" (UNIDADE), "Suporte" (UNIDADE).

    Desativar NÃO afeta o que já saiu: movimentações antigas seguem apontando para
    a categoria e a pendência continua cobrável.
    """
    __tablename__ = 'categorias_complemento'
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'nome', name='uq_categorias_complemento_empresa_nome'),
    )
    id         = db.Column(db.Integer,    primary_key=True)
    empresa_id = db.Column(db.Integer,    db.ForeignKey('empresas.id'), nullable=False)
    nome       = db.Column(db.String(40), nullable=False)
    controle   = db.Column(db.String(12), nullable=False, default=COMPLEMENTO_QUANTIDADE,
                           server_default=COMPLEMENTO_QUANTIDADE)
    # NULL = sem restrição térmica. Ver COMPLEMENTO_CAMARA_* e `atende_camara`.
    camara_atendida = db.Column(db.String(15), nullable=True)
    ativa      = db.Column(db.Boolean,    nullable=False, default=True, server_default='1')
    # B1 — bateria não é um complemento como os outros: é FUNGÍVEL, some o tempo
    # todo e ninguém quer cadastrá-la uma a uma. Tratá-la junto com headset e
    # suporte foi o que obrigou a tela a perguntar "como você controla essas
    # peças?" — uma decisão de modelagem empurrada para quem opera o CD.
    #
    # A marca separa as DUAS TELAS sem tocar no domínio: o cálculo de saldo, o
    # que está em campo e o atraso continuam sendo os mesmos da H5, já testados
    # e já em produção. Trocar o modelo por causa de apresentação seria jogar
    # fora código que funciona.
    e_bateria  = db.Column(db.Boolean,    nullable=False, default=False,
                           server_default='0')
    # Aparência do tipo na tela de baterias. Escolhida por quem cadastra, porque
    # só ele sabe qual bateria é qual na prateleira — e não derivada da câmara,
    # que é regra de compatibilidade e não de identificação.
    icone      = db.Column(db.String(40), nullable=True)
    cor        = db.Column(db.String(20), nullable=True)
    # NULL = COLETOR. Ver EQUIPAMENTO_* e `serve_coletor`.
    equipamento = db.Column(db.String(20), nullable=True)

    @property
    def serve_coletor(self):
        """Esta peça é oferecida quando alguém retira um COLETOR?

        Separado de `atende_camara` de propósito: são perguntas diferentes. A
        câmara diz se a peça AGUENTA o ambiente; esta diz de que equipamento ela
        é. Uma bateria de headset pode aguentar o congelado e mesmo assim não
        ter nada que ver com o coletor.
        """
        return self.equipamento in (None, EQUIPAMENTO_COLETOR)

    def atende_camara(self, camara):
        """Diz se este complemento pode ser usado num equipamento daquela câmara.

        🔴 A assimetria do D5 mora AQUI, num lugar só — é a regra que, solta na
        cabeça do operador, vira bateria morrendo no congelado às 3h da manhã:

            sem restrição  → serve em qualquer lugar
            CLIMATIZADO    → serve em qualquer lugar (é a bateria "boa")
            SECO           → serve APENAS no seco

        `camara` None (equipamento sem câmara declarada) conta como seco: é o caso
        menos exigente, e recusar por ausência de informação travaria a retirada de
        todo coletor que ainda não teve a câmara preenchida.
        """
        if not self.camara_atendida:
            return True
        if self.camara_atendida == COMPLEMENTO_CAMARA_CLIMATIZADO:
            return True
        return camara in (None, COMPLEMENTO_CAMARA_SECO)

    @property
    def por_unidade(self):
        return self.controle == COMPLEMENTO_UNIDADE


class ItemComplementar(db.Model):
    """Uma peça IDENTIFICADA do catálogo — o headset nº 47, o suporte tal.

    Só existe para categorias de controle UNIDADE. Bateria não vira linha aqui:
    ela é contada, não identificada.

    `coletor_id` é o PAREAMENTO do D2: no voice picking o headset trabalha com UM
    coletor específico. Guardar o par é o que permite ao sistema saber qual headset
    deveria voltar — e perceber que voltou outro. Nullable porque nem todo item é
    pareado (um suporte avulso não é) e porque o par pode ser desfeito.
    """
    __tablename__ = 'itens_complementares'
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'identificador',
                            name='uq_itens_complementares_empresa_identificador'),
    )
    id            = db.Column(db.Integer,    primary_key=True)
    empresa_id    = db.Column(db.Integer,    db.ForeignKey('empresas.id'), nullable=False)
    categoria_id  = db.Column(db.Integer,    db.ForeignKey('categorias_complemento.id'),
                              nullable=False)
    identificador = db.Column(db.String(60), nullable=False)   # patrimônio/serial da peça
    # D2 — o coletor com que esta peça faz par (voice picking).
    coletor_id    = db.Column(db.Integer,    db.ForeignKey('coletores.id'), nullable=True)
    # Escopo geográfico, como Coletor: get_filtro_localidade é fail-closed (T3).
    localidade_id = db.Column(db.Integer,    db.ForeignKey('localidades.id'), nullable=True)
    ativo         = db.Column(db.Boolean,    nullable=False, default=True, server_default='1')

    categoria = db.relationship('CategoriaComplemento',
                                backref=db.backref('itens', lazy=True))
    coletor   = db.relationship('Coletor',
                                backref=db.backref('complementos_pareados', lazy=True))
    # H5 — o rastreio precisa dizer em QUAL CD a peça está parada, e sem esta
    # relação isso viraria uma consulta por item. Relação não é coluna: zero DDL.
    localidade = db.relationship('Localidade',
                                 backref=db.backref('itens_complementares', lazy=True))


class EstoqueComplemento(db.Model):
    """Quantas peças de um tipo existem NUM CD (D7).

    Deliberadamente magro: guarda só o total cadastrado por (categoria,
    localidade). O DISPONÍVEL é derivado — total menos o que está em campo, somado
    das movimentações não devolvidas. Estado derivado não dessincroniza, mesma
    doutrina da pendência.

    ⚠️ Isto NÃO é um sistema de estoque: não há entrada, saída, ajuste nem
    contagem. Essas peças transformariam a fatia num projeto próprio, e o pedido
    foi "cadastrar quantas baterias temos para cada setor".
    """
    __tablename__ = 'estoques_complemento'
    __table_args__ = (
        db.UniqueConstraint('categoria_id', 'localidade_id',
                            name='uq_estoques_complemento_categoria_localidade'),
    )
    id             = db.Column(db.Integer, primary_key=True)
    empresa_id     = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    categoria_id   = db.Column(db.Integer, db.ForeignKey('categorias_complemento.id'),
                               nullable=False)
    localidade_id  = db.Column(db.Integer, db.ForeignKey('localidades.id'), nullable=False)
    qtd_cadastrada = db.Column(db.Integer, nullable=False, default=0, server_default='0')

    categoria  = db.relationship('CategoriaComplemento',
                                 backref=db.backref('estoques', lazy=True))
    localidade = db.relationship('Localidade', lazy='select')


class MovimentacaoComplemento(db.Model):
    """O que saiu junto com o coletor, e o que voltou.

    Uma linha por complemento de cada movimentação. Cobre os dois mundos do D4
    numa tabela só:

        bateria → item_id NULL, qtd_saida=2, qtd_devolvida=1   (fungível)
        headset → item_id=47,   qtd_saida=1, qtd_devolvida=0   (identificado)

    🔴 NÃO existe coluna "pendente". A falta é DERIVADA (`qtd_devolvida <
    qtd_saida`) porque estado duplicado dessincroniza: bastaria uma devolução
    parcial corrigida depois para a flag mentir enquanto os números dizem a
    verdade. Mesma escolha do saldo de estoque.
    """
    __tablename__ = 'movimentacoes_complemento'
    id              = db.Column(db.Integer, primary_key=True)
    movimentacao_id = db.Column(db.Integer, db.ForeignKey('movimentacoes.id'), nullable=False)
    categoria_id    = db.Column(db.Integer, db.ForeignKey('categorias_complemento.id'),
                                nullable=False)
    # Preenchido só para controle UNIDADE — qual peça exatamente saiu.
    item_id         = db.Column(db.Integer, db.ForeignKey('itens_complementares.id'),
                                nullable=True)
    qtd_saida       = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    qtd_devolvida   = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    devolvido_em    = db.Column(db.DateTime, nullable=True)
    # D6 — dispensa auditada do portão de compatibilidade: quem liberou a exceção
    # e por quê.
    dispensa_motivo = db.Column(db.String(200), nullable=True)
    dispensa_por    = db.Column(db.String(100), nullable=True)

    movimentacao = db.relationship('Movimentacao',
                                   backref=db.backref('complementos', lazy=True,
                                                      cascade='all, delete-orphan'))
    categoria    = db.relationship('CategoriaComplemento', lazy='select')
    item         = db.relationship('ItemComplementar', lazy='select')

    @property
    def faltando(self):
        """Quantas peças não voltaram. 0 = quitado."""
        return max((self.qtd_saida or 0) - (self.qtd_devolvida or 0), 0)

    @property
    def pendente(self):
        return self.faltando > 0


# ---------------------------------------------------------------------------
# SISTEMA DE GRUPOS E PERMISSÕES (RBAC)
# ---------------------------------------------------------------------------

# Tabela associativa pura — muitos-para-muitos entre Grupo e Permissao.
# Não é um Model completo (classe Python) porque não há campos extras além
# das chaves estrangeiras. db.Table é o jeito certo do SQLAlchemy para
# tabelas de junção simples: menos boilerplate, e o ORM gerencia os inserts
# automaticamente via relationship().
grupo_permissoes = db.Table(
    'grupo_permissoes',
    db.Column('grupo_id',     db.Integer, db.ForeignKey('grupos.id'),     primary_key=True),
    db.Column('permissao_id', db.Integer, db.ForeignKey('permissoes.id'), primary_key=True),
)


class Grupo(db.Model):
    """Perfil de acesso — agrupa um conjunto de permissões atribuíveis a usuários."""
    __tablename__ = 'grupos'
    # T1 — nome único POR empresa (cada tenant tem seu próprio TI_MASTER, GERENTE…).
    # empresa_id nullable para retrocompatibilidade; backfill no seed_empresa().
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'nome', name='uq_grupos_empresa_nome'),
    )
    id         = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    nome       = db.Column(db.String(30),  nullable=False)   # 'TI_MASTER', 'GERENTE'…
    descricao  = db.Column(db.String(200))
    protegido  = db.Column(db.Boolean, default=False, nullable=False)     # TI_MASTER não editável pela UI

    # lazy='select' carrega as permissões sob demanda (query separada quando acessado).
    # Para listas pequenas de permissões como essa, é o comportamento adequado —
    # não há custo de N+1 porque o acesso acontece uma vez por request no _sync_permissoes.
    permissoes = db.relationship('Permissao', secondary=grupo_permissoes, lazy='select',
                                 backref=db.backref('grupos', lazy='select'))
    usuarios   = db.relationship('Usuario', backref='grupo', lazy=True)


class Permissao(db.Model):
    """Unidade atômica de acesso no sistema RBAC.

    O campo `codigo` é o identificador canônico usado nos decorators
    (@permissao_required) e na verificação de sessão (has_permissao).
    O campo `modulo` serve apenas para agrupamento visual na tela de admin.
    """
    __tablename__ = 'permissoes'
    id       = db.Column(db.Integer, primary_key=True)
    codigo   = db.Column(db.String(50),  unique=True, nullable=False)   # 'lote.criar'
    descricao= db.Column(db.String(150))
    modulo   = db.Column(db.String(30))  # 'operacional' | 'manutencao' | 'relatorios' | 'admin'


class Usuario(db.Model):
    """Representa um operador do sistema — pode ser criado pela TI ou via API mobile."""
    __tablename__ = 'usuarios'
    # T1 — RE vira matrícula interna, única POR empresa (o login global passa a
    # ser por e-mail no T2). empresa_id nullable; backfill no seed_empresa().
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 're', name='uq_usuarios_empresa_re'),
    )
    id          = db.Column(db.Integer, primary_key=True)
    empresa_id  = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    nome        = db.Column(db.String(100), nullable=False)
    re          = db.Column(db.String(50), nullable=False)
    senha_hash  = db.Column(db.String(255), nullable=False)
    grupo_id    = db.Column(db.Integer, db.ForeignKey('grupos.id'), nullable=True)
    localidade_id = db.Column(db.Integer, db.ForeignKey('localidades.id'), nullable=True)
    localidade    = db.relationship('Localidade', foreign_keys=[localidade_id], lazy='select')
    # T2 — e-mail é a credencial de login (único GLOBAL, validado app-level como
    # numero_serie: constraint de banco conflitaria com múltiplos NULLs no SQL
    # Server, e quem não loga — OPERADOR — não precisa de e-mail). Sempre
    # normalizado (lower/strip) no save; o lookup do login usa lower() p/ cobrir
    # valores legados gravados antes da normalização.
    email       = db.Column(db.String(120), nullable=True)
    telefone    = db.Column(db.String(30),  nullable=True)
    foto_perfil      = db.Column(db.String(255), nullable=True)
    foto_cracha      = db.Column(db.String(255), nullable=True)
    # Flag que indica usuários cadastrados pelo app mobile (coletores de campo),
    # sem passar pelo fluxo de aprovação da TI. Serve como sinalizador para revisão
    # posterior: a TI precisa confirmar o grupo e as permissões desses usuários.
    criado_em_campo  = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    # P2 — Câmara e turno operacional do colaborador
    camara           = db.Column(db.String(15), nullable=True)  # SECO | RESFRIADO | CONGELADO
    turno            = db.Column(db.String(5),  nullable=True)  # T1 | T2 | T3
    # P5 — Multi-tenancy: escopo de acesso geográfico
    # nivel_acesso determina o teto de visibilidade; os FKs abaixo definem o escopo
    # dentro desse teto. Para CD, usa localidade_id (já existente). Para GLOBAL, sem FK.
    nivel_acesso     = db.Column(db.String(10), default='CD', nullable=False, server_default="'CD'")  # CD | PAIS | AMERICA | GLOBAL
    america_id       = db.Column(db.Integer, db.ForeignKey('americas.id'), nullable=True)
    pais_id          = db.Column(db.Integer, db.ForeignKey('paises.id'),   nullable=True)
    idioma           = db.Column(db.String(5), default='pt', nullable=False, server_default="'pt'")   # pt | en | es
    # UX Tokens — preferências visuais do usuário (tema + cor de acento)
    # tema: light | dark | auto (auto segue prefers-color-scheme do SO). Nullable → default light.
    # cor_acento: hex #RRGGBB de uma paleta CURADA (contraste AA garantido). Nullable → verde da marca.
    tema             = db.Column(db.String(10), nullable=True)  # light | dark | auto
    cor_acento       = db.Column(db.String(7),  nullable=True)  # #RRGGBB
    # T2 — Identidade multi-empresa
    # is_owner: dono da PLATAFORMA, acima das empresas (CRUD de tenants,
    # entitlements, impersonation auditada no T4). Não confundir com TI_MASTER,
    # que é o admin DE UMA empresa. Owner ignora o bloqueio de empresa inativa.
    is_owner              = db.Column(db.Boolean, nullable=False, default=False, server_default='0')
    # Reset de senha: o token enviado por e-mail NUNCA é gravado em claro —
    # só o sha256 (vazamento do banco não vira account takeover). Uso único:
    # os dois campos são limpos ao redefinir a senha.
    reset_token_hash      = db.Column(db.String(64), nullable=True)
    reset_token_expira    = db.Column(db.DateTime,   nullable=True)
    # Lockout de brute force: contador de falhas dentro da janela temporal +
    # instante do bloqueio. Zerados no login bem-sucedido e no reset de senha.
    login_tentativas        = db.Column(db.Integer,  nullable=False, default=0, server_default='0')
    login_ultima_tentativa  = db.Column(db.DateTime, nullable=True)
    login_bloqueado_ate     = db.Column(db.DateTime, nullable=True)
    # Relationships de escopo geográfico — usados na tela de gestão de usuários
    pais             = db.relationship('Pais',    foreign_keys=[pais_id],    lazy='select')
    america          = db.relationship('America', foreign_keys=[america_id], lazy='select')
    # T2 — empresa do usuário: carregada na sessão no login e usada no bloqueio
    # de tenant inativo (joinedload no _sync_permissoes evita query extra).
    empresa          = db.relationship('Empresa', foreign_keys=[empresa_id], lazy='select')


class LogAuditoria(db.Model):
    """Trilha de auditoria imutável — registra ações relevantes de usuários no sistema.

    Gravado via helpers.registrar_log(), que silencia falhas para não interferir
    no fluxo de negócio. O campo `usuario` armazena o nome (não o ID) para que
    o log continue legível mesmo se o usuário for removido do sistema.
    """
    __tablename__ = 'logs_auditoria'
    id         = db.Column(db.Integer, primary_key=True)
    # T1 — empresa a que o log pertence (empresa vê o seu, Owner vê tudo — T3).
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    data_hora  = db.Column(db.DateTime, default=datetime.now)
    usuario   = db.Column(db.String(100))
    user_re   = db.Column(db.String(50))   # RE único — identifica o autor sem ambiguidade
    acao      = db.Column(db.String(50))
    detalhe   = db.Column(db.Text)


# ---------------------------------------------------------------------------
# HIERARQUIA GEOGRÁFICA (P5 — Multi-tenancy)
# ---------------------------------------------------------------------------

class America(db.Model):
    """Agrupamento continental — nível mais alto da hierarquia geográfica.

    SA (South America) | CA (Central America) | NA (North America).
    Porto Rico (PR) é tratado como CA por decisão organizacional interna da MB,
    não por localização geográfica — hardcoded no seed e não deve ser questionado.
    """
    __tablename__ = 'americas'
    id    = db.Column(db.Integer, primary_key=True)
    sigla = db.Column(db.String(5),  unique=True, nullable=False)  # SA | CA | NA
    nome  = db.Column(db.String(50), nullable=False)

    paises = db.relationship('Pais', backref='america', lazy=True)


class Pais(db.Model):
    """País dentro de uma América — agrupa CDs (Localidades) sob o mesmo país."""
    __tablename__ = 'paises'
    id         = db.Column(db.Integer, primary_key=True)
    sigla      = db.Column(db.String(5),  unique=True, nullable=False)  # BR | PA | CR | PR | US
    nome       = db.Column(db.String(50), nullable=False)
    america_id = db.Column(db.Integer, db.ForeignKey('americas.id'), nullable=False)

    localidades = db.relationship('Localidade', backref='pais', lazy=True)


class Localidade(db.Model):
    """Representa uma unidade física (CD, filial) que possui coletores sob sua responsabilidade."""
    __tablename__ = 'localidades'
    # P8a — sigla única por país, não globalmente. Permite o mesmo código de CD em
    # países diferentes (ex: GR existe em BR-GR e pode existir em CR-GR).
    # T1 — a unicidade ganha a empresa: dois tenants podem ter BR-GR sem conflito.
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'sigla', 'pais_id',
                            name='uq_localidade_empresa_sigla_pais'),
    )
    id         = db.Column(db.Integer, primary_key=True)
    # T1 — âncora do multi-tenancy: todo o operacional herda o isolamento daqui.
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    sigla      = db.Column(db.String(10), nullable=False)   # GR, EC, JC...
    nome      = db.Column(db.String(100), nullable=False)
    descricao = db.Column(db.String(200))
    # P5 — vínculo com o País ao qual este CD pertence (nullable para retrocompatibilidade)
    pais_id   = db.Column(db.Integer, db.ForeignKey('paises.id'), nullable=True)
    coletores = db.relationship('Coletor', backref='localidade', lazy=True)

    @property
    def sigla_completa(self):
        """Retorna a sigla composta no formato PAIS-CD (ex: BR-GR, CR-SJO, US-RSM).

        Usado em todas as telas que hoje exibem só a sigla simples — a property
        garante ponto único de formatação sem duplicar lógica em template/rota.
        Retorna só a sigla simples se o País ainda não estiver vinculado (legado).
        """
        if self.pais:
            return f'{self.pais.sigla}-{self.sigla}'
        return self.sigla


class Coletor(db.Model):
    """Equipamento coletor de dados (scanner) rastreado individualmente pelo sistema."""
    __tablename__ = 'coletores'
    id                = db.Column(db.Integer, primary_key=True)
    serial_number     = db.Column(db.String(100), unique=True, nullable=False)
    numero_identificacao = db.Column(db.String(50))
    modelo            = db.Column(db.String(50), default='MC9190G')
    status            = db.Column(db.String(20), default='Disponível')
    categoria_defeito = db.Column(db.String(20))
    detalhe_defeito   = db.Column(db.String(50))
    descricao_problema= db.Column(db.Text)
    re_colaborador    = db.Column(db.String(50))
    hora_saida        = db.Column(db.DateTime)

    # Fase 2 — identificação patrimonial por localidade
    numero_patrimonio = db.Column(db.String(50))
    localidade_id     = db.Column(db.Integer, db.ForeignKey('localidades.id'), nullable=True)
    tipo_alcance      = db.Column(db.String(10))   # CURTA | LONGA
    camara            = db.Column(db.String(15), nullable=True)  # SECO | RESFRIADO | CONGELADO

    # P4b — garantia operacional (fica no equipamento, não na NF)
    tem_garantia      = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    validade_garantia = db.Column(db.Date, nullable=True)
    # B6 — DESATIVAÇÃO. Três colunas e não quatro: `desativado_em` nulo já
    # significa ativo, e é também o "quando". Uma booleana `ativo` ao lado seria
    # um segundo estado dizendo a mesma coisa — e dois campos que precisam
    # concordar entre si acabam discordando (o saldo da H5 nasceu assim).
    #
    # O item desativado NÃO some do banco nem do histórico: ele sai das telas de
    # operação e não pode mais ser movimentado. Desativar é reversível; excluir,
    # que é irreversível, exige senha e só vale para item sem histórico.
    desativado_em     = db.Column(db.DateTime, nullable=True)
    desativado_por_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    desativado_motivo = db.Column(db.String(200), nullable=True)
    # `foreign_keys` explícito: as duas classes têm mais de um caminho possível
    # até `usuarios`, e sem isto o SQLAlchemy recusa a relação por ambiguidade.
    desativado_por    = db.relationship('Usuario', foreign_keys=[desativado_por_id],
                                        lazy='select')

    @property
    def esta_ativo(self):
        """Estado DERIVADO — ver a nota nas colunas acima.

        `esta_ativo` e não `ativo`: a pergunta é sobre a desativação, e um
        `coletor.ativo` solto não diz isso.
        """
        return self.desativado_em is None


    @property
    def patrimonio_display(self):
        """Formata o patrimônio para exibição visual, prefixando a sigla da localidade.

        Retorna strings como 'GR 253' em vez de só '253', tornando o número
        imediatamente identificável na interface sem precisar de coluna extra.
        O '—' como fallback evita células vazias nas tabelas de listagem.
        """
        sigla = self.localidade.sigla if self.localidade else ''
        if sigla and self.numero_patrimonio:
            return f'{sigla} {self.numero_patrimonio}'
        return self.numero_patrimonio or '—'

    @property
    def rotulo(self):
        """Como CHAMAR este coletor numa FRASE — nunca devolve traço.

        `patrimonio_display` foi feito para CÉLULA DE TABELA: o '—' existe para
        a coluna não ficar vazia, e a coluna de serial ao lado identifica a
        linha. Numa frase não há coluna vizinha, e o balcão dizia "SUCESSO:
        Coletor — liberado para João da Silva" — o operador fica sem saber qual
        equipamento saiu, na hora em que a fila anda.

        O patrimônio é opcional (equipamento novo ainda não etiquetado), então a
        frase cai para o serial, e só em último caso para o id.
        """
        if self.numero_patrimonio:
            return self.patrimonio_display
        return self.serial_number or f'#{self.id}'


class Movimentacao(db.Model):
    """Registra a saída e devolução de um coletor para um colaborador operacional.

    Cada movimentação é um empréstimo: o coletor sai com um colaborador (re_colaborador)
    e deve retornar com a identificação intacta. Se a identificação foi violada, a foto de devolução
    se torna obrigatória para fins de responsabilização.

    data_retorno nulo indica que o coletor ainda está em campo com o colaborador.

    colaborador_id é o FK para o Colaborador — a retirada só é aceita para um RE
    cadastrado e ativo. Nullable para retrocompatibilidade com movimentações
    anteriores ao vínculo, que só tinham re_colaborador (texto livre).
    """
    __tablename__ = 'movimentacoes'
    id                   = db.Column(db.Integer, primary_key=True)
    coletor_id           = db.Column(db.Integer, db.ForeignKey('coletores.id'), nullable=False)
    re_colaborador       = db.Column(db.String(50), nullable=False)
    colaborador_id       = db.Column(db.Integer, db.ForeignKey('colaboradores.id'), nullable=True)
    data_saida           = db.Column(db.DateTime, default=datetime.now)
    data_retorno         = db.Column(db.DateTime)
    status_identificacao_retorno = db.Column(db.String(50))

    # Fase 4 — evidência fotográfica da movimentação
    foto_retirada  = db.Column(db.String(200))   # opcional na saída
    foto_devolucao = db.Column(db.String(200))   # obrigatória quando identificação violada

    coletor     = db.relationship('Coletor', backref=db.backref('movimentacoes', lazy=True))
    colaborador = db.relationship('Colaborador', backref=db.backref('movimentacoes_coletores', lazy=True))


class ChecklistEntrega(db.Model):
    """Checklist obrigatório preenchido pelo analista na retirada de cada coletor.

    Vinculado a uma Movimentacao específica — registra que o analista verificou
    fisicamente o equipamento antes de entregá-lo ao colaborador. Os 5 itens
    são todos obrigatórios (nullable=False) e só chegam ao banco como True porque
    a interface bloqueia o submit até todos estarem marcados, e o backend valida
    novamente antes de persistir.
    """
    __tablename__ = 'checklists_entrega'
    id              = db.Column(db.Integer, primary_key=True)
    movimentacao_id = db.Column(db.Integer, db.ForeignKey('movimentacoes.id'), nullable=False)
    coletor_id      = db.Column(db.Integer, db.ForeignKey('coletores.id'), nullable=False)
    analista_re     = db.Column(db.String(50),  nullable=False)
    analista_nome   = db.Column(db.String(100), nullable=True)
    data_hora       = db.Column(db.DateTime, default=datetime.now)
    # 5 itens de verificação física — todos obrigatórios
    estado_visual     = db.Column(db.Boolean, nullable=False)  # Sem danos físicos visíveis
    bateria_ok        = db.Column(db.Boolean, nullable=False)  # Bateria carregada/funcionando
    leitura_ok        = db.Column(db.Boolean, nullable=False)  # Leitura de barras funcionando
    identificacao_ok  = db.Column(db.Boolean, nullable=False)  # Identificação intacta e número confere
    equipamento_limpo = db.Column(db.Boolean, nullable=False)  # Equipamento limpo para entrega

    movimentacao = db.relationship('Movimentacao', backref=db.backref('checklist', uselist=False))
    coletor      = db.relationship('Coletor', backref='checklists')


class GrupoDeletado(db.Model):
    """Registro permanente de grupos padrão excluídos intencionalmente via UI.

    O seed de boot consulta esta tabela antes de recriar grupos ausentes —
    se o nome estiver aqui, o grupo foi deletado conscientemente e não volta.
    Isso permite que o DB seja a fonte de verdade para quais grupos existem,
    sem depender do código para recriá-los a cada reinicialização.

    Somente grupos que existem em GRUPOS_DEFAULTS são registrados aqui —
    grupos criados manualmente pela UI simplesmente somem quando deletados,
    sem precisar de rastreamento porque o seed nunca tentaria recriá-los.

    T4a — a deleção passa a ser POR EMPRESA: a PK deixa de ser o `nome` global
    (que fazia o tenant B, ao excluir GERENTE, matar o seed do GERENTE de TODOS
    os tenants) e vira um id surrogate, com o par (empresa_id, nome) único. O
    registro legado (empresa_id NULL) é migrado para a MB no boot. empresa_id
    nullable só para o instante do bootstrap (grupos órfãos vinculados à MB pelo
    seed_empresa) — em produção todo registro é escopado.
    """
    __tablename__ = 'grupos_deletados'
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'nome', name='uq_grupos_deletados_empresa_nome'),
    )
    id           = db.Column(db.Integer, primary_key=True)
    empresa_id   = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    nome         = db.Column(db.String(30), nullable=False)
    deletado_por = db.Column(db.String(100))
    data_delecao = db.Column(db.DateTime, default=datetime.now)


class ReativacaoIdentificacao(db.Model):
    """Registra o processo de reativação de um coletor suspenso por violação de identificação.

    Fluxo de dois fatores: o TI que instala a identificação cria o registro (PENDENTE_APROVACAO);
    um segundo membro da TI — diferente do primeiro — aprova e libera o coletor.
    Isso garante que nenhum TI possa auto-aprovar uma reativação que ele mesmo executou,
    criando rastreabilidade e responsabilidade dupla para essa operação sensível.
    """
    __tablename__ = 'reativacoes_identificacao'
    id                  = db.Column(db.Integer, primary_key=True)
    coletor_id          = db.Column(db.Integer, db.ForeignKey('coletores.id'), nullable=False)
    # Quem instalou a identificação e iniciou a reativação
    reativado_por_re    = db.Column(db.String(50),  nullable=False)
    reativado_por_nome  = db.Column(db.String(100))
    data_reativacao     = db.Column(db.DateTime, default=datetime.now)
    foto_identificacao  = db.Column(db.String(255), nullable=False)  # evidência obrigatória
    declaracao_aceita   = db.Column(db.Boolean,     nullable=False, default=False)
    status              = db.Column(db.String(30),  default='PENDENTE_APROVACAO')
    # Preenchidos na aprovação pelo segundo TI
    aprovado_por_re     = db.Column(db.String(50))
    aprovado_por_nome   = db.Column(db.String(100))
    data_aprovacao      = db.Column(db.DateTime)

    coletor = db.relationship('Coletor', backref=db.backref('reativacoes_identificacao', lazy=True))


# ---------------------------------------------------------------------------
# COLABORADORES
# ---------------------------------------------------------------------------

class Colaborador(db.Model):
    """Colaborador operacional — recebe equipamentos mas não loga no sistema.

    Distingue-se do Usuario (staff de TI) por não ter credenciais de acesso.
    Identifica quem está com o coletor.

    administrativo=True indica cargo fora da operação de câmara —
    nesse caso camara e turno não se aplicam e ficam desabilitados na UI.
    """
    __tablename__ = 'colaboradores'
    # T1 — RE único POR empresa. empresa_id nullable; backfill no seed_empresa().
    __table_args__ = (
        db.UniqueConstraint('empresa_id', 're', name='uq_colaboradores_empresa_re'),
    )
    id             = db.Column(db.Integer,    primary_key=True)
    empresa_id     = db.Column(db.Integer,    db.ForeignKey('empresas.id'), nullable=True)
    re             = db.Column(db.String(20),  nullable=False)
    nome           = db.Column(db.String(100), nullable=False)
    cargo          = db.Column(db.String(50),  nullable=True)
    departamento   = db.Column(db.String(50),  nullable=True)
    administrativo = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    camara         = db.Column(db.String(20),  nullable=True)   # SECO | RESFRIADO | CONGELADO — n/a se administrativo
    turno          = db.Column(db.String(5),   nullable=True)   # T1 | T2 | T3 — n/a se administrativo
    ativo          = db.Column(db.Boolean, default=True, nullable=False, server_default='1')
