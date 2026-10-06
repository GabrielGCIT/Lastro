"""
A página Sobre: identificação, autoria, licença e componentes.

Serve a três leitores diferentes, e é por isso que ela tem seções distintas:

  - quem USA, e quer saber o que o sistema faz e de onde ele veio;
  - quem ADMINISTRA a máquina, e precisa da versão exata para relatar um
    problema ou conferir se a atualização foi aplicada;
  - o time de SEGURANÇA DA INFORMAÇÃO, que vai perguntar o que roda ali dentro
    e sob qual licença. Essa pergunta vem sempre, e chegar com a resposta
    pronta é a diferença entre uma conversa de cinco minutos e um processo de
    aprovação.

🔴 A seção de autoria não é formalidade. O MBAssets é obra de uma pessoa, cedida
para uso de uma empresa — e as duas coisas precisam estar escritas no próprio
sistema, não só num contrato que ninguém abre. Software sem autoria declarada
vira, com o tempo, software de quem o estiver usando.

Nota honesta: o texto de licença aqui foi escrito para ser claro, não para ser
peça jurídica. Ele diz o essencial — quem fez, quem pode usar, o que não está
incluído. Numa disputa de verdade, quem decide é um advogado.
"""

# --------------------------------------------------------------------------
# IDENTIFICAÇÃO
# --------------------------------------------------------------------------

NOME = 'MBAssets'
DESCRICAO_CURTA = 'Controle de coletores de centro de distribuição'

PROPOSITO = (
    'Registrar quem levou cada coletor, quando devolveu e o que voltou '
    'faltando — do balcão ao relatório. O sistema existe para que a '
    'responsabilidade por um equipamento em campo tenha nome, data e trilha, '
    'sem depender da memória de quem estava no turno.'
)

# --------------------------------------------------------------------------
# AUTORIA E TITULARIDADE
# --------------------------------------------------------------------------

AUTOR = 'Gabriel Garcia de Carvalho'
ANO = '2026'

TITULARIDADE = (
    f'O {NOME} foi concebido, projetado e desenvolvido por {AUTOR}, que detém '
    'os direitos sobre o programa e sobre seu código-fonte. O sistema não foi '
    'encomendado, contratado nem custeado por terceiros, e não deriva de '
    'software de propriedade de outra parte.'
)

LICENCA_TITULO = 'Licença de uso'
LICENCA = [
    ('O que está concedido',
     'Autorização para instalar e usar este sistema nas operações da empresa '
     'que o recebeu, sem limite de prazo, de número de usuários ou de '
     'unidades. O uso é gratuito.'),
    ('O que não está concedido',
     'A cessão dos direitos sobre o programa. Revender, sublicenciar, '
     'redistribuir a terceiros ou apresentar o sistema como obra própria não '
     'está autorizado. O código pode ser lido e ajustado para uso interno — '
     'é software em Python, e isso é inevitável —, mas o trabalho continua '
     'sendo de quem o fez.'),
    ('Garantia',
     'O sistema é entregue no estado em que se encontra. Não há garantia de '
     'funcionamento ininterrupto nem obrigação de manutenção, correção ou '
     'suporte. Quem opera é responsável por manter as cópias de segurança e '
     'por conferir os dados que registra.'),
    ('Outros produtos',
     f'O {NOME} é um produto independente. Receber ou usar este sistema não '
     'cria direito algum sobre qualquer outro software do mesmo autor, ainda '
     'que haja semelhança de finalidade ou de aparência entre eles.'),
]

# --------------------------------------------------------------------------
# O QUE O SISTEMA CONTEMPLA
# --------------------------------------------------------------------------

