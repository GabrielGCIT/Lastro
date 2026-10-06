"""
O manual do sistema — organizado por TAREFA, não por tela.

🔴 Quem abre a ajuda não tem curiosidade sobre a tela: tem um problema. "A
bateria não voltou, e agora?" é a pergunta real; "o que faz a tela de Rastreio"
não é. Um manual indexado por tela obriga a pessoa a saber em qual tela está a
resposta — que é justamente o que ela não sabe.

Por isso cada item aqui começa pela SITUAÇÃO, tem os passos na ordem, e termina
com o que fazer quando não sai como esperado. A seção "Quando foge do script" é
a mais importante: o caminho feliz qualquer um descobre clicando.

O conteúdo fica aqui, em dados, e não no template: assim dá para testar que toda
seção tem passos, que todo link aponta para rota que existe, e que o "?" de cada
tela encontra a sua seção.

Idioma: o manual é em português. O sistema tem seletor de três idiomas herdado,
mas esta instalação atende uma operação no Brasil, e um manual meio traduzido é
pior que um manual num idioma só. Se um dia precisar, o texto está num lugar só.
"""

# Cada seção: id (usado na âncora e no "?" das telas), título, e itens.
# Cada item: situação, passos, e o "se não sair como esperado".
SECOES = [
    {
        'id': 'balcao',
        'titulo': 'O dia a dia do balcão',
        'resumo': 'Entregar, receber de volta, e o que fazer quando falta peça.',
        'icone': 'barcode',
        'itens': [
            {
                'titulo': 'Entregar um coletor para alguém',
                'situacao': 'O colaborador chegou no balcão para pegar o equipamento.',
                'passos': [
                    'Abra <b>Movimentação</b> e deixe a aba <b>Retirada</b> selecionada.',
                    'Bipe ou digite o <b>RE</b> do colaborador.',
                    'Bipe ou digite o <b>patrimônio</b> do coletor.',
                    'Confira os cinco itens do checklist. Todos são obrigatórios: '
                    'é essa conferência que separa "voltou quebrado" de "saiu quebrado".',
                    'Se o coletor levar bateria, o sistema pergunta quantas e já '
                    'desconta do estoque do CD.',
                    'Clique em <b>Confirmar saída</b>.',
                ],
                'problemas': [
                    ('O sistema diz que a pessoa já está com um coletor',
                     'Cada colaborador fica com um equipamento por vez. '
                     'Registre a devolução do anterior antes.'),
                    ('O RE não é encontrado',
                     'Use <b>Cadastrar novo colaborador</b>, ali mesmo na tela. '
                     'Pede só nome e RE; a TI completa o resto depois.'),
                    ('A bateria que aparece não é a certa',
                     'O sistema sugere as compatíveis com a câmara do coletor. '
                     'Se faltar alguma, confira o tipo de bateria em '
                     '<b>Baterias</b> — provavelmente está marcado para outro '
                     'equipamento ou para outra câmara.'),
                ],
            },
            {
                'titulo': 'Receber um coletor de volta',
                'situacao': 'O colaborador voltou da pista com o equipamento.',
                'passos': [
                    'Em <b>Movimentação</b>, escolha a aba <b>Devolução</b>.',
                    'Bipe o patrimônio do coletor.',
                    'Diga se a identificação está intacta ou violada.',
                    'O sistema lista o que saiu junto. Informe quanto voltou de '
                    'cada item — não precisa lembrar: a lista já vem pronta.',
                    'Se algo voltou com defeito, marque e descreva.',
                ],
                'problemas': [
                    ('Voltou menos do que saiu',
                     'Informe a quantidade real. O sistema registra a falta no '
                     'nome de quem retirou e ela aparece no painel inicial até '
                     'ser resolvida. Isso não impede a devolução do coletor.'),
                ],
            },
            {
                'titulo': 'A peça que faltava apareceu depois',
                'situacao': 'O coletor já voltou dias atrás, sem a bateria, e agora '
                            'ela apareceu.',
                'passos': [
                    'Abra a tela inicial. A falta está na lista '
                    '<b>Complementos que não voltaram</b>.',
                    'Clique em <b>Recebi</b> na linha da peça.',
                    'Se voltou só parte, informe quanto.',
                ],
                'problemas': [
                    ('A falta não aparece na lista',
                     'Ela some assim que é baixada. Confira no histórico do '
                     'coletor se alguém já registrou.'),
                ],
            },
        ],
    },
    {
        'id': 'imprevistos',
        'titulo': 'Quando foge do script',
        'resumo': 'Defeito, identificação violada, equipamento sumido.',
        'icone': 'triangle-exclamation',
        'itens': [
            {
                'titulo': 'O coletor está com defeito',
                'situacao': 'O equipamento não liga, não lê, ou voltou danificado.',
                'passos': [
                    'Abra <b>Reportar Problema</b>.',
                    'Bipe o patrimônio e escolha o que está acontecendo.',
                    'O coletor sai da lista de disponíveis e não pode ser '
                    'entregue a ninguém enquanto estiver assim.',
                ],
                'problemas': [
                    ('Preciso entregar mesmo assim',
                     'Não dá, e é de propósito: entregar equipamento com defeito '
                     'conhecido transfere o problema para o colaborador e some '
                     'com o registro de quem percebeu.'),
                ],
            },
            {
                'titulo': 'A identificação do coletor foi violada',
                'situacao': 'O lacre ou a etiqueta de identificação está rompida.',
                'passos': [
                    'Na devolução, marque a identificação como violada.',
                    'A foto passa a ser obrigatória — ela é a evidência.',
                    'O coletor vai para <b>Suspensos / Identificação</b> e só '
                    'volta a circular depois de aprovado.',
                ],
                'problemas': [
                    ('Não tenho como tirar foto agora',
                     'Sem a foto o registro não é aceito. Se o celular não '
                     'estiver à mão, peça para alguém fotografar: é o que '
                     'sustenta a decisão depois.'),
                ],
            },
            {
                'titulo': 'O coletor não aparece em lugar nenhum',
                'situacao': 'Você bipa o patrimônio e o sistema diz que não existe.',
                'passos': [
                    'Confira em <b>Coletores</b> se ele está cadastrado.',
                    'Se estiver, veja em qual CD: você só enxerga os do seu.',
                    'Se não estiver, cadastre — ou peça à TI.',
                ],
                'problemas': [
                    ('Está cadastrado em outro CD',
                     'Quem tem acesso a mais de um CD pode movê-lo pelo '
                     'inventário, marcando o coletor e usando <b>Mover</b>.'),
                ],
            },
        ],
    },
    {
        'id': 'cadastros',
        'titulo': 'Cadastrar as coisas',
        'resumo': 'CDs, colaboradores, coletores, tipos de bateria e peças.',
        'icone': 'folder-plus',
        'itens': [
            {
                'titulo': 'Cadastrar um CD',
                'situacao': 'Primeira configuração, ou um centro novo entrou na '
                            'operação.',
                'passos': [
                    'Vá em <b>Usuários e acessos</b> e depois em <b>CDs</b>.',
                    'Clique em <b>Novo CD</b>.',
                    'A sigla aparece na frente de todo patrimônio: GR vira "GR 001".',
                ],
                'problemas': [
                    ('Preciso excluir um CD',
                     'Só é possível se nenhum coletor estiver vinculado a ele.'),
                ],
            },
            {
                'titulo': 'Cadastrar um coletor',
                'situacao': 'Equipamento novo chegou.',
                'passos': [
                    'Em <b>Coletores</b>, clique em <b>Novo</b>.',
                    'Informe patrimônio, serial e o CD onde ele fica.',
                    'A câmara define que baterias podem sair com ele.',
                ],
                'problemas': [
                    ('O coletor some da lista depois de salvo',
                     'Ele foi cadastrado sem CD, e quem enxerga por CD não o vê. '
                     'Abra o inventário sem filtro e corrija o CD dele.'),
                ],
            },
            {
                'titulo': 'Cadastrar um tipo de bateria',
                'situacao': 'Chegou um modelo novo, ou o estoque precisa ser '
                            'separado por tipo.',
                'passos': [
                    'Abra <b>Baterias</b> e use o formulário no topo.',
                    'Diga de qual equipamento ela é: só as do coletor aparecem '
                    'na retirada.',
                    'Diga onde pode ser usada: só no seco, ou também no '
                    'climatizado.',
                    'Escolha ícone e cor — é como o pessoal do CD vai reconhecer '
                    'na prateleira.',
                ],
                'problemas': [
                    ('A bateria aparece com um aviso vermelho',
                     'Falta dizer onde ela pode ser usada. Enquanto isso, o '
                     'sistema não consegue impedir que ela saia para o '
                     'congelado.'),
                ],
            },
        ],
    },
    {
        'id': 'acompanhar',
        'titulo': 'Acompanhar a operação',
        'resumo': 'Quem está com o quê, o que atrasou, e os números do mês.',
        'icone': 'chart-line',
        'itens': [
            {
                'titulo': 'Saber quem está com cada coletor',
                'situacao': 'Precisa achar um equipamento específico, ou saber '
                            'quanta coisa está na rua.',
                'passos': [
                    'A tela inicial mostra o total em operação.',
                    '<b>Coletores</b> lista cada um com seu status.',
                    'O <b>Histórico</b> mostra todas as retiradas e devoluções, '
                    'com o nome de quem pegou.',
                ],
                'problemas': [],
            },
            {
                'titulo': 'Ver o que está atrasado',
                'situacao': 'Cobrar as peças que não voltaram.',
                'passos': [
                    '<b>Rastreio</b> mostra cada peça que está fora e há quanto '
                    'tempo.',
                    'Use <b>Só o que está fora do prazo</b> para ver apenas o '
                    'que passou do limite.',
                    '<b>Baterias</b> mostra o saldo de cada CD: quantas existem, '
                    'quantas estão na rua e quantas atrasaram.',
                ],
                'problemas': [
                    ('O saldo não bate com a prateleira',
                     'O sistema conta o que foi registrado. Diferença costuma '
                     'ser peça que voltou sem alguém dar baixa — procure em '
                     'Rastreio e baixe pelo botão <b>Recebi</b>.'),
                ],
            },
        ],
    },
    {
        'id': 'administracao',
        'titulo': 'Administrar o sistema',
        'resumo': 'Acessos, senhas, backup e saúde da instalação.',
        'icone': 'screwdriver-wrench',
        'itens': [
            {
                'titulo': 'Dar acesso a alguém',
                'situacao': 'Pessoa nova vai operar o sistema.',
                'passos': [
                    'Vá em <b>Usuários e acessos</b> e clique em <b>Novo '
                    'usuário</b>.',
                    'Escolha o perfil pelo trabalho que a pessoa faz: '
                    '<b>Balcão</b> entrega e recebe; <b>Supervisor</b> também '
                    'cadastra; <b>Consulta</b> só olha; <b>TI</b> administra.',
                    'Escolha o CD. Sem CD, a pessoa entra e não enxerga nada.',
                    'A senha que você define é provisória: ela troca no primeiro '
                    'acesso.',
                ],
                'problemas': [
                    ('A pessoa entrou e não vê nenhum equipamento',
                     'Quase sempre é o CD em branco no cadastro dela.'),
                ],
            },
            {
                'titulo': 'Alguém esqueceu a senha',
                'situacao': 'O sistema não envia e-mail — a redefinição é local.',
                'passos': [
                    'Um usuário de TI abre o cadastro da pessoa e define uma '
                    'senha nova.',
                    'Ela entra com essa senha e troca na hora.',
                ],
                'problemas': [
                    ('Quem esqueceu a senha é o único TI',
                     'No servidor, na pasta do sistema, execute: '
                     '<code>venv\\Scripts\\python.exe manage.py redefinir-senha '
                     'email@da.pessoa</code>'),
                ],
            },
            {
                'titulo': 'Conferir se está tudo bem',
                'situacao': 'Rotina, ou algo parecendo estranho.',
                'passos': [
                    'Abra <b>Saúde do sistema</b>, no menu de administração.',
                    'Ela diz em português o estado do banco, do backup, do disco '
                    'e das pastas.',
                    'Cada item traz o que fazer quando não está certo.',
                ],
                'problemas': [
                    ('Diz que o backup está parado',
                     'Confira espaço em disco e se a pasta <b>backups</b> ainda '
                     'existe na pasta do sistema. O backup roda todo dia às 3h, '
                     'e também assim que o servidor liga, se tiver perdido o '
                     'horário.'),
                ],
            },
            {
                'titulo': 'Recuperar o sistema a partir de um backup',
                'situacao': 'O banco foi perdido ou corrompido.',
                'passos': [
                    'Pare o sistema.',
                    'Na pasta <b>backups</b>, escolha o arquivo da data '
                    'desejada — o nome traz data e hora.',
                    'Copie-o para a pasta <b>instance</b>, substituindo o '
                    '<b>lastro.db</b>.',
                    'Inicie o sistema de novo.',
                ],
                'problemas': [
                    ('Não sei se o backup está bom',
                     'Toda cópia é conferida quando é feita; as que falham não '
                     'são guardadas. Se o arquivo está na pasta, ele abriu e '
                     'tinha conteúdo no momento em que foi criado.'),
                ],
            },
        ],
    },
]

