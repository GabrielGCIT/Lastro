"""
Inicialização do banco: schema e seeds.

O banco do MBAssets sempre nasce novo — não existe base legada para migrar. Por
isso o schema inteiro vem do `db.create_all()` (models.py é a fonte da verdade,
com constraints nomeadas lá) e este arquivo cuida só dos seeds.

Ordem de execução dentro do app_context (ver executar_startup):
  1. db.create_all()
  2. seed_grupos_e_permissoes()
  3. seed_geografico()
  4. cria_usuario_admin()   ← em helpers.py
  5. seed_empresa()         ← empresa da instalação + vínculo do que nasceu órfão
  6. seed_owner()           ← Owner com e-mail de bootstrap

⚠️  Mudança de schema depois que houver banco em uso: `create_all` cria tabela
nova, mas NÃO altera tabela existente. Coluna nova numa tabela que já existe
precisa de migration explícita.
"""
from sqlalchemy import text
from app import db


# Nome da empresa da instalação. O `empresa_id` continua em todas as tabelas
# (ver a docstring de Empresa em models.py); com uma empresa só, ele é invisível.
NOME_EMPRESA = 'Martin Brower'

# Tabelas com `empresa_id` direto — as que o seed_empresa vincula no primeiro boot.
_TABELAS_EMPRESA = [
    'usuarios', 'colaboradores', 'localidades', 'grupos', 'logs_auditoria',
]


# ---------------------------------------------------------------------------
# DEFINIÇÃO COMPLETA DE PERMISSÕES E GRUPOS
# ---------------------------------------------------------------------------

# Fonte da verdade do RBAC. Alterar aqui e reiniciar o servidor aplica
# automaticamente — seed_grupos_e_permissoes() é idempotente.
TODAS_PERMISSOES = [
    # módulo: operacional
    ('mov.retirar',                 'Realizar retirada de coletor',             'operacional'),
    ('mov.devolver',                'Realizar devolução de coletor',             'operacional'),
    ('inventario.reportar',         'Reportar problema em coletor',              'operacional'),
    ('inventario.liberar_suspenso', 'Remover coletor do status Suspenso',        'operacional'),
    ('admin.inventario_ativos',     'Gerenciar inventário de ativos',            'operacional'),
    # módulo: relatorios
    ('dashboard.ver',      'Ver dashboard (Visão Geral)',                        'relatorios'),
    ('painel_op.ver',      'Ver Painel Operacional',                             'relatorios'),
    ('relatorio.exportar', 'Exportar dados em CSV',                              'relatorios'),
    ('auditoria.ver',      'Ver log de auditoria do sistema',                    'relatorios'),
    # módulo: admin
    ('admin.usuarios',      'Gerenciar usuários do sistema',                     'admin'),
    ('admin.grupos',        'Gerenciar grupos e permissões',                     'admin'),
    ('admin.localidades',   'Gerenciar localidades',                             'admin'),
    ('admin.revisar_campo',    'Revisar conta criada em campo',                   'admin'),
    ('admin.colaboradores',    'Gerenciar cadastro de colaboradores',             'admin'),
]

# TI tem acesso total ao operacional, mas não gerencia grupos pela UI
# (admin.grupos — evita auto-elevação sem auditoria) e não revisa contas de
# campo (admin.revisar_campo).
_PERM_EXCLUIDAS_TI = {'admin.revisar_campo', 'admin.grupos'}
_TODAS_CODIGOS     = [c for c, _, _ in TODAS_PERMISSOES]

