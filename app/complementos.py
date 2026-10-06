"""
Regras de COMPLEMENTOS na movimentação (pacote H, fatia H2).

Módulo de domínio — as rotas chamam daqui, e não o contrário. Fica fora de
`routes/` porque é regra de negócio que precisa ser testável sem HTTP, e que a
tela de balcão consome mas não define.

Responsabilidades:

    disponivel_por_categoria  — quantas peças há para emprestar NESTE CD agora
    sugerir_para_coletor      — o que a tela deve propor ao identificar o coletor
    registrar_saida           — grava o que saiu, aplicando o portão do D6 (H2)
    em_campo_da_movimentacao  — o que ESTE colaborador está devendo (H3)
    conferir_devolucao        — baixa o que voltou e deixa a falta explícita (H3)
    pendencias_abertas        — a dívida que precisa ser cobrada AGORA (H4/H5)
    rastreio_itens            — onde está cada peça identificada, agora (H5)
    rastreio_quantidades      — quantas estão fora e COM QUEM, por CD (H5)

🔴 O princípio que rege o desenho da H2 inteira: a tela de movimentação é balcão,
com leitor de código de barras e fila na frente. O sistema já sabe qual coletor
é — então ele PROPÕE, e o operador CONFIRMA. Nada aqui deve exigir digitação no
caso comum.
"""
from app import db
from app.models import (CategoriaComplemento, EstoqueComplemento, ItemComplementar,
                        Localidade, Movimentacao, MovimentacaoComplemento, Coletor,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_CAMARA_SECO,
                        COMPLEMENTO_CAMARA_CLIMATIZADO)

# D8 — toda retirada sai com UMA bateria. Não é default de formulário: é a
# operação normal, e mais de uma é exceção. A tela confirma esse número em vez de
# perguntar "quantas?", justamente para não induzir ao excesso.
BATERIAS_PADRAO = 1

# H5 — o prazo de cobrança. O turno da MB tem ~9h, mas medir por turno é frágil
# (vira, emenda, atravessa a meia-noite), então o Gabriel travou 1 DIA como a
# régua para começar a alertar. Fica como política por empresa e não como
# constante porque é regra de NEGÓCIO: outro cliente opera em 12h ou 48h, e isso
# não pode exigir deploy.
PRAZO_PADRAO_HORAS = 24

# As três situações de uma dívida. Não são estados gravados — são derivados do
# relógio a cada leitura. Um `esta_atrasado` em coluna dessincronizaria no
# instante seguinte ao INSERT, porque o tempo anda e o banco não.
SITUACAO_FALTOU    = 'FALTOU'      # o coletor voltou, o complemento não
SITUACAO_ATRASADO  = 'ATRASADO'    # ainda em campo, estourou o prazo
SITUACAO_NO_PRAZO  = 'NO_PRAZO'    # ainda em campo, dentro do prazo


class ComplementoRecusado(Exception):
    """Portão do D6: o complemento escolhido não pode sair assim.

    Exceção própria porque o chamador precisa distinguir "regra de negócio
    recusou" de erro genérico — a primeira vira mensagem para o operador e, com
    dispensa, pode ser contornada; a segunda é bug.
    """


def em_campo_por_categoria(categoria_id, localidade_id):
    """Quantas peças desta categoria estão fora agora, neste CD.

    Soma tudo que saiu e não voltou. É a metade "viva" do saldo — a outra é o
    total cadastrado no estoque.

    🔴 H5 — "fora" NÃO é o mesmo que "movimentação aberta". Até a H4 esta soma
    exigia `data_retorno IS NULL`, e por isso a peça que o coletor devolveu SEM
    trazer sumia da conta: a movimentação fechava, a bateria continuava na rua, e
    o saldo disponível voltava a contá-la como se estivesse na prateleira. O CD
    passava a acreditar que tinha mais bateria do que tem.

    A verdade é `qtd_devolvida < qtd_saida`, independente da movimentação estar
    aberta. Se a peça foi dada como perdida, a correção é dar baixa no CADASTRO
    — não fingir que ela voltou.
    """
    total = (db.session.query(
                db.func.sum(MovimentacaoComplemento.qtd_saida
                            - MovimentacaoComplemento.qtd_devolvida))
             .join(Movimentacao,
                   Movimentacao.id == MovimentacaoComplemento.movimentacao_id)
             .join(Coletor, Coletor.id == Movimentacao.coletor_id)
             .filter(MovimentacaoComplemento.categoria_id == categoria_id,
                     MovimentacaoComplemento.qtd_devolvida
                     < MovimentacaoComplemento.qtd_saida,
                     Coletor.localidade_id == localidade_id)
             .scalar())
    return int(total or 0)


def disponivel_por_categoria(categoria_id, localidade_id):
    """Quantas peças há para emprestar agora. DERIVADO, nunca armazenado.

    total cadastrado − o que está em campo. Guardar um contador de saldo seria
    criar um número que dessincroniza no primeiro lançamento corrigido; aqui ele
    é sempre a verdade, ao custo de uma soma.

    Sem linha de estoque, devolve None = "não controlado". Diferente de zero: o
    CD que nunca cadastrou quantas baterias tem não deve ser impedido de operar
    por causa disso.
    """
    linha = EstoqueComplemento.query.filter_by(categoria_id=categoria_id,
                                               localidade_id=localidade_id).first()
    if linha is None:
        return None
    return linha.qtd_cadastrada - em_campo_por_categoria(categoria_id, localidade_id)


