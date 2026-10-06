"""
Inicialização do banco: schema e seeds.

O banco do Lastro sempre nasce novo — não existe base legada para migrar. Por
isso o schema inteiro vem do `db.create_all()` (models.py é a fonte da verdade,
com constraints nomeadas lá) e este arquivo cuida só dos seeds.

Ordem de execução dentro do app_context (ver executar_startup):
  1. db.create_all()
  2. seed_grupos_e_permissoes()
  3. seed_empresa()         ← empresa da instalação + vínculo do que nasceu órfão

O boot NÃO cria usuário: o administrador nasce no primeiro acesso
(auth.primeiro_acesso), com a senha escolhida por quem instala.

⚠️  Mudança de schema depois que houver banco em uso: `create_all` cria tabela
nova, mas NÃO altera tabela existente. Coluna nova numa tabela que já existe
precisa de migration explícita.
"""
from sqlalchemy import text
from app import db


# Nome da empresa da instalação, usado até alguém informar o verdadeiro no
# primeiro acesso. O `empresa_id` continua em todas as tabelas (ver a docstring
# de Empresa em models.py); com uma empresa só, ele é invisível.
#
# 🔴 Genérico de propósito. Antes aqui estava cravado o nome de um cliente
# específico — no código-fonte, que é entregue junto com o produto. Software que
# nasce dizendo o nome de uma empresa parece ter sido feito sob medida para ela,
# e isso vira argumento contra quem o escreveu. O nome real entra na instalação,
# não no código.
NOME_EMPRESA = 'Minha Empresa'

# Tabelas com `empresa_id` direto — as que o seed_empresa vincula no primeiro boot.
_TABELAS_EMPRESA = [
    'usuarios', 'colaboradores', 'localidades', 'grupos', 'logs_auditoria',
]


# ---------------------------------------------------------------------------
# DEFINIÇÃO COMPLETA DE PERMISSÕES E GRUPOS
# ---------------------------------------------------------------------------

# Fonte da verdade do RBAC. Os perfis são FIXOS: não há tela para criar, excluir
# ou redistribuir permissões, e o seed impõe exatamente o que está declarado aqui
# a cada boot. Menos coisa para alguém configurar errado — e ninguém vai ligar
# para perguntar por que o balcão não abre.
TODAS_PERMISSOES = [
    # módulo: operacional
    ('mov.retirar',                 'Realizar retirada de coletor',             'operacional'),
    ('mov.devolver',                'Realizar devolução de coletor',             'operacional'),
    ('inventario.reportar',         'Reportar problema em coletor',              'operacional'),
    ('inventario.liberar_suspenso', 'Liberar coletor suspenso',                  'operacional'),
    ('admin.inventario_ativos',     'Cadastrar coletores, baterias e complementos', 'operacional'),
    # módulo: relatorios
    ('dashboard.ver',      'Ver dashboard (Visão Geral)',                        'relatorios'),
    ('painel_op.ver',      'Ver Painel Operacional',                             'relatorios'),
    ('relatorio.exportar', 'Exportar dados em CSV',                              'relatorios'),
    ('auditoria.ver',      'Ver log de auditoria do sistema',                    'relatorios'),
    # módulo: admin
    ('admin.usuarios',      'Gerenciar usuários do sistema',                     'admin'),
    ('admin.grupos',        'Ver o que cada perfil pode fazer',                  'admin'),
    ('admin.localidades',   'Gerenciar CDs',                                     'admin'),
    ('admin.colaboradores', 'Gerenciar cadastro de colaboradores',               'admin'),
]

_TODAS_CODIGOS = [c for c, _, _ in TODAS_PERMISSOES]

_OPERACAO = ['mov.retirar', 'mov.devolver', 'inventario.reportar']
_LEITURA  = ['dashboard.ver', 'painel_op.ver']