GRUPOS_DEFAULTS = {
    'TI_MASTER': {
        'descricao': 'Acesso total ao sistema, gestão de usuários e grupos.',
        # protegido=True bloqueia edição via UI — evita que o único grupo com
        # admin.grupos seja removido acidentalmente, trancando o sistema.
        'protegido': True,
        'permissoes': _TODAS_CODIGOS,
    },
    'TI': {
        'descricao': 'Acesso completo exceto gestão de grupos e revisão de campo.',
        'protegido': False,
        'permissoes': [c for c in _TODAS_CODIGOS if c not in _PERM_EXCLUIDAS_TI],
    },
    'DIRETOR': {
        'descricao': 'Visão executiva — dashboards e relatórios.',
        'protegido': False,
        'permissoes': ['dashboard.ver', 'painel_op.ver', 'relatorio.exportar'],
    },
    'GERENTE': {
        'descricao': 'Dashboards e relatórios.',
        'protegido': False,
        'permissoes': [
            'dashboard.ver', 'painel_op.ver', 'relatorio.exportar',
        ],
    },
    'SUPERVISOR': {
        'descricao': 'Visão geral operacional e reporte de problemas.',
        'protegido': False,
        'permissoes': ['dashboard.ver', 'inventario.reportar'],
    },
    'COORDENADOR': {
        'descricao': 'Reporte de problemas em coletores.',
        'protegido': False,
        'permissoes': ['inventario.reportar'],
    },
    'ANALISTA': {
        'descricao': 'Movimentação e dashboards operacionais.',
        'protegido': False,
        'permissoes': [
            'mov.retirar', 'mov.devolver', 'inventario.reportar',
            'dashboard.ver', 'painel_op.ver',
        ],
    },
    'ESPECIALISTA': {
        'descricao': 'Técnico sênior — movimentação e reporte de problemas.',
        'protegido': False,
        'permissoes': [
            'mov.retirar', 'mov.devolver', 'inventario.reportar',
        ],
    },
    'TECNICO': {
        'descricao': 'Movimentação e reporte de problemas.',
        'protegido': False,
        'permissoes': [
            'mov.retirar', 'mov.devolver', 'inventario.reportar',
        ],
    },
    'LIDER': {
        'descricao': 'Liderança operacional — movimentação e visão geral.',
        'protegido': False,
        'permissoes': ['mov.retirar', 'mov.devolver', 'inventario.reportar', 'dashboard.ver'],
    },
    'OPERADOR': {
        'descricao': 'Usuário de campo — sem acesso ao sistema. RE utilizado para atribuição de movimentações.',
        'protegido': False,
        'permissoes': [],
    },
    'CONSULTA': {
        'descricao': 'Somente leitura — dashboards e relatórios.',
        'protegido': False,
        'permissoes': [
            'dashboard.ver', 'painel_op.ver', 'relatorio.exportar',
        ],
    },
    'AUDITOR': {
        'descricao': 'Acesso exclusivo ao log de auditoria.',
        'protegido': False,
        'permissoes': ['auditoria.ver'],
    },
}


# ---------------------------------------------------------------------------
# SEEDS
# ---------------------------------------------------------------------------

def seed_geografico():
    """Popula Américas, Países e vincula Localidades existentes ao País correto.

    Completamente idempotente — seguro rodar em todo boot. Qualquer execução
    subsequente descobre que os registros já existem e não duplica.

    Migração automática: todas as Localidades sem pais_id são assumidas como BR
    (único país com CDs no deploy inicial). Ao adicionar CDs de outros países
    no futuro, o pais_id deverá ser definido manualmente via tela de admin.
    """
    from app.models import America, Pais, Localidade

    AMERICAS = [
        ('SA', 'South America'),
        ('CA', 'Central America'),
        ('NA', 'North America'),
    ]
    # Porto Rico (PR) é CA por decisão organizacional interna — não é erro.
    PAISES = [
        ('BR', 'Brasil',         'SA'),
        ('PA', 'Panamá',         'CA'),
        ('CR', 'Costa Rica',     'CA'),
        ('PR', 'Porto Rico',     'CA'),
        ('US', 'Estados Unidos', 'NA'),
    ]

    for sigla, nome in AMERICAS:
        if not America.query.filter_by(sigla=sigla).first():
            db.session.add(America(sigla=sigla, nome=nome))
            print(f"[SEED] América criada: {sigla}")
    db.session.flush()

    for sigla, nome, america_sigla in PAISES:
        if not Pais.query.filter_by(sigla=sigla).first():
            america = America.query.filter_by(sigla=america_sigla).first()
            db.session.add(Pais(sigla=sigla, nome=nome, america_id=america.id))
            print(f"[SEED] País criado: {sigla}")
    db.session.flush()

    brasil = Pais.query.filter_by(sigla='BR').first()
    if brasil:
        migradas = Localidade.query.filter_by(pais_id=None).count()
        if migradas:
            Localidade.query.filter_by(pais_id=None).update({'pais_id': brasil.id})
            print(f"[SEED] {migradas} localidade(s) vinculada(s) ao Brasil.")

    db.session.commit()
    print("[SEED] Hierarquia geográfica sincronizada.")