def baterias_compativeis(empresa_id, coletor):
    """Categorias de bateria que servem NESTE coletor, a melhor primeiro.

    A ordem importa para a tela: a primeira é a que vem pré-selecionada. Com
    coletor de climatizado só sobra a climatizada; no seco as duas servem, e a
    específica do seco vem antes — a climatizada é mais cara e deve ficar para
    quem precisa dela.
    """
    todas = (CategoriaComplemento.query
             .filter_by(empresa_id=empresa_id, ativa=True)
             .filter(CategoriaComplemento.e_bateria == True)      # noqa: E712
             .order_by(CategoriaComplemento.nome).all())
    camara = coletor.camara
    # 🔴 B1 — antes daqui, "é bateria" era ADIVINHADO por `controle=QUANTIDADE`.
    # Funcionava enquanto bateria era a única coisa contada; no dia em que
    # alguém cadastrasse "CABO USB" por quantidade, o popup de bateria ofereceria
    # cabo. A marca `e_bateria` diz o que a heurística supunha.
    #
    # A nota antiga continua valendo para a CÂMARA: NÃO exigir `camara_atendida`
    # preenchido. A restrição térmica é OPCIONAL, e uma bateria cadastrada como
    # "sem restrição" é bateria igual — filtrá-la fora fazia a sugestão voltar
    # vazia e o popup nunca abria para quem não preencheu o campo.
    # Duas perguntas diferentes: `serve_coletor` é de QUE equipamento a peça é,
    # `atende_camara` é se ela aguenta o ambiente. Sem a primeira, uma bateria de
    # headset cadastrada como "só seco" era sugerida para o coletor do seco — e
    # ainda ganhava a vez da bateria certa no desempate por nome.
    compativeis = [c for c in todas if c.serve_coletor and c.atende_camara(camara)]
    compativeis.sort(key=lambda c: _prioridade_para(c, camara))
    return compativeis


def _prioridade_para(categoria, camara):
    """Ordem de sugestão: a mais específica primeiro, a coringa por último.

    A primeira da lista é a que a tela pré-seleciona, então a ordem é decisão de
    operação: gastar a bateria climatizada (mais cara) num coletor do seco seria
    desperdiçar o estoque bom.
    """
    equivalente = (COMPLEMENTO_CAMARA_CLIMATIZADO
                   if camara in ('RESFRIADO', 'CONGELADO')
                   else COMPLEMENTO_CAMARA_SECO)
    if categoria.camara_atendida == equivalente:
        return 0                                  # feita para esta câmara
    if categoria.camara_atendida:
        return 1                                  # serve, mas é de outra faixa
    return 2                                      # sem restrição: coringa


def sugerir_para_coletor(empresa_id, coletor):
    """O que a tela propõe ao identificar o coletor. Só leitura.

    Devolve baterias compatíveis (com disponibilidade) e as peças pareadas —
    o headset de voice picking que acompanha ESTE coletor (D2). É o que permite
    ao operador confirmar em vez de digitar.
    """
    sugestao = {'camara': coletor.camara, 'baterias': [], 'pareados': []}

    for cat in baterias_compativeis(empresa_id, coletor):
        sugestao['baterias'].append({
            'categoria_id': cat.id,
            'nome': cat.nome,
            'disponivel': disponivel_por_categoria(cat.id, coletor.localidade_id),
        })

    pareados = (ItemComplementar.query
                .filter_by(coletor_id=coletor.id, ativo=True)
                .join(CategoriaComplemento,
                      CategoriaComplemento.id == ItemComplementar.categoria_id)
                # '= 1' é válido no SQL Server; `.is_(True)` geraria 'IS 1',
                # que o T-SQL recusa — o IS de lá só aceita NULL.
                .filter(CategoriaComplemento.ativa == True)   # noqa: E712
                .all())
    for item in pareados:
        sugestao['pareados'].append({
            'item_id': item.id,
            'identificador': item.identificador,
            'categoria_id': item.categoria_id,
            'categoria': item.categoria.nome,
            'em_uso': _item_em_campo(item.id),
        })
    return sugestao


def _item_em_campo(item_id):
    """Diz se esta peça identificada está fora agora (não voltou da última saída).

    ⚠️ `db.session.query(<query>.exists()).scalar()` seria o idioma natural do
    SQLAlchemy, mas gera `SELECT EXISTS (...)` — e o T-SQL não tem EXISTS como
    expressão escalar, só dentro de WHERE. O SQLite aceita, o SQL Server recusa.
    `.first() is not None` custa o mesmo e funciona nos dois.

    🔴 H5 — mesma correção de `em_campo_por_categoria`: a peça que não voltou
    continua fora mesmo com a movimentação fechada. Sem isso o headset perdido
    voltava a ser oferecido na retirada seguinte, como se estivesse na gaveta.
    """
    return (MovimentacaoComplemento.query
            .filter(MovimentacaoComplemento.item_id == item_id,
                    MovimentacaoComplemento.qtd_devolvida < MovimentacaoComplemento.qtd_saida)
            .first() is not None)


def validar_bateria(categoria, coletor, dispensa_motivo=None):
    """Portão de compatibilidade do D6. Levanta `ComplementoRecusado` ou passa.

    🔴 BLOQUEIA por decisão do Gabriel — bateria seca no congelado morre no meio
    do turno. Mas com DISPENSA AUDITADA, no mesmo desenho do portão de NF do TF4:
    bloqueio puro tem falha de modo — sem a bateria certa em estoque às 3h da
    manhã, o operador entrega por fora do sistema e some o registro junto com a
    regra. A dispensa mantém as duas coisas.
    """
    if categoria.atende_camara(coletor.camara):
        return
    if dispensa_motivo:
        return
    raise ComplementoRecusado(
        f'"{categoria.nome}" não pode ser usada em coletor de '
        f'{(coletor.camara or "SECO").lower()}. Escolha uma bateria compatível ou '
        f'registre uma justificativa para liberar a exceção.')