CONTROLES = [
    ('barcode', 'Balcão de entrega e devolução',
     'Retirada e devolução de coletores por leitura de código, com checklist '
     'obrigatório de conferência na entrega e registro de quem retirou.'),
    ('box-open', 'Inventário de equipamentos',
     'Cadastro dos coletores por CD, com patrimônio, serial, câmara de '
     'operação e situação atual de cada um.'),
    ('battery-half', 'Baterias e complementos',
     'Controle do que sai junto com o coletor, com saldo por CD e '
     'compatibilidade por câmara e por tipo de equipamento.'),
    ('magnifying-glass-location', 'Rastreio e cobrança',
     'Peças que saíram e não voltaram, com o nome de quem retirou e há quanto '
     'tempo estão fora, inclusive depois que o coletor já foi devolvido.'),
    ('triangle-exclamation', 'Ocorrências e suspensão',
     'Registro de defeito e de violação de identificação, com evidência '
     'fotográfica e bloqueio do equipamento até a liberação.'),
    ('chart-line', 'Acompanhamento',
     'Painel do dia, histórico completo das movimentações e indicadores de '
     'uso da frota, com exportação para planilha.'),
    ('users-gear', 'Acessos por perfil',
     'Quatro perfis de uso e recorte por CD: cada pessoa vê e opera apenas o '
     'que lhe cabe.'),
    ('shield-halved', 'Trilha de auditoria',
     'Registro das ações relevantes, com autor e data, para revisão posterior.'),
    ('floppy-disk', 'Cópias de segurança automáticas',
     'Backup diário do banco, conferido a cada execução, com retenção e '
     'tela de saúde que informa o estado da instalação.'),
]

# --------------------------------------------------------------------------
# COMPONENTES DE TERCEIROS
# --------------------------------------------------------------------------
# 🔴 Esta lista existe para a pergunta que o time de segurança sempre faz: "o
# que roda aí dentro?". Todos são de código aberto e de licença permissiva, o
# que significa que podem ser usados em ambiente corporativo sem obrigação de
# abrir o código de quem os utiliza.

COMPONENTES = [
    ('Flask',            '3.0.0',  'BSD-3-Clause', 'Estrutura da aplicação web'),
    ('Werkzeug',         '3.0.1',  'BSD-3-Clause', 'Base HTTP e segurança de senha'),
    ('Jinja2',           '3.1.2',  'BSD-3-Clause', 'Montagem das telas'),
    ('SQLAlchemy',       '2.0.23', 'MIT',          'Acesso ao banco de dados'),
    ('Flask-SQLAlchemy', '3.1.1',  'BSD-3-Clause', 'Integração do banco com a aplicação'),
    ('Flask-WTF',        '1.3.0',  'BSD-3-Clause', 'Formulários e proteção contra CSRF'),
    ('WTForms',          '3.2.2',  'BSD-3-Clause', 'Validação de formulários'),
    ('itsdangerous',     '2.1.2',  'BSD-3-Clause', 'Assinatura dos dados de sessão'),
    ('click',            '8.1.7',  'BSD-3-Clause', 'Comandos de linha de comando'),
    ('blinker',          '1.7.0',  'MIT',          'Sinais internos da aplicação'),
    ('python-dotenv',    '1.0.1',  'BSD-3-Clause', 'Leitura de configuração'),
    ('Waitress',         '3.0.0',  'ZPL-2.1',      'Servidor web de produção'),
    ('Bootstrap',        '5.3.0',  'MIT',          'Aparência e comportamento das telas'),
    ('Font Awesome',     '6.4.0',  'CC BY 4.0 / OFL / MIT', 'Ícones'),
    ('Chart.js',         '4.4.0',  'MIT',          'Gráficos do painel'),
    ('SQLite',           '—',      'Domínio público', 'Banco de dados (vem com o Python)'),
]

NOTA_COMPONENTES = (
    'Todos os componentes acima são de código aberto, com licenças permissivas, '
    'e vêm incluídos na instalação. O sistema não baixa nada da internet para '
    'funcionar, nem envia dados para fora da rede onde está instalado.'
)


def versao(root_dir):
    """A versão instalada, lida do arquivo VERSAO — a mesma da tela de Saúde."""
    from app.saude import versao_instalada
    return versao_instalada(root_dir)