# Os quatro perfis do produto. Todos protegidos: são parte do sistema, não
# configuração. A ordem é a de exibição (do mais amplo ao mais restrito).
GRUPOS_DEFAULTS = {
    'TI': {
        'descricao': 'Administra o sistema: usuários, CDs, cadastros, suspensos e logs.',
        'protegido': True,
        'permissoes': _TODAS_CODIGOS,
    },
    'SUPERVISOR': {
        'descricao': 'Opera o balcão e cuida dos cadastros do CD: coletores, '
                     'baterias, complementos e colaboradores.',
        'protegido': True,
        'permissoes': _OPERACAO + _LEITURA + ['relatorio.exportar',
                                              'admin.inventario_ativos',
                                              'admin.colaboradores'],
    },
    'BALCAO': {
        'descricao': 'Retira e devolve coletores e reporta problemas.',
        'protegido': True,
        'permissoes': _OPERACAO + _LEITURA,
    },
    'CONSULTA': {
        'descricao': 'Somente leitura: dashboard, painel operacional e exportação.',
        'protegido': True,
        'permissoes': _LEITURA + ['relatorio.exportar'],
    },
}

# O perfil do administrador criado no primeiro acesso.
PERFIL_ADMIN = 'TI'


# ---------------------------------------------------------------------------
# SEEDS
# ---------------------------------------------------------------------------

def seed_grupos_empresa(empresa_id, perm_map):
    """Cria/converge os quatro perfis para UMA empresa.

    🔴 Convergência EXATA: as permissões do perfil passam a ser as declaradas em
    GRUPOS_DEFAULTS, inclusive removendo as que sobrarem. Com perfis fixos, uma
    permissão a mais no banco (edição manual, versão antiga) é um desvio do
    produto, não uma customização a preservar.

    empresa_id None é o caminho de bootstrap (fresh boot sem empresa ainda) — os
    grupos nascem órfãos e o seed_empresa os vincula à empresa por último.
    """
    from app.models import Grupo
    for nome, cfg in GRUPOS_DEFAULTS.items():
        g = Grupo.query.filter_by(empresa_id=empresa_id, nome=nome).first()
        if not g:
            g = Grupo(nome=nome, empresa_id=empresa_id)
            db.session.add(g)
            db.session.flush()
        g.descricao  = cfg['descricao']
        g.protegido  = cfg['protegido']
        g.permissoes = [perm_map[c] for c in cfg['permissoes']]


def seed_grupos_e_permissoes():
    """Sincroniza permissões (globais) e os quatro perfis (por empresa).

    Idempotente — roda em todo boot e converge o banco para o estado declarado.
    Permissão que saiu de TODAS_PERMISSOES é apagada (e some dos perfis junto).
    """
    from app.models import Permissao, Empresa

    perm_map = {}
    for codigo, descricao, modulo in TODAS_PERMISSOES:
        p = Permissao.query.filter_by(codigo=codigo).first()
        if not p:
            p = Permissao(codigo=codigo)
            db.session.add(p)
        p.descricao = descricao
        p.modulo    = modulo
        perm_map[codigo] = p
    db.session.flush()

    empresas = Empresa.query.all()
    for emp in empresas or [None]:
        seed_grupos_empresa(emp.id if emp else None, perm_map)
    db.session.flush()

    for sobra in Permissao.query.filter(Permissao.codigo.notin_(list(perm_map))).all():
        db.session.delete(sobra)

    db.session.commit()
    print("[SEED] Perfis e permissões sincronizados.")


def seed_empresa():
    """Cria a empresa da instalação e vincula a ela o que nasceu sem empresa.

    Idempotente: roda em todo boot. Fica depois dos perfis porque eles nascem
    antes de a empresa existir no primeiro boot — o UPDATE ... WHERE
    empresa_id IS NULL os vincula aqui.

    Fora do primeiro boot, um órfão é anomalia (toda rota carimba empresa_id):
    grita no log para investigação, mas vincula assim mesmo para o dado não
    ficar inacessível.
    """
    from app.models import Empresa

    empresa = Empresa.query.order_by(Empresa.id).first()
    bootstrap = empresa is None
    if bootstrap:
        empresa = Empresa(nome=NOME_EMPRESA)
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


def executar_startup():
    """Cria o schema e roda os seeds. Idempotente — chamada em todo boot.

    Deve ser chamada dentro de um app_context ativo.
    """
    db.create_all()
    seed_grupos_e_permissoes()
    seed_empresa()