def registrar_saida(movimentacao, coletor, selecoes, operador=None):
    """Grava o que saiu junto com o coletor. Chamado DENTRO da transação da retirada.

    `selecoes` é uma lista de dicts vinda da tela:
        {'categoria_id': 3, 'qtd': 1}                      → bateria
        {'categoria_id': 7, 'item_id': 47}                 → peça identificada
        {'categoria_id': 3, 'qtd': 1, 'dispensa': 'texto'} → exceção do D6

    Não faz commit: quem chama é dono da transação, e a retirada precisa que o
    complemento e a movimentação subam ou caiam juntos.
    """
    empresa_id = coletor.localidade.empresa_id if coletor.localidade else None
    gravadas = []

    for escolha in selecoes or []:
        categoria = db.session.get(CategoriaComplemento, escolha.get('categoria_id'))
        # Categoria de outra empresa é inexistente — a família B do S2 vale aqui
        # como em qualquer id que chega pelo formulário.
        if categoria is None or (empresa_id is not None
                                 and categoria.empresa_id != empresa_id):
            raise ComplementoRecusado('Complemento inválido.')
        if not categoria.ativa:
            raise ComplementoRecusado(f'"{categoria.nome}" está desativado.')

        dispensa = (escolha.get('dispensa') or '').strip() or None
        item_id = escolha.get('item_id')

        if categoria.por_unidade:
            item = db.session.get(ItemComplementar, item_id) if item_id else None
            if item is None or item.categoria_id != categoria.id or not item.ativo:
                raise ComplementoRecusado(f'Peça inválida para "{categoria.nome}".')
            qtd = 1
        else:
            validar_bateria(categoria, coletor, dispensa)
            # ⚠️ NÃO usar `or BATERIAS_PADRAO` aqui: zero é falsy em Python, e o
            # operador que zerou a quantidade veria uma bateria sair assim mesmo.
            # Ausência de valor é que vira o padrão; zero é uma escolha explícita.
            qtd_bruta = escolha.get('qtd')
            qtd = BATERIAS_PADRAO if qtd_bruta is None else int(qtd_bruta)
            if qtd < 1:
                continue                      # zero = não levou; não vira linha
            saldo = disponivel_por_categoria(categoria.id, coletor.localidade_id)
            # Saldo None = CD sem estoque cadastrado: não controlado, deixa passar.
            # Saldo insuficiente NÃO bloqueia: o estoque é um número digitado por
            # alguém, e travar a operação por causa dele puniria o operador por um
            # cadastro desatualizado. O negativo aparece na tela de estoque.
            if saldo is not None and qtd > saldo:
                dispensa = dispensa or f'saldo insuficiente ({saldo} disponível)'
            item = None

        gravadas.append(MovimentacaoComplemento(
            movimentacao_id=movimentacao.id,
            categoria_id=categoria.id,
            item_id=item.id if item else None,
            qtd_saida=qtd,
            qtd_devolvida=0,
            dispensa_motivo=dispensa,
            dispensa_por=operador if dispensa else None,
        ))

    for linha in gravadas:
        db.session.add(linha)
    return gravadas


def ler_selecoes_do_form(form):
    """Traduz o POST da tela de balcão numa lista de seleções.

    O formulário manda:
        complemento_categoria = [3, 7]      (repetido)
        complemento_qtd_3     = 1
        complemento_item_7    = 47
        complemento_dispensa_3= 'texto'

    Formato achatado de propósito: o form da retirada é montado por JS num campo
    por complemento, e nomear por id evita depender da ordem dos campos — que é a
    fonte clássica de bug quando o operador troca a seleção antes de enviar.

    🔴 A categoria repetida na lista é DESCARTADA. Quantidade, item e dispensa
    vêm de campos nomeados por categoria e lidos com `form.get()`, ou seja, um
    valor só — então a categoria repetida não descreve duas peças diferentes,
    descreve a MESMA seleção duas vezes. Sem o descarte, cada repetição virava
    uma linha de saída: `complemento_categoria=1` duas vezes com `qtd_1=2` dava
    4 baterias em 2 linhas, e a devolução mostrava "BATERIA PADRAO" duplicado
    para o operador conferir. Pela tela isso não acontece (o popup monta um
    campo por categoria); num POST montado à mão, acontece.
    """
    selecoes = []
    vistas = set()   # pelo id JÁ convertido: '1' e '01' são a mesma categoria
    for bruto in form.getlist('complemento_categoria'):
        if not str(bruto).isdigit():
            continue
        cid = int(bruto)
        if cid in vistas:
            continue
        vistas.add(cid)
        qtd_bruta = form.get(f'complemento_qtd_{cid}')
        item_bruto = form.get(f'complemento_item_{cid}')
        selecoes.append({
            'categoria_id': cid,
            'qtd': int(qtd_bruta) if str(qtd_bruta or '').isdigit() else BATERIAS_PADRAO,
            'item_id': int(item_bruto) if str(item_bruto or '').isdigit() else None,
            'dispensa': form.get(f'complemento_dispensa_{cid}'),
        })
    return selecoes