def seed_grupos_empresa(empresa_id, perm_map, deletados=None):
    """Cria/converge os grupos padrão (GRUPOS_DEFAULTS) para UMA empresa (T4a).

    Convergente por (empresa_id, nome): grupos que já existem NÃO são
    duplicados. Grupos registrados em GrupoDeletado PARA ESTA empresa não são
    recriados — a deleção intencional passou a ser por tenant.

    perm_map vem pronto da 1ª passada de seed_grupos_e_permissoes (as Permissao
    são globais, compartilhadas entre empresas). deletados: conjunto de nomes já
    deletados nesta empresa; se None, é resolvido aqui. empresa_id None é o
    caminho de bootstrap (fresh boot sem empresa ainda) — os grupos nascem
    órfãos e o seed_empresa os vincula à empresa por último.
    """
    from app.models import Grupo, GrupoDeletado
    if deletados is None:
        deletados = {r.nome for r in
                     GrupoDeletado.query.filter_by(empresa_id=empresa_id).all()}

    for nome, cfg in GRUPOS_DEFAULTS.items():
        if nome in deletados:
            continue  # deletado intencionalmente via UI nesta empresa — não recriar

        g = Grupo.query.filter_by(empresa_id=empresa_id, nome=nome).first()
        if not g:
            g = Grupo(nome=nome, descricao=cfg['descricao'], protegido=cfg['protegido'],
                      empresa_id=empresa_id)
            db.session.add(g)
            db.session.flush()
            g.permissoes = [perm_map[c] for c in cfg['permissoes'] if c in perm_map]
        else:
            g.descricao = cfg['descricao']
            g.protegido = cfg['protegido']
            # Adiciona permissões novas declaradas em GRUPOS_DEFAULTS que ainda não
            # estão no grupo. Operação apenas aditiva — não remove permissões existentes,
            # preservando configurações manuais feitas via /grupos.
            codigos_atuais = {p.codigo for p in g.permissoes}
            for c in cfg['permissoes']:
                if c in perm_map and c not in codigos_atuais:
                    g.permissoes.append(perm_map[c])
                    print(f"[SEED] Permissão '{c}' adicionada ao grupo '{nome}' (empresa {empresa_id}).")


def seed_grupos_e_permissoes():
    """Sincroniza permissões (globais) e grupos (POR empresa) com GRUPOS_DEFAULTS.

    Idempotente — roda em todo boot e converge o banco para o estado declarado
    em GRUPOS_DEFAULTS. Adicionar uma permissão nova ou mudar a descrição de um
    grupo basta reiniciar para aplicar.

    T4a — os grupos passam a ser por tenant (cada empresa tem seu TI_MASTER,
    GERENTE…). 1ª passada: garante as Permissao (globais). 2ª passada: itera as
    empresas e chama seed_grupos_empresa para cada uma, com os deletados
    escopados. Bootstrap (fresh boot, ainda sem nenhuma empresa): semeia grupos
    órfãos (empresa_id NULL) que o seed_empresa vincula à empresa por último —
    no boot seguinte ela existe e a convergência por (empresa_id, nome) não
    duplica.
    """
    from collections import defaultdict
    from app.models import Permissao, Empresa, GrupoDeletado

    # Primeira passagem: garante que todas as permissões existem antes
    # de tentar vinculá-las a grupos (evita FK sem ID). Permissao é global.
    perm_map = {}
    for codigo, descricao, modulo in TODAS_PERMISSOES:
        p = Permissao.query.filter_by(codigo=codigo).first()
        if not p:
            p = Permissao(codigo=codigo, descricao=descricao, modulo=modulo)
            db.session.add(p)
            db.session.flush()
        perm_map[codigo] = p

    # Segunda passagem: grupos por empresa. Deletados agrupados em 1 query
    # (evita N consultas ao iterar as empresas).
    deletados_por_empresa = defaultdict(set)
    for r in GrupoDeletado.query.all():
        deletados_por_empresa[r.empresa_id].add(r.nome)

    empresas = Empresa.query.all()
    if empresas:
        for emp in empresas:
            seed_grupos_empresa(emp.id, perm_map, deletados_por_empresa.get(emp.id, set()))
    else:
        # Bootstrap: nenhuma empresa ainda (fresh boot). Grupos nascem órfãos;
        # seed_empresa (por último) faz o backfill empresa_id NULL -> empresa.
        seed_grupos_empresa(None, perm_map, deletados_por_empresa.get(None, set()))

    db.session.commit()
    print("[SEED] Grupos e permissões sincronizados.")