# Qual seção o "?" de cada tela abre. Endpoint do Flask → id de seção.
# 🔴 Mapear por ENDPOINT, não por URL: a URL muda sem ninguém lembrar de
# atualizar isto, e o "?" passaria a levar à seção errada em silêncio.
AJUDA_POR_TELA = {
    'movimentacao.operacao_index': 'balcao',
    'movimentacao.realizar_retirada': 'balcao',
    'movimentacao.realizar_devolucao': 'balcao',
    'movimentacao.operacao_inventario': 'imprevistos',
    'suspensos.suspensos_index': 'imprevistos',
    'coletores.gerenciar_coletores': 'cadastros',
    'coletores.gerenciar_localidades': 'cadastros',
    'colaboradores.lista': 'cadastros',
    'complementos.baterias': 'cadastros',
    'complementos.baterias_detalhe': 'cadastros',
    'complementos.index': 'cadastros',
    'complementos.rastreio': 'acompanhar',
    'dashboard.index': 'acompanhar',
    'dashboard.historico_completo': 'acompanhar',
    'dashboard.relatorio_operacional': 'acompanhar',
    'dashboard.gerenciar_usuarios': 'administracao',
    'dashboard.gerenciar_grupos': 'administracao',
    'dashboard.saude': 'administracao',
    'dashboard.auditoria': 'administracao',
}


def secao_da_tela(endpoint):
    """A seção de ajuda que o '?' desta tela deve abrir, ou None."""
    return AJUDA_POR_TELA.get(endpoint or '')


def secao_por_id(secao_id):
    for secao in SECOES:
        if secao['id'] == secao_id:
            return secao
    return None