# ---------------------------------------------------------------------------
# H3 — DEVOLUÇÃO
# ---------------------------------------------------------------------------

def em_campo_da_movimentacao(movimentacao):
    """O que saiu nesta movimentação e ainda não voltou.

    Alimenta o modal de conferência: o operador não precisa lembrar o que o
    colaborador levou — o sistema mostra. É a diferença entre cobrar e torcer
    para alguém lembrar.
    """
    pendentes = []
    for linha in movimentacao.complementos:
        if linha.faltando <= 0:
            continue
        pendentes.append({
            'linha_id': linha.id,
            'categoria_id': linha.categoria_id,
            'categoria': linha.categoria.nome if linha.categoria else '?',
            'item_id': linha.item_id,
            'identificador': linha.item.identificador if linha.item else None,
            'qtd_saida': linha.qtd_saida,
            'qtd_devolvida': linha.qtd_devolvida,
            'faltando': linha.faltando,
        })
    return pendentes


def conferir_devolucao(movimentacao, conferencias, agora=None):
    """Baixa o que voltou. Chamado DENTRO da transação da devolução.

    `conferencias` é {linha_id: quantidade_devolvida_agora}, vindo do modal.

    A devolução NUNCA é bloqueada por falta: o coletor volta de qualquer jeito e
    o que não voltou fica registrado como pendência. Travar a devolução porque o
    headset sumiu deixaria o coletor preso em campo — punindo o inventário para
    cobrar um acessório.

    Devolve a lista de linhas que ficaram com falta, para a tela avisar.
    """
    from datetime import datetime
    agora = agora or datetime.now()
    faltantes = []

    for linha in movimentacao.complementos:
        if linha.faltando <= 0:
            continue
        devolvido = conferencias.get(linha.id)
        if devolvido is None:
            devolvido = 0                      # não conferido = não voltou
        devolvido = max(0, min(int(devolvido), linha.qtd_saida))
        linha.qtd_devolvida = devolvido
        if devolvido:
            linha.devolvido_em = agora
        if linha.faltando > 0:
            faltantes.append(linha)

    return faltantes


def ler_conferencias_do_form(form):
    """Traduz o POST do modal de conferência em {linha_id: qtd}.

    Campo por linha (`conferencia_<id>`) e não por posição: o operador pode
    marcar em qualquer ordem, e depender de ordem é a origem clássica de baixa no
    item errado.
    """
    conferencias = {}
    for chave, valor in form.items():
        if not chave.startswith('conferencia_'):
            continue
        bruto = chave[len('conferencia_'):]
        if bruto.isdigit() and str(valor).isdigit():
            conferencias[int(bruto)] = int(valor)
    return conferencias


def pendencias_do_coletor(coletor_id):
    """Tudo que já saiu com este coletor e não voltou — para o histórico.

    Uma query só, com joinedload das duas pontas: o histórico do coletor é uma
    tela de leitura e já sofreu com N+1 antes (o custo acumulado de manutenção
    foi reescrito por isso).
    """
    from sqlalchemy.orm import joinedload
    return (MovimentacaoComplemento.query
            .join(Movimentacao, Movimentacao.id == MovimentacaoComplemento.movimentacao_id)
            .filter(Movimentacao.coletor_id == coletor_id,
                    MovimentacaoComplemento.qtd_devolvida < MovimentacaoComplemento.qtd_saida)
            .options(joinedload(MovimentacaoComplemento.categoria),
                     joinedload(MovimentacaoComplemento.item))
            .all())


# ---------------------------------------------------------------------------
# H4 — a pendência ONDE ela é vista (inventário e dashboard)
# ---------------------------------------------------------------------------

def prazo_horas(empresa_id=None):
    """Quantas horas um complemento pode ficar fora antes de virar cobrança.

    Política por empresa (mecanismo do TF3, D4), não constante: o turno da MB tem
    ~9h e o Gabriel travou 1 dia como régua, mas outro cliente opera em 12h ou
    48h — e isso não pode exigir deploy. `politica_empresa` já degrada para o
    default do registry quando a empresa não gravou linha própria.

    Sem `empresa_id`, resolve a empresa da sessão.

    ⚠️ Fora de request não há sessão, e `politica_empresa` sem `empresa_id`
    tentaria lê-la. Acontece de verdade: os scripts de validação e o cenário de
    teste rodam em app_context puro. Aí o default é a resposta, não uma exceção —
    um relógio de cobrança não pode derrubar o processo que só queria conferir.
    """
    from flask import has_request_context
    from app.helpers import politica_empresa
    if empresa_id is None and not has_request_context():
        return PRAZO_PADRAO_HORAS
    valor = politica_empresa('complemento_prazo_horas', empresa_id)
    return valor if valor else PRAZO_PADRAO_HORAS


def horas_fora(linha, agora=None):
    """Há quantas horas esta peça está na rua. Conta da SAÍDA, sempre.

    Mesmo quando o coletor já voltou sem ela: o que interessa para a cobrança é
    desde quando a peça está com a pessoa, não quando o coletor foi devolvido.
    """
    from datetime import datetime
    saida = linha.movimentacao.data_saida if linha.movimentacao else None
    if saida is None:
        return 0.0
    return max(((agora or datetime.now()) - saida).total_seconds() / 3600.0, 0.0)