def seed_empresa():
    """Cria a empresa da instalação e vincula a ela o que nasceu sem empresa.

    Idempotente: roda em todo boot. Fica depois de grupos e admin porque esses
    nascem antes de a empresa existir no primeiro boot — o UPDATE ... WHERE
    empresa_id IS NULL os vincula aqui.

    Fora do primeiro boot, um órfão é anomalia (toda rota carimba empresa_id):
    grita no log para investigação, mas vincula assim mesmo para o dado não
    ficar inacessível.
    """
    from app.models import Empresa

    empresa = Empresa.query.order_by(Empresa.id).first()
    bootstrap = empresa is None
    if bootstrap:
        empresa = Empresa(nome=NOME_EMPRESA, ativa=True)
        db.session.add(empresa)
        db.session.flush()
        print(f"[SEED] Empresa criada: {empresa.nome} (id={empresa.id}).")

    for tabela in _TABELAS_EMPRESA:
        r = db.session.execute(
            text(f'UPDATE {tabela} SET empresa_id = :eid WHERE empresa_id IS NULL'),
            {'eid': empresa.id},
        )
        if r.rowcount:
            if bootstrap:
                print(f"[SEED] {r.rowcount} registro(s) de {tabela} vinculado(s) à empresa.")
            else:
                print(f"[ALERTA] {r.rowcount} registro(s) ÓRFÃO(S) em {tabela} "
                      f"vinculado(s) à empresa — investigar origem.")

    db.session.commit()


def seed_owner():
    """Garante que existe um Owner apto a logar. Idempotente.

    Sem Owner, o admin de bootstrap (re='admin', o de menor id) é promovido.
    Como o login é por e-mail, um Owner sem e-mail ficaria trancado para fora —
    recebe o endereço de bootstrap (trocável depois pela tela de perfil).
    """
    from app.models import Usuario

    owner = (Usuario.query.filter_by(is_owner=True)
             .order_by(Usuario.id).first())
    if not owner:
        owner = Usuario.query.filter_by(re='admin').order_by(Usuario.id).first()
        if not owner:
            return  # banco sem admin de bootstrap — cria_usuario_admin cuida no boot novo
        owner.is_owner = True
        print(f"[SEED] Usuário '{owner.nome}' (re={owner.re}) promovido a Owner.")

    if not owner.email:
        owner.email = 'admin@mbassets.local'
        print("[SEED] Owner sem e-mail — 'admin@mbassets.local' atribuído para permitir login.")

    db.session.commit()


def executar_startup():
    """Cria o schema e roda os seeds. Idempotente — chamada em todo boot.

    Deve ser chamada dentro de um app_context ativo.
    """
    from app.helpers import cria_usuario_admin
    db.create_all()
    seed_grupos_e_permissoes()
    seed_geografico()
    cria_usuario_admin()
    seed_empresa()
    seed_owner()