def situacao_da_pendencia(linha, prazo=None, agora=None):
    """FALTOU / ATRASADO / NO_PRAZO — derivado, nunca gravado.

    🔴 A distinção é a regra que o Gabriel pediu: o que voltou faltando é fato
    consumado e grita na hora; o que ainda está em campo só vira cobrança depois
    do prazo. Sem isso, todo coletor em uso ficaria vermelho — e alerta que
    sempre acende ninguém lê.
    """
    if linha.movimentacao and linha.movimentacao.data_retorno is not None:
        return SITUACAO_FALTOU
    limite = prazo if prazo is not None else prazo_horas()
    return SITUACAO_ATRASADO if horas_fora(linha, agora) >= limite else SITUACAO_NO_PRAZO


def _anotar(linhas, prazo, agora):
    """Carimba `situacao` e `horas_fora` nas linhas, para template e ordenação.

    Atributos Python soltos num objeto mapeado — o SQLAlchemy ignora o que não
    conhece. É deliberado: a classificação acontece UMA vez, onde o prazo foi
    resolvido, em vez de cada template resolver política por conta própria.
    """
    for linha in linhas:
        linha.horas_fora = horas_fora(linha, agora)
        linha.situacao = situacao_da_pendencia(linha, prazo, agora)
    return linhas


def pendencias_abertas(ids_localidades=None, incluir_no_prazo=False,
                      prazo=None, agora=None):
    """Tudo que saiu com um coletor e não voltou, pronto para exibir.

    🔴 Pendência escondida não cobra ninguém. A H3 deixou a falta explícita no
    histórico DO COLETOR — mas ninguém abre o histórico de um coletor por acaso;
    abre quando já desconfia. Para a cobrança acontecer, a dívida precisa aparecer
    onde o gestor JÁ olha: o inventário e o dashboard.

    Uma query com `joinedload` de tudo que a tela toca: movimentação, coletor,
    LOCALIDADE do coletor, colaborador e categoria/item. Sem isso seria N+1 numa
    listagem — o custo acumulado do histórico já foi reescrito por esse motivo.

    A localidade entra porque `patrimonio_display` prefixa a sigla dela ('GR 253')
    — é o tipo de N+1 que se esconde atrás de uma property inocente.

    `ids_localidades` None = sem recorte (Owner); lista vazia = nada, fail-closed
    como o `get_filtro_localidade` do T3.

    🔴 H5 — por padrão devolve só o que é COBRÁVEL: o que voltou faltando, mais o
    que está em campo além do prazo. O que está fora dentro do prazo é operação
    normal, não dívida, e aparece só no rastreio. `incluir_no_prazo=True` traz
    tudo — é o que o rastreio usa.

    A classificação é feita em Python, não no WHERE: comparar `data_saida` com
    "agora menos N horas" dentro do SQL amarraria a regra ao dialeto (DATEADD ×
    datetime()) justo no ponto que já nos custou três bugs. As pendências abertas
    são dezenas, não milhões — o filtro em memória é honesto aqui.
    """
    from sqlalchemy.orm import joinedload

    if ids_localidades is not None and not ids_localidades:
        return []

    q = (MovimentacaoComplemento.query
         .join(Movimentacao, Movimentacao.id == MovimentacaoComplemento.movimentacao_id)
         .join(Coletor, Coletor.id == Movimentacao.coletor_id)
         .filter(MovimentacaoComplemento.qtd_devolvida < MovimentacaoComplemento.qtd_saida)
         .options(joinedload(MovimentacaoComplemento.categoria),
                  joinedload(MovimentacaoComplemento.item),
                  joinedload(MovimentacaoComplemento.movimentacao)
                  .joinedload(Movimentacao.coletor)
                  .joinedload(Coletor.localidade),
                  joinedload(MovimentacaoComplemento.movimentacao)
                  .joinedload(Movimentacao.colaborador))
         .order_by(Movimentacao.data_retorno.desc(), Movimentacao.data_saida.desc()))

    if ids_localidades is not None:
        q = q.filter(Coletor.localidade_id.in_(ids_localidades))

    linhas = _anotar(q.all(), prazo if prazo is not None else prazo_horas(), agora)
    if incluir_no_prazo:
        return linhas
    return [l for l in linhas if l.situacao != SITUACAO_NO_PRAZO]


def pendencias_por_coletor(ids_localidades=None, **kwargs):
    """As mesmas pendências, indexadas por coletor — para marcar linhas de lista."""
    indice = {}
    for linha in pendencias_abertas(ids_localidades, **kwargs):
        indice.setdefault(linha.movimentacao.coletor_id, []).append(linha)
    return indice


def registrar_devolucao_atrasada(linha, quantidade=None, agora=None):
    """Baixa uma falta DEPOIS que o coletor já voltou.

    🔴 Até aqui, o único lugar do sistema que baixava complemento era
    `conferir_devolucao`, chamado só na devolução do coletor. Quem devolvesse a
    bateria no dia seguinte — que é a rotina, não a exceção — não tinha onde
    registrar: a falta ficava na tela para sempre e o saldo do CD ficava
    permanentemente errado para menos.

    A saída que sobrava era recontar a prateleira e ajustar a quantidade à mão,
    o que apaga de quem era a cobrança. Some o rastro de quem devia.

    `quantidade=None` devolve tudo o que falta, que é o caso comum de um clique.
    Nunca passa de `qtd_saida`: devolver mais do que saiu criaria bateria do
    nada no estoque.
    """
    from datetime import datetime

    if linha.faltando <= 0:
        raise ComplementoRecusado('Esta pendência já está quitada.')

    pedido = linha.faltando if quantidade is None else int(quantidade)
    if pedido < 1:
        raise ComplementoRecusado('Informe quantas voltaram.')

    devolvidas = min(pedido, linha.faltando)
    linha.qtd_devolvida += devolvidas
    linha.devolvido_em = agora or datetime.now()
    return devolvidas


def descrever_pendencia(linha):
    """Texto curto e completo de uma falta: '2x BATERIA SECO' / '1x HEADSET (HS-047)'.

    Concentrado aqui porque a mesma frase aparece em quatro telas (inventário,
    dashboard, histórico e o flash da devolução) — e uma pendência descrita de
    jeitos diferentes em cada lugar é uma pendência que ninguém confere.
    """
    nome = linha.categoria.nome if linha.categoria else '?'
    texto = f'{linha.faltando}x {nome}'
    if linha.item:
        texto += f' ({linha.item.identificador})'
    return texto


# ---------------------------------------------------------------------------
# H5 — RASTREIO: onde está cada complemento AGORA
# ---------------------------------------------------------------------------
# 🔴 A pergunta do Gabriel foi "onde está cada item ou com quem está cada item",
# e ela tem DUAS respostas diferentes por causa da D4:
#
#   UNIDADE     (headset, suporte) — identidade própria. Dá para dizer onde a
#               peça HS-2001 está: no CD, ou com o Fulano no coletor tal.
#   QUANTIDADE  (bateria)          — sem identidade. Não existe "a bateria nº12".
#               A resposta honesta é agregada: quantas estão fora e com quem.
#
# Duas funções em vez de uma justamente para não fingir precisão que o cadastro
# não tem. Uma tela única que dissesse "bateria #12 com o Fulano" estaria
# inventando um número.

def rastreio_itens(ids_localidades=None, empresa_id=None, agora=None,
                   incluir_inativos=False):
    """Onde está cada peça IDENTIFICADA. Uma linha por item, sem exceção.

    Devolve dicts com `estado` = 'ESTOQUE' | situação da pendência, mais quem
    está com ela. O item que não voltou continua listado como fora e COM a
    pessoa — o coletor ter sido devolvido não traz o headset de volta.

    Duas queries, não N: uma para os itens, outra para tudo que está fora. O
    cruzamento é em memória.

    `incluir_inativos` existe para o inventário da B3 — ver a nota no corpo.
    """
    from sqlalchemy.orm import joinedload

    if ids_localidades is not None and not ids_localidades:
        return []

    itens_q = (ItemComplementar.query
               .options(joinedload(ItemComplementar.categoria),
                        joinedload(ItemComplementar.localidade),
                        joinedload(ItemComplementar.coletor)))
    # B3 — o INVENTÁRIO precisa da peça desativada; o rastreio, não. Uma peça
    # desativada que some da listagem é uma peça que ninguém consegue reativar,
    # e o cadastro duplicado vira a saída óbvia. Já no rastreio ela seria ruído:
    # ninguém cobra a devolução de um headset aposentado.
    if not incluir_inativos:
        itens_q = itens_q.filter(ItemComplementar.ativo == True)    # noqa: E712
    if empresa_id is not None:
        itens_q = itens_q.filter(ItemComplementar.empresa_id == empresa_id)
    if ids_localidades is not None:
        # Item sem CD ainda não foi alocado; some da tela se filtrado fora, e um
        # item que some do rastreio é exatamente o que não pode acontecer aqui.
        itens_q = itens_q.filter(db.or_(
            ItemComplementar.localidade_id.in_(ids_localidades),
            ItemComplementar.localidade_id.is_(None)))
    itens = itens_q.order_by(ItemComplementar.identificador).all()

    fora = {}
    for linha in pendencias_abertas(ids_localidades, incluir_no_prazo=True,
                                    agora=agora):
        if linha.item_id and linha.item_id not in fora:
            fora[linha.item_id] = linha          # a query já vem da mais recente

    mapa = []
    for item in itens:
        linha = fora.get(item.id)
        mov = linha.movimentacao if linha else None
        mapa.append({
            'item': item,
            'categoria': item.categoria.nome if item.categoria else '?',
            'estado': linha.situacao if linha else 'ESTOQUE',
            'localidade': item.localidade,
            'coletor': (mov.coletor if mov else None) or item.coletor,
            'colaborador': mov.colaborador if mov else None,
            're': (mov.re_colaborador if mov else None),
            'desde': mov.data_saida if mov else None,
            'coletor_voltou': mov.data_retorno if mov else None,
            'horas_fora': linha.horas_fora if linha else 0.0,
        })
    return mapa


def rastreio_quantidades(ids_localidades=None, empresa_id=None, agora=None,
                         so_bateria=None):
    """Saldo por categoria/CD, e COM QUEM está o que saiu.

    Uma entrada por par (categoria de quantidade, CD) que tenha estoque cadastrado
    OU alguma peça fora — um par sem nenhum dos dois não é informação, é ruído.

    `portadores` é a resposta de cobrança: quem está com quantas, desde quando,
    em qual coletor. Não diz QUAL bateria porque o cadastro não sabe — e inventar
    isso seria pior do que admitir.
    """
    if ids_localidades is not None and not ids_localidades:
        return []

    # `so_bateria` deixa a MESMA função servir as duas telas: a de baterias pede
    # True, o rastreio pede False, e None mantém o comportamento de antes. Um
    # segundo cálculo de saldo em outro arquivo é a receita para os dois números
    # discordarem no dia em que só um for corrigido.
    cat_q = CategoriaComplemento.query.filter(
        CategoriaComplemento.controle == COMPLEMENTO_QUANTIDADE,
        CategoriaComplemento.ativa == True)                        # noqa: E712
    if so_bateria is not None:
        cat_q = cat_q.filter(CategoriaComplemento.e_bateria == bool(so_bateria))
    if empresa_id is not None:
        cat_q = cat_q.filter(CategoriaComplemento.empresa_id == empresa_id)
    categorias = {c.id: c for c in cat_q.order_by(CategoriaComplemento.nome).all()}
    if not categorias:
        return []

    est_q = EstoqueComplemento.query.filter(
        EstoqueComplemento.categoria_id.in_(list(categorias)))
    if ids_localidades is not None:
        est_q = est_q.filter(EstoqueComplemento.localidade_id.in_(ids_localidades))
    cadastrado = {(e.categoria_id, e.localidade_id): e.qtd_cadastrada
                  for e in est_q.all()}

    portadores = {}
    for linha in pendencias_abertas(ids_localidades, incluir_no_prazo=True,
                                    agora=agora):
        if linha.item_id or linha.categoria_id not in categorias:
            continue                       # peça identificada tem tela própria
        mov = linha.movimentacao
        coletor = mov.coletor if mov else None
        if coletor is None:
            continue
        portadores.setdefault((linha.categoria_id, coletor.localidade_id), []).append(linha)

    localidades = {}
    for _cat_id, loc_id in list(cadastrado) + list(portadores):
        if loc_id is not None and loc_id not in localidades:
            localidades[loc_id] = db.session.get(Localidade, loc_id)

    saldos = []
    for chave in sorted(set(list(cadastrado) + list(portadores)),
                        key=lambda k: (categorias[k[0]].nome,
                                       getattr(localidades.get(k[1]), 'sigla', ''))):
        cat_id, loc_id = chave
        linhas = portadores.get(chave, [])
        em_campo = sum(l.faltando for l in linhas)
        qtd = cadastrado.get(chave)
        saldos.append({
            'categoria': categorias[cat_id],
            'localidade': localidades.get(loc_id),
            'cadastrada': qtd,
            'em_campo': em_campo,
            # None = CD que nunca cadastrou quantas tem. Diferente de zero, e a
            # tela precisa dizer isso em vez de mostrar um saldo inventado.
            'disponivel': None if qtd is None else qtd - em_campo,
            'portadores': sorted(linhas, key=lambda l: -l.horas_fora),
            'atrasados': sum(1 for l in linhas if l.situacao != SITUACAO_NO_PRAZO),
        })
    return saldos


def tempo_fora(horas):
    """'3h 20m' / '2d 4h' — legível de relance, que é o ponto de um alerta.

    Quem varre a tela de cobrança precisa comparar tempos com o olho. "52.34
    horas" obriga a fazer conta mental; "2d 4h" não. Acima de um dia o minuto
    deixa de importar e vira ruído.
    """
    horas = max(float(horas or 0), 0)
    if horas < 1:
        return f'{int(horas * 60)}m'
    if horas < 24:
        return f'{int(horas)}h {int((horas % 1) * 60)}m'
    return f'{int(horas // 24)}d {int(horas % 24)}h'


# ---------------------------------------------------------------------------
# B2 — o painel de baterias, um card por CD
# ---------------------------------------------------------------------------

def painel_baterias(ids_localidades=None, empresa_id=None, agora=None):
    """Um card por CD CADASTRADO, mesmo os que estão zerados.

    🔴 Começa pelas LOCALIDADES, não pelos saldos. `rastreio_quantidades` só
    devolve par que tem estoque ou movimento — usá-la sozinha faria o CD sem
    bateria nenhuma sumir da tela, e um CD que some é um CD que ninguém lembra
    de abastecer. Aqui ele aparece zerado, que é a informação.

    Reusa o cálculo da H5 em vez de recontar: um segundo saldo em outro arquivo
    é a receita para os dois números discordarem no dia em que só um for
    corrigido.

    `cadastrada` None significa "ninguém contou ainda", diferente de zero. A
    distinção vem da H5 e a tela precisa mantê-la — mostrar 0 para quem nunca
    preencheu faz o CD parecer sem bateria.
    """
    from app.models import Localidade

    if ids_localidades is not None and not ids_localidades:
        return []

    loc_q = Localidade.query
    if empresa_id is not None:
        loc_q = loc_q.filter(Localidade.empresa_id == empresa_id)
    if ids_localidades is not None:
        loc_q = loc_q.filter(Localidade.id.in_(ids_localidades))
    localidades = loc_q.order_by(Localidade.sigla).all()

    saldos = rastreio_quantidades(ids_localidades, empresa_id, agora,
                                  so_bateria=True)
    por_cd = {}
    for linha in saldos:
        if linha['localidade'] is not None:
            por_cd.setdefault(linha['localidade'].id, []).append(linha)

    painel = []
    for loc in localidades:
        linhas = por_cd.get(loc.id, [])
        # Só soma o que foi informado; se NENHUMA categoria tem estoque no CD, o
        # total continua None — "ninguém contou", e não "contaram e deu zero".
        informadas = [l for l in linhas if l['cadastrada'] is not None]
        cadastrada = sum(l['cadastrada'] for l in informadas) if informadas else None
        em_campo = sum(l['em_campo'] for l in linhas)


        painel.append({
            'localidade': loc,
            'cadastrada': cadastrada,
            'em_campo': em_campo,
            'disponivel': None if cadastrada is None else cadastrada - em_campo,
            # PEÇAS atrasadas, não empréstimos: "2 em atraso" no card significa
            # duas baterias na rua, que é o número que o CD precisa repor.
            'atrasadas': sum(p.faltando for l in linhas for p in l['portadores']
                             if p.situacao != SITUACAO_NO_PRAZO),
            # 🔴 A quebra é POR TIPO, e não o par fixo comum/climatizada. Os
            # tipos são quantos a operação precisar — bateria de headset já está
            # no horizonte — e cada um carrega a própria aparência.
            #
            # Só entra quem TEM quantidade: um tipo zerado no CD é um tipo que
            # aquele CD não usa, e mostrá-lo enche o card de zeros que não
            # ajudam a decidir nada.
            'tipos': [l for l in informadas if l['cadastrada']],
            'linhas': linhas,
        })
    return painel


def categorias_de_bateria(empresa_id):
    """As categorias que a tela de baterias administra, climatizada primeiro.

    A ordem é a do cadastro em dois cartões: gelo à esquerda, bateria comum à
    direita — a mesma dos cards, para o olho não trocar uma pela outra.
    """
    return (CategoriaComplemento.query
            .filter_by(empresa_id=empresa_id, ativa=True)
            .filter(CategoriaComplemento.e_bateria == True)      # noqa: E712
            .order_by(CategoriaComplemento.camara_atendida.desc(),
                      CategoriaComplemento.nome)
            .all())


# ---------------------------------------------------------------------------
# B4 — O VÍNCULO PEÇA ↔ COLETOR, visto de dentro do coletor
# ---------------------------------------------------------------------------
# O pareamento sempre existiu (D2, voice picking): o headset trabalha com UM
# coletor. O que muda na B4 é ONDE ele se decide. Estava no catálogo de peças,
# onde a pergunta é "o que existe para emprestar?"; passa para a página do
# coletor, onde a pergunta é "o que sai junto com ESTE aqui?" — que é a pergunta
# que alguém realmente faz, e a convenção do mercado de gestão de ativos.


def pecas_do_coletor(coletor):
    """As peças que acompanham este coletor hoje, em ordem de identificador.

    Uma consulta só, com a categoria junto: esta lista é desenhada dentro de uma
    tela que já pagou uma reescrita por N+1 (o custo acumulado do histórico).
    """
    from sqlalchemy.orm import joinedload

    if coletor is None:
        return []
    return (ItemComplementar.query
            .options(joinedload(ItemComplementar.categoria))
            .filter(ItemComplementar.coletor_id == coletor.id)
            .order_by(ItemComplementar.identificador)
            .all())


def pecas_para_vincular(coletor, empresa_id=None):
    """Candidatas a acompanhar este coletor, cada uma com o seu porém.

    Devolve dicts com `item`, `com_outro` (o coletor que hoje leva a peça) e
    `serve` (a câmara bate, D5). Os dois avisos existem porque as duas situações
    são LEGÍTIMAS e o operador precisa vê-las antes de confirmar, não depois:

        · roubar a peça de outro coletor é o caso real de quando o titular vai
          para manutenção — o vínculo é um só, então vincular aqui desfaz lá;
        · a peça de ambiente seco não serve no congelado. Aqui isso é AVISO e
          não trava: o vínculo é cadastro, e quem barra de verdade é a retirada
          (H2), que já pede justificativa. Duas travas para a mesma regra viram
          duas versões dela.

    O escopo é geográfico de propósito: peça de outro CD não entra, porque ela
    está fisicamente em outro lugar. A peça SEM CD entra — é justamente a que
    precisa de destino, e vinculá-la resolve o buraco que a B3 escancarou.
    """
    from sqlalchemy.orm import joinedload

    if coletor is None:
        return []
    if empresa_id is None and coletor.localidade is not None:
        empresa_id = coletor.localidade.empresa_id

    q = (ItemComplementar.query
         .options(joinedload(ItemComplementar.categoria),
                  joinedload(ItemComplementar.coletor))
         .join(CategoriaComplemento,
               ItemComplementar.categoria_id == CategoriaComplemento.id)
         .filter(ItemComplementar.ativo == True)                       # noqa: E712
         .filter(CategoriaComplemento.ativa == True)                   # noqa: E712
         .filter(CategoriaComplemento.e_bateria == False)              # noqa: E712
         .filter(db.or_(ItemComplementar.coletor_id != coletor.id,
                        ItemComplementar.coletor_id.is_(None))))
    if empresa_id is not None:
        q = q.filter(ItemComplementar.empresa_id == empresa_id)
    if coletor.localidade_id is not None:
        q = q.filter(db.or_(ItemComplementar.localidade_id == coletor.localidade_id,
                            ItemComplementar.localidade_id.is_(None)))

    candidatas = []
    for item in q.order_by(ItemComplementar.identificador).all():
        cat = item.categoria
        candidatas.append({
            'item': item,
            'com_outro': item.coletor,
            'serve': cat.atende_camara(coletor.camara) if cat else True,
        })
    # As LIVRES vêm primeiro. Roubar a peça de outro coletor é legítimo, mas é a
    # exceção — e num CD onde todas estão pareadas (o cenário de teste é assim)
    # a lista inteira virava aviso, fazendo o excepcional parecer o normal. A
    # tela agrupa; a ordem aqui garante que o agrupamento não dependa dela.
    candidatas.sort(key=lambda c: (c['com_outro'] is not None,
                                   c['item'].identificador))
    return candidatas
