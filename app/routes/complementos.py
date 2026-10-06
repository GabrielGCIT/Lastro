"""
Catálogo de COMPLEMENTOS do handheld (pacote H, fatia H1).

O que o colaborador leva junto com o coletor: baterias, headsets de voice picking,
suportes. Aqui mora só o CADASTRO — quem controla a saída e a devolução é a tela
de movimentação, nas fatias H2 e H3. Esta fatia é deliberadamente invisível para
o operador de balcão: nada do que existe neste arquivo altera o fluxo de retirada
que já está em produção.

Depois da B1/B2/B3 este arquivo atende DUAS telas, e elas não são a mesma coisa:

    /complementos — INVENTÁRIO das peças com patrimônio (headset nº 47, suporte
                    tal), no desenho do inventário de coletores: filtro na URL,
                    seleção múltipla e colunas secundárias sob demanda. Os TIPOS
                    ficam aqui também, recolhidos — são cadastro raro.
    /baterias     — a bateria, que é fungível e contada por CD (B2).

O PAREAMENTO (o headset que faz par com um coletor, D2) saiu do inventário na
B3: é decisão sobre o coletor, e vai para dentro do detalhe dele na B4. A rota
`item_parear` continua aqui, íntegra e testada, esperando o novo ponto de
entrada.

Escopo: `empresa_para_escrita` (S1) decide o tenant, e `get_filtro_localidade`
(T3, fail-closed) decide quais CDs o usuário enxerga. Toda leitura e toda escrita
passam pelos dois — é a disciplina que o pacote S1/S2/S3 estabeleceu depois de a
classe de bug `empresa_id` ter vazado três vezes.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from app import db
from app.models import (CategoriaComplemento, ItemComplementar, EstoqueComplemento,
                        Coletor, Localidade, COMPLEMENTO_CAMARAS,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE,
                        BATERIA_ICONES, BATERIA_CORES, ICONE_BATERIA_PADRAO,
                        COR_BATERIA_PADRAO,
                        EQUIPAMENTO_COLETOR, EQUIPAMENTOS)
from app.helpers import (permissao_required, registrar_log, empresa_para_escrita,
                         get_filtro_localidade, localidade_para_escrita, parse_int,
                         voltar_seguro)

complementos_bp = Blueprint('complementos', __name__)

# Mesma permissão do inventário de coletores: quem administra o parque de
# equipamentos administra o que sai junto com ele. Não se cria permissão nova
# para uma tela que pertence ao mesmo papel.
_PERMISSAO = 'admin.inventario_ativos'


def _empresa():
    """Tenant cujo catálogo esta tela gerencia. Ver helpers.empresa_para_escrita."""
    return empresa_para_escrita()


def _categorias_da_empresa(empresa_id, apenas_ativas=False, incluir_bateria=False):
    """Categorias do catálogo. Bateria fica de FORA por padrão (B1).

    A tela de complementos passou a ser sobre peça com patrimônio; bateria tem
    tela própria. Mas o padrão precisa ser explícito no parâmetro: quem chama
    para operar (a sugestão do balcão) ainda quer as duas, e um filtro implícito
    ali faria a bateria sumir da retirada sem ninguém entender por quê.
    """
    q = CategoriaComplemento.query
    if empresa_id is not None:
        q = q.filter_by(empresa_id=empresa_id)
    if not incluir_bateria:
        q = q.filter(CategoriaComplemento.e_bateria == False)   # noqa: E712
    if apenas_ativas:
        # noqa: E712 — ver a nota em app/complementos.py: .is_(True) vira
        # 'IS 1' e o SQL Server recusa.
        q = q.filter(CategoriaComplemento.ativa == True)  # noqa: E712
    return q.order_by(CategoriaComplemento.ativa.desc(), CategoriaComplemento.nome).all()


def _localidades_visiveis():
    """CDs que o usuário enxerga — fail-closed pelo escopo geográfico do T3."""
    permitidas = get_filtro_localidade()
    q = Localidade.query
    if permitidas is not None:
        if not permitidas:
            return []
        q = q.filter(Localidade.id.in_(permitidas))
    return q.order_by(Localidade.sigla).all()


@complementos_bp.route('/complementos')
@permissao_required(_PERMISSAO)
def index():
    """INVENTÁRIO das peças com patrimônio (B3).

    Antes eram três passos numerados de peso igual — tipo, estoque contado e
    peça — porque a bateria morava aqui e era ela quem exigia o passo do meio.
    Com a bateria em tela própria (B1/B2), o que sobra é o que a tela deveria
    ter sido desde o começo: uma lista de peças identificadas. O desenho é o
    mesmo do inventário de coletores, e de propósito — quem administra o parque
    já sabe operar aquela tela, e duas telas do mesmo papel com convenções
    diferentes obrigam a reaprender o que já se sabia.

    Da U1 vêm duas das três coisas que fazem a lista trabalhar: filtros na URL
    (dá para favoritar a visão) e seleção múltipla com ação em lote. A terceira
    — colunas secundárias atrás de um controle — foi RECUSADA pelo Gabriel na
    validação de 08/09, olhando a tela com dado real: quem administra o parque
    quer ver tudo de uma vez, e um clique a cada visita não paga a economia de
    espaço. A tabela rola dentro do próprio container nas larguras estreitas.

    A SITUAÇÃO de cada peça sai do rastreio da H5, com as mesmas palavras da
    tela de rastreio: um inventário que diz onde a peça está vale mais do que um
    que só diz que ela existe.
    """
    from app.complementos import (SITUACAO_ATRASADO, SITUACAO_FALTOU,
                                  SITUACAO_NO_PRAZO, prazo_horas, rastreio_itens)

    empresa_id = _empresa()
    categorias = _categorias_da_empresa(empresa_id)          # bateria fica de fora (B1)
    localidades = _localidades_visiveis()
    permitidas = get_filtro_localidade()

    filtro_loc = (request.args.get('localidade') or '').strip().upper() or None
    filtro_tipo = parse_int(request.args.get('tipo'))
    filtro_situacao = (request.args.get('situacao') or '').strip().upper() or None

    # 🔴 O filtro do usuário só ESTREITA o que o T3 já permitiu — nunca amplia.
    # A sigla vem da query string, e resolvê-la contra as localidades JÁ VISÍVEIS
    # (em vez de consultar o banco por sigla) é o que impede um CD de outro
    # tenant de entrar pela barra de endereços. Sigla desconhecida ⇒ lista vazia,
    # fail-closed como o resto do T3.
    ids = permitidas
    if filtro_loc:
        ids = [l.id for l in localidades if l.sigla == filtro_loc]

    linhas = rastreio_itens(ids, empresa_id, incluir_inativos=True)
    # Uma categoria marcada como bateria DEPOIS de já ter peças cadastradas
    # deixaria itens órfãos nesta tela. A fronteira da B1 vale para a listagem
    # também, não só para o catálogo de tipos.
    linhas = [r for r in linhas
              if not (r['item'].categoria and r['item'].categoria.e_bateria)]

    if filtro_loc:
        # Com um CD escolhido, a peça ainda sem CD não entra: quem filtrou por GR
        # pediu o que está em GR. Sem filtro ela aparece, com selo próprio — some
        # da lista é o único desfecho inaceitável para uma peça sem lugar.
        linhas = [r for r in linhas if r['item'].localidade_id in ids]
    if filtro_tipo:
        linhas = [r for r in linhas if r['item'].categoria_id == filtro_tipo]
    if filtro_situacao == 'INATIVA':
        linhas = [r for r in linhas if not r['item'].ativo]
    elif filtro_situacao in (SITUACAO_ATRASADO, SITUACAO_FALTOU,
                             SITUACAO_NO_PRAZO, 'ESTOQUE'):
        linhas = [r for r in linhas if r['estado'] == filtro_situacao and r['item'].ativo]

    # Peça desativada vai para o FIM. Ela precisa continuar na lista (é o único
    # caminho para reativá-la), mas no meio das ativas ela disputa a atenção de
    # quem está conferindo o que existe hoje — era assim que a listagem antiga
    # ordenava, e a ordem do rastreio, sozinha, não sabe disso.
    linhas.sort(key=lambda r: (not r['item'].ativo, r['item'].identificador))

    # Herança: um tipo CONTADO que não é bateria (ninguém criou até hoje, mas o
    # modelo permite) não pode perder a tela onde se informa a quantidade. O
    # bloco só existe se o dado existir — senão seria um passo vazio de volta.
    categorias_quantidade = [c for c in categorias if c.ativa and not c.por_unidade]
    estoques = {}
    if categorias_quantidade:
        eq = EstoqueComplemento.query.filter(
            EstoqueComplemento.categoria_id.in_([c.id for c in categorias_quantidade]))
        if empresa_id is not None:
            eq = eq.filter_by(empresa_id=empresa_id)
        if permitidas is not None:
            eq = eq.filter(EstoqueComplemento.localidade_id.in_([l.id for l in localidades]))
        estoques = {(e.categoria_id, e.localidade_id): e.qtd_cadastrada for e in eq.all()}

    return render_template(
        'complementos.html',
        linhas=linhas,
        categorias=categorias,
        # Só as ATIVAS entram no cadastro de peça: oferecer um tipo desativado
        # seria criar peça num tipo que a empresa decidiu não usar mais.
        categorias_ativas=[c for c in categorias if c.ativa],
        categorias_quantidade=categorias_quantidade,
        estoques=estoques,
        localidades=localidades,
        filtro_loc=filtro_loc,
        filtro_tipo=filtro_tipo,
        filtro_situacao=filtro_situacao,
        camaras=COMPLEMENTO_CAMARAS,
        prazo=prazo_horas(empresa_id),
        # A dívida ANTES da lista: a marcação na linha só cobra quem rolar até
        # ela, e o inventário pode ter centenas de peças.
        fora_de_prazo=sum(1 for r in linhas
                          if r['item'].ativo
                          and r['estado'] in (SITUACAO_ATRASADO, SITUACAO_FALTOU)))


@complementos_bp.route('/complementos/categoria/criar', methods=['POST'])
@permissao_required(_PERMISSAO)
def categoria_criar():
    """Cria um tipo de complemento. Nome único POR EMPRESA."""
    empresa_id = _empresa()
    if empresa_id is None:
        flash('Selecione uma empresa no topo da tela para gerenciar os tipos de peça.', 'warning')
        return redirect(url_for('complementos.index'))

    nome = request.form.get('nome', '').strip().upper()[:40]
    camara = request.form.get('camara_atendida') or None

    if not nome:
        flash('Informe o nome do tipo de peça.', 'danger')
        return redirect(url_for('complementos.index'))
    if camara is not None and camara not in COMPLEMENTO_CAMARAS:
        flash('Opção inválida em "essa peça aguenta o congelado?".', 'danger')
        return redirect(url_for('complementos.index'))
    if CategoriaComplemento.query.filter_by(empresa_id=empresa_id, nome=nome).first():
        flash(f'Já existe um tipo chamado "{nome}".', 'warning')
        return redirect(url_for('complementos.index'))

    # 🔴 B3 — o tipo criado AQUI é sempre por unidade, e a tela não pergunta mais.
    # A pergunta "você conta quantas tem, ou cada peça tem patrimônio?" só existia
    # por causa da bateria; sem ela, a segunda opção era a única com destino — e
    # oferecer duas opções em que uma leva a um beco é o defeito que a B2 já
    # tinha corrigido na tela de baterias. Bateria continua sendo contada, e é
    # criada na tela dela.
    db.session.add(CategoriaComplemento(empresa_id=empresa_id, nome=nome,
                                        controle=COMPLEMENTO_UNIDADE,
                                        camara_atendida=camara))
    db.session.commit()
    registrar_log('COMPLEMENTO_CATEGORIA_CRIAR',
                  f'Complemento "{nome}" criado (UNIDADE, câmara={camara or "qualquer"}).')
    flash(f'Tipo "{nome}" criado. Agora cadastre as peças, uma a uma, com o '
          f'patrimônio de cada uma.', 'success')
    return redirect(url_for('complementos.index'))


@complementos_bp.route('/complementos/categoria/<int:cat_id>/toggle', methods=['POST'])
@permissao_required(_PERMISSAO)
def categoria_toggle(cat_id):
    """Ativa/desativa um tipo. NÃO afeta o que já saiu — a pendência segue cobrável."""
    empresa_id = _empresa()
    cat = db.session.get(CategoriaComplemento, cat_id)
    # Isolamento: complemento de outra empresa é "inexistente" (padrão do T3/S2).
    if not cat or (empresa_id is not None and cat.empresa_id != empresa_id):
        flash('Tipo de peça não encontrado.', 'danger')
        return redirect(url_for('complementos.index'))

    cat.ativa = not cat.ativa
    db.session.commit()
    estado = 'reativado' if cat.ativa else 'desativado'
    registrar_log('COMPLEMENTO_CATEGORIA_TOGGLE', f'Complemento "{cat.nome}" {estado}.')
    flash(f'Tipo "{cat.nome}" {estado}. O que já saiu continua sendo cobrado.',
          'success')
    return redirect(url_for('complementos.index'))


@complementos_bp.route('/complementos/item/criar', methods=['POST'])
@permissao_required(_PERMISSAO)
def item_criar():
    """Cadastra uma peça identificada e, opcionalmente, o coletor com que ela faz par."""
    empresa_id = _empresa()
    if empresa_id is None:
        flash('Escolha uma empresa no topo da tela para cadastrar peças.', 'warning')
        return redirect(url_for('complementos.index'))

    identificador = request.form.get('identificador', '').strip().upper()[:60]
    categoria_id = parse_int(request.form.get('categoria_id'))
    localidade_id = parse_int(request.form.get('localidade_id'))

    cat = db.session.get(CategoriaComplemento, categoria_id) if categoria_id else None
    if not cat or cat.empresa_id != empresa_id:
        flash('Selecione um tipo de peça válido.', 'danger')
        return redirect(url_for('complementos.index'))
    if not cat.por_unidade:
        # Bateria é contada, não identificada: criar peça aqui seria inventar um
        # controle que a categoria declarou não ter.
        flash(f'"{cat.nome}" é do tipo contado: informe a quantidade no passo 2, em vez de cadastrar peça por peça.',
              'warning')
        return redirect(url_for('complementos.index'))
    if not identificador:
        flash('Informe o identificador da peça (patrimônio ou serial).', 'danger')
        return redirect(url_for('complementos.index'))
    if ItemComplementar.query.filter_by(empresa_id=empresa_id,
                                        identificador=identificador).first():
        flash(f'Já existe uma peça com o identificador "{identificador}".', 'warning')
        return redirect(url_for('complementos.index'))

    # 🔴 B3 — o PAREAMENTO saiu deste formulário. Vincular um headset a um coletor
    # é uma decisão sobre AQUELE COLETOR, não sobre o catálogo, e vai para dentro
    # do detalhe dele (B4). A rota `item_parear` continua de pé: é ela que a
    # próxima fatia chama, e desfazer o vínculo nunca deixou de ser possível.
    permitidas = get_filtro_localidade()
    if localidade_id and permitidas is not None and localidade_id not in permitidas:
        flash('Esse CD não está entre os que você acessa.', 'danger')
        return redirect(url_for('complementos.index'))

    db.session.add(ItemComplementar(
        empresa_id=empresa_id, categoria_id=cat.id, identificador=identificador,
        localidade_id=localidade_id or None))
    db.session.commit()
    registrar_log('COMPLEMENTO_ITEM_CRIAR', f'Peça "{identificador}" ({cat.nome}).')
    flash(f'Peça "{identificador}" cadastrada.', 'success')
    return redirect(url_for('complementos.index'))


@complementos_bp.route('/complementos/item/<int:item_id>/toggle', methods=['POST'])
@permissao_required(_PERMISSAO)
def item_toggle(item_id):
    """Ativa/desativa uma peça (perdida, em conserto, aposentada)."""
    empresa_id = _empresa()
    item = db.session.get(ItemComplementar, item_id)
    if not item or (empresa_id is not None and item.empresa_id != empresa_id):
        flash('Peça não encontrada.', 'danger')
        return redirect(url_for('complementos.index'))

    item.ativo = not item.ativo
    db.session.commit()
    estado = 'reativada' if item.ativo else 'desativada'
    registrar_log('COMPLEMENTO_ITEM_TOGGLE', f'Peça "{item.identificador}" {estado}.')
    flash(f'Peça "{item.identificador}" {estado}.', 'success')
    return redirect(url_for('complementos.index'))


@complementos_bp.route('/complementos/itens/mover', methods=['POST'])
@permissao_required(_PERMISSAO)
def itens_mover():
    """Move as peças marcadas para outro CD (B3, no molde da U1).

    A ação em lote existe porque o caso real é em lote: chegou uma caixa de dez
    headsets, todos cadastrados sem CD, e todos vão para o mesmo lugar. Um a um,
    é a viagem que a tela nova existe para acabar.

    🔴 Origem E destino são validados. Validar só o destino deixaria alguém
    puxar a peça de OUTRO CD para dentro do seu forjando os ids da lista — a
    família B do S2, mais tentadora num POST em massa. A peça SEM CD é a exceção
    deliberada: ela não pertence a lugar nenhum ainda, e dar-lhe um CD é
    exatamente o buraco que esta ação fecha.
    """
    ids = request.form.getlist('item_ids')
    voltar = voltar_seguro(url_for('complementos.index'))

    if not ids:
        flash('Marque ao menos uma peça para mover.', 'warning')
        return redirect(voltar)

    # `parse_int` não é decoração: `localidade_para_escrita` compara o id contra
    # uma lista de ints, e a string do form nunca casaria ("2" in [2] é falso).
    ok, destino_id = localidade_para_escrita(parse_int(request.form.get('localidade_destino')))
    if not ok or destino_id is None:
        flash('Escolha um CD de destino válido.', 'danger')
        return redirect(voltar)

    empresa_id = _empresa()
    permitidas = get_filtro_localidade()
    movidas, barradas = 0, 0
    for bruto in ids:
        item = db.session.get(ItemComplementar, parse_int(bruto))
        # Fora do escopo é tratado como inexistente, mesmo padrão do T3/S2.
        if item is None or (empresa_id is not None and item.empresa_id != empresa_id):
            barradas += 1
            continue
        if (item.localidade_id is not None and permitidas is not None
                and item.localidade_id not in permitidas):
            barradas += 1
            continue
        item.localidade_id = destino_id
        movidas += 1

    if movidas:
        db.session.commit()
        destino = db.session.get(Localidade, destino_id)
        registrar_log('COMPLEMENTO_ITEM_MOVER',
                      f'{movidas} peça(s) movida(s) para {destino.sigla}.')
        flash(f'{movidas} peça(s) movida(s) para {destino.sigla}.', 'success')
    if barradas:
        flash(f'{barradas} não foram movidas: fora dos CDs que você acessa.', 'warning')
    return redirect(voltar)


@complementos_bp.route('/complementos/estoque', methods=['POST'])
@permissao_required(_PERMISSAO)
def estoque_salvar():
    """Grava quantas peças de um tipo existem num CD (D7).

    Convergente: a linha é criada na primeira vez e atualizada depois. Guarda o
    TOTAL cadastrado — o disponível é derivado na hora da retirada (total menos o
    que está em campo), e por isso não há aqui nenhuma noção de entrada ou baixa.
    """
    empresa_id = _empresa()
    if empresa_id is None:
        flash('Escolha uma empresa no topo da tela.', 'warning')
        return redirect(url_for('complementos.index'))

    categoria_id = parse_int(request.form.get('categoria_id'))
    localidade_id = parse_int(request.form.get('localidade_id'))
    qtd = parse_int(request.form.get('qtd_cadastrada'))

    cat = db.session.get(CategoriaComplemento, categoria_id) if categoria_id else None
    if not cat or cat.empresa_id != empresa_id:
        flash('Tipo de peça inválido.', 'danger')
        return redirect(url_for('complementos.index'))
    permitidas = get_filtro_localidade()
    if not localidade_id or (permitidas is not None and localidade_id not in permitidas):
        flash('CD inválido ou fora dos que você acessa.', 'danger')
        return redirect(url_for('complementos.index'))
    if qtd is None or qtd < 0:
        flash('Informe uma quantidade válida (0 ou mais).', 'danger')
        return redirect(url_for('complementos.index'))

    linha = EstoqueComplemento.query.filter_by(categoria_id=cat.id,
                                               localidade_id=localidade_id).first()
    if linha:
        linha.qtd_cadastrada = qtd
    else:
        db.session.add(EstoqueComplemento(empresa_id=empresa_id, categoria_id=cat.id,
                                          localidade_id=localidade_id,
                                          qtd_cadastrada=qtd))
    db.session.commit()
    loc = db.session.get(Localidade, localidade_id)
    registrar_log('COMPLEMENTO_ESTOQUE',
                  f'Estoque de "{cat.nome}" em {loc.sigla if loc else localidade_id}: {qtd}.')
    flash(f'Estoque de "{cat.nome}" atualizado.', 'success')
    return redirect(url_for('complementos.index'))


@complementos_bp.route('/complementos/item/<int:item_id>/parear', methods=['POST'])
@permissao_required(_PERMISSAO)
def item_parear(item_id):
    """Vincula, troca ou desfaz o par entre uma peça e um coletor.

    O pareamento MUDA na operação real: quando o coletor titular vai para
    manutenção, o headset passa a acompanhar outro. Sem esta rota, trocar
    exigiria desativar a peça e recadastrar — perdendo o histórico dela por um
    motivo puramente operacional.

    Desde a B4 quem chama é a página do COLETOR, e por isso a rota volta para
    onde foi chamada (`voltar`, validado em `_voltar_para`). Enviar `coletor_id`
    vazio desfaz o par.

    O vínculo é UM só: vincular aqui desfaz o par que a peça tivesse com outro
    coletor. A tela avisa antes; a rota não impede, porque é exatamente o que se
    quer quando o titular sai de operação.
    """
    empresa_id = _empresa()
    item = db.session.get(ItemComplementar, item_id)
    if not item or (empresa_id is not None and item.empresa_id != empresa_id):
        flash('Peça não encontrada.', 'danger')
        return redirect(voltar_seguro(url_for('complementos.index')))

    voltar = voltar_seguro(url_for('complementos.index'))
    coletor_id = parse_int(request.form.get('coletor_id'))
    if coletor_id is None:
        item.coletor_id = None
        db.session.commit()
        registrar_log('COMPLEMENTO_ITEM_PAREAR',
                      f'Peça "{item.identificador}" desvinculada do coletor.')
        flash(f'Peça "{item.identificador}" não acompanha mais nenhum coletor.',
              'success')
        return redirect(voltar)

    # Mesma trava do cadastro: o coletor precisa existir E estar no escopo de quem
    # parear — apontar para equipamento de outro CD é a família B do S2.
    permitidas = get_filtro_localidade()
    coletor = db.session.get(Coletor, coletor_id)
    if coletor is None or (permitidas is not None and coletor.localidade_id not in permitidas):
        flash('Coletor inválido para vínculo.', 'danger')
        return redirect(voltar)

    # 🔴 Coletor não tem `empresa_id` próprio — o tenant dele é o da LOCALIDADE.
    # Sem esta checagem, um Owner (que passa pelo escopo geográfico com None)
    # poderia prender a peça de um tenant num coletor de outro, e o complemento
    # nunca apareceria na retirada — a categoria de bug que o pacote S1/S2 fechou.
    dono_do_coletor = coletor.localidade.empresa_id if coletor.localidade else None
    if dono_do_coletor is not None and item.empresa_id != dono_do_coletor:
        flash('Este coletor é de outra empresa: a peça não apareceria na retirada dele.',
              'danger')
        return redirect(voltar)

    anterior = item.coletor
    item.coletor_id = coletor.id
    # A peça sem CD herda o do coletor. Ela estava aparecendo na lista de TODOS
    # os CDs justamente por não ter lugar; a partir do vínculo, tem.
    if item.localidade_id is None and coletor.localidade_id is not None:
        item.localidade_id = coletor.localidade_id
    db.session.commit()

    registrar_log('COMPLEMENTO_ITEM_PAREAR',
                  f'Peça "{item.identificador}" vinculada a {coletor.rotulo}'
                  + (f' (saiu de {anterior.rotulo})' if anterior else '') + '.')
    if anterior and anterior.id != coletor.id:
        flash(f'Peça "{item.identificador}" agora acompanha {coletor.rotulo} '
              f'— e deixou de acompanhar {anterior.rotulo}.', 'success')
    else:
        flash(f'Peça "{item.identificador}" agora acompanha {coletor.rotulo}.',
              'success')
    return redirect(voltar)


@complementos_bp.route('/complementos/item/vincular', methods=['POST'])
@permissao_required(_PERMISSAO)
def item_vincular():
    """Vincula a peça ESCOLHIDA num select ao coletor da página (B4).

    Existe separada de `item_parear` por um motivo de forma: lá a peça vem na
    URL, porque a tela já sabia qual era; aqui quem escolhe a peça é o
    formulário, e só se sabe no submit. O miolo é o mesmo — delegar em vez de
    duplicar mantém as travas de escopo num lugar só.
    """
    item_id = parse_int(request.form.get('item_id'))
    if item_id is None:
        flash('Escolha uma peça para vincular.', 'warning')
        return redirect(voltar_seguro(url_for('complementos.index')))
    return item_parear(item_id)


@complementos_bp.route('/complementos/rastreio')
@permissao_required(_PERMISSAO)
def rastreio():
    """Onde está cada PEÇA agora — a cobrança em tempo real (H5, enxugada na B5).

    Separada do catálogo de propósito: `/complementos` é CADASTRO, isto é
    OPERAÇÃO. Misturar as duas faria o gestor procurar cobrança dentro de um CRUD.

    🔴 B5 — a seção de QUANTIDADES saiu. Ela virava uma tabela de saldos por CD,
    que é exatamente o que os cards da tela de Baterias passaram a mostrar, com
    mais clareza e no lugar certo. Duas telas respondendo a mesma pergunta é
    convite para elas divergirem — e a de baterias responde melhor.

    Sobra uma lista só, e a tela volta a ter uma pergunta única: onde está cada
    peça com patrimônio. `rastreio_quantidades` continua no domínio, viva e
    testada: quem a consome agora é o `painel_baterias`.
    """
    from app.complementos import SITUACAO_NO_PRAZO, prazo_horas, rastreio_itens

    empresa_id = _empresa()
    permitidas = get_filtro_localidade()
    filtro_loc = parse_int(request.args.get('localidade'))
    so_atrasados = bool(request.args.get('atrasados'))

    # O filtro do usuário NUNCA amplia o escopo — ele só estreita o que o T3 já
    # permitiu. Confiar no id que veio da query string seria IDOR de leitura.
    ids = permitidas
    if filtro_loc:
        if permitidas is not None and filtro_loc not in permitidas:
            ids = []
        else:
            ids = [filtro_loc]

    itens = rastreio_itens(ids, empresa_id)

    if so_atrasados:
        itens = [i for i in itens if i['estado'] not in ('ESTOQUE', SITUACAO_NO_PRAZO)]

    return render_template('complementos_rastreio.html',
                           itens=itens,
                           localidades=_localidades_visiveis(),
                           filtro_loc=filtro_loc,
                           so_atrasados=so_atrasados,
                           prazo=prazo_horas(),
                           fora_de_prazo=sum(
                               1 for i in itens
                               if i['estado'] not in ('ESTOQUE', SITUACAO_NO_PRAZO)))


# ---------------------------------------------------------------------------
# B2 — BATERIAS: tela própria, porque bateria não é complemento como os outros
# ---------------------------------------------------------------------------

@complementos_bp.route('/complementos/pendencia/<int:linha_id>/devolver',
                       methods=['POST'])
@permissao_required(_PERMISSAO)
def pendencia_devolver(linha_id):
    """Registra que a peça em falta VOLTOU, depois do coletor já ter voltado.

    🔴 O buraco que isto fecha, achado pelo Gabriel na véspera do treinamento: a
    única baixa de complemento do sistema era a da devolução do coletor. Bateria
    que volta no dia seguinte é rotina, e não havia onde registrar — a falta
    ficava na tela para sempre e o saldo do CD ficava errado para menos.

    Escopo pelo CD do coletor, como todo o resto da operação: pendência de um CD
    que a sessão não alcança é tratada como inexistente, nunca como 403.
    """
    from app.complementos import (ComplementoRecusado, descrever_pendencia,
                                  registrar_devolucao_atrasada)
    from app.models import Coletor, Movimentacao, MovimentacaoComplemento

    voltar = voltar_seguro(url_for('dashboard.index'))

    linha = db.session.get(MovimentacaoComplemento, linha_id)
    if linha is None:
        flash('Pendência não encontrada.', 'danger')
        return redirect(voltar)

    movimentacao = db.session.get(Movimentacao, linha.movimentacao_id)
    coletor = db.session.get(Coletor, movimentacao.coletor_id) if movimentacao else None
    permitidas = get_filtro_localidade()
    if coletor is None or (permitidas is not None
                           and coletor.localidade_id not in permitidas):
        flash('Pendência não encontrada.', 'danger')
        return redirect(voltar)

    descricao = descrever_pendencia(linha)
    try:
        devolvidas = registrar_devolucao_atrasada(linha, request.form.get('quantidade') or None)
    except (ComplementoRecusado, ValueError) as erro:
        flash(str(erro) or 'Quantidade inválida.', 'danger')
        return redirect(voltar)

    db.session.commit()
    # Quem recebeu e quando fica na trilha: a devolução em atraso é o fim de uma
    # cobrança, e sem registro ela vira "sumiu da tela e ninguém sabe por quê".
    registrar_log('COMPLEMENTO_DEVOLUCAO_ATRASADA',
                  f'{descricao} devolvido(s) em atraso — coletor {coletor.rotulo}.')
    flash(f'Recebido: {devolvidas}x {linha.categoria.nome if linha.categoria else "peça"}. '
          f'A pendência do coletor {coletor.rotulo} foi baixada.', 'success')
    return redirect(voltar)


@complementos_bp.route('/baterias')
@permissao_required(_PERMISSAO)
def baterias():
    """Um card por CD, com o cenário de baterias daquele CD.

    Bateria é FUNGÍVEL: some o tempo todo e ninguém quer cadastrá-la uma a uma.
    Por isso a tela não lista peças — lista CDs, e cada um responde: quantas
    tenho, quantas estão fora, quantas posso emprestar agora, quantas
    estouraram o prazo.
    """
    from app.complementos import categorias_de_bateria, painel_baterias, prazo_horas

    empresa_id = _empresa()
    permitidas = get_filtro_localidade()
    painel = painel_baterias(permitidas, empresa_id)
    return render_template(
        'baterias.html',
        painel=painel,
        categorias=categorias_de_bateria(empresa_id) if empresa_id else [],
        # Inclui os INATIVOS: quem desativou por engano precisa achar para
        # reativar, e um tipo que some da tela vira um tipo recriado duplicado.
        todos_tipos=(CategoriaComplemento.query
                     .filter_by(empresa_id=empresa_id)
                     .filter(CategoriaComplemento.e_bateria == True)   # noqa: E712
                     .order_by(CategoriaComplemento.ativa.desc(),
                               CategoriaComplemento.nome).all()
                     if empresa_id else []),
        camaras=COMPLEMENTO_CAMARAS,
        # 🔴 As listas brancas de aparência PRECISAM chegar aqui: sem elas o
        # `{% for %}` do seletor não roda e a tela mostra selects vazios, sem
        # erro nenhum. Foi o que aconteceu — a inserção caiu na rota de
        # complementos, que tem a MESMA linha de âncora.
        icones=BATERIA_ICONES,
        cores=BATERIA_CORES,
        prazo=prazo_horas(empresa_id),
        # Os totais do topo respondem a pergunta antes de olhar card por card.
        total_atrasadas=sum(c['atrasadas'] for c in painel),
        total_em_campo=sum(c['em_campo'] for c in painel))


@complementos_bp.route('/baterias/<int:loc_id>')
@permissao_required(_PERMISSAO)
def baterias_detalhe(loc_id):
    """O cenário de UM CD: quem está com quantas, desde quando, em qual coletor.

    O escopo é conferido aqui e não confiado à URL — trocar o id na barra de
    endereços não pode abrir o CD de outro tenant (família B do S2).
    """
    from app.complementos import painel_baterias, prazo_horas

    permitidas = get_filtro_localidade()
    if permitidas is not None and loc_id not in permitidas:
        flash('Este CD não está entre os que você acessa.', 'danger')
        return redirect(url_for('complementos.baterias'))

    empresa_id = _empresa()
    cartoes = painel_baterias([loc_id], empresa_id)
    if not cartoes:
        flash('CD não encontrado.', 'danger')
        return redirect(url_for('complementos.baterias'))

    # 🔴 As categorias vêm SEPARADAS do painel. `painel_baterias` só traz linha
    # onde já existe estoque ou movimento — iterar por elas no formulário faria o
    # CD zerado não ter cartão nenhum para preencher, justamente o CD que mais
    # precisa cadastrar.
    from app.complementos import categorias_de_bateria
    linhas = {l['categoria'].id: l for l in cartoes[0]['linhas']}
    ajuste = [(cat, linhas.get(cat.id, {}).get('cadastrada'))
              for cat in categorias_de_bateria(empresa_id)] if empresa_id else []

    return render_template('baterias_detalhe.html', cartao=cartoes[0],
                           ajuste=ajuste, prazo=prazo_horas(empresa_id))


@complementos_bp.route('/baterias/<int:loc_id>/salvar', methods=['POST'])
@permissao_required(_PERMISSAO)
def baterias_salvar(loc_id):
    """Grava as quantidades dos dois cartões de uma vez.

    🔴 O formulário manda TODAS as categorias juntas, e não uma por vez: quem
    ajusta bateria ajusta as duas na mesma conferência de prateleira. Salvar uma
    e esquecer a outra é o erro que a tela antiga convidava a cometer.

    Campo em branco é IGNORADO, não vira zero. Zerar o estoque de um CD por
    engano — porque o campo veio vazio — apagaria a base do cálculo de saldo.
    """
    from app.complementos import categorias_de_bateria

    empresa_id = _empresa()
    if empresa_id is None:
        flash('Escolha uma empresa no topo da tela para ajustar o estoque.', 'warning')
        return redirect(url_for('complementos.baterias'))

    ok, loc_ok = localidade_para_escrita(loc_id)
    if not ok or loc_ok is None:
        flash('Este CD não está entre os que você acessa.', 'danger')
        return redirect(url_for('complementos.baterias'))

    salvos = 0
    for cat in categorias_de_bateria(empresa_id):
        bruto = request.form.get(f'qtd_{cat.id}')
        if bruto is None or bruto.strip() == '':
            continue
        qtd = parse_int(bruto)
        if qtd is None or qtd < 0:
            flash(f'Quantidade inválida para {cat.nome}.', 'danger')
            return redirect(url_for('complementos.baterias_detalhe', loc_id=loc_id))

        linha = EstoqueComplemento.query.filter_by(
            categoria_id=cat.id, localidade_id=loc_ok).first()
        if linha is None:
            linha = EstoqueComplemento(empresa_id=empresa_id, categoria_id=cat.id,
                                       localidade_id=loc_ok, qtd_cadastrada=qtd)
            db.session.add(linha)
        else:
            linha.qtd_cadastrada = qtd
        salvos += 1

    if salvos:
        db.session.commit()
        registrar_log('BATERIA_ESTOQUE',
                      f'Estoque de bateria ajustado em {salvos} tipo(s), CD {loc_ok}.')
        flash('Quantidades salvas.', 'success')
    return redirect(url_for('complementos.baterias_detalhe', loc_id=loc_id))


@complementos_bp.route('/baterias/tipo/criar', methods=['POST'])
@permissao_required(_PERMISSAO)
def bateria_tipo_criar():
    """Cria um tipo de bateria DENTRO da tela de baterias.

    🔴 O Gabriel: "não tenho que cadastrar baterias em outra tela, tem que ser
    aqui, tudo sobre baterias". Está certo — mandar para Complementos para criar
    um tipo reintroduz exatamente a viagem que o pacote B existe para acabar. E o
    aviso que eu tinha escrito apontava para lá, ou seja, para o lugar de onde a
    bateria acabara de sair.

    O formulário tem DOIS campos, e não os quatro do catálogo de complementos: o
    "como você controla" não existe aqui, porque bateria é SEMPRE contada. A
    pergunta que sobra é só onde ela aguenta trabalhar.

    Não são dois tipos fixos: são quantos a operação precisar. Bateria de headset
    já está no horizonte, e a tela desenha um cartão por tipo cadastrado.
    """
    empresa_id = _empresa()
    if empresa_id is None:
        flash('Escolha uma empresa no topo da tela para cadastrar um tipo.', 'warning')
        return redirect(url_for('complementos.baterias'))

    voltar = voltar_seguro(url_for('complementos.baterias'))
    nome = request.form.get('nome', '').strip().upper()[:40]
    camara = request.form.get('camara_atendida') or None
    # Default COLETOR e não NULL: quem não responde está cadastrando bateria de
    # coletor, que é o caso de longe mais comum. NULL fica reservado para as
    # linhas que já existiam antes desta coluna.
    equipamento = request.form.get('equipamento') or EQUIPAMENTO_COLETOR

    if not nome:
        flash('Informe o nome do tipo de bateria.', 'danger')
        return redirect(voltar)
    if camara is not None and camara not in COMPLEMENTO_CAMARAS:
        flash('Opção inválida em "onde essa bateria aguenta trabalhar?".', 'danger')
        return redirect(voltar)
    if equipamento not in EQUIPAMENTOS:
        flash('Opção inválida em "de qual equipamento é esta bateria?".', 'danger')
        return redirect(voltar)
    if CategoriaComplemento.query.filter_by(empresa_id=empresa_id, nome=nome).first():
        flash(f'Já existe um tipo chamado "{nome}".', 'warning')
        return redirect(voltar)

    db.session.add(CategoriaComplemento(
        empresa_id=empresa_id, nome=nome, camara_atendida=camara,
        controle=COMPLEMENTO_QUANTIDADE,   # bateria é sempre contada
        e_bateria=True,
        equipamento=equipamento,
        **_aparencia_do_form()))
    db.session.commit()
    registrar_log('BATERIA_TIPO_CRIAR', f'Tipo de bateria "{nome}" criado.')
    flash(f'Tipo "{nome}" criado. Agora informe quantas cada CD tem.', 'success')
    return redirect(voltar)


@complementos_bp.route('/baterias/tipo/<int:cat_id>/toggle', methods=['POST'])
@permissao_required(_PERMISSAO)
def bateria_tipo_toggle(cat_id):
    """Liga e desliga um tipo. Nunca apaga — o histórico depende dele.

    Um tipo desativado some das retiradas novas, mas as movimentações que já
    aconteceram continuam apontando para ele. Apagar deixaria a cobrança órfã.
    """
    empresa_id = _empresa()
    cat = db.session.get(CategoriaComplemento, cat_id)
    if cat is None or not cat.e_bateria or (empresa_id is not None
                                            and cat.empresa_id != empresa_id):
        flash('Tipo de bateria não encontrado.', 'danger')
        return redirect(url_for('complementos.baterias'))

    cat.ativa = not cat.ativa
    db.session.commit()
    estado = 'reativado' if cat.ativa else 'desativado'
    registrar_log('BATERIA_TIPO_TOGGLE', f'Tipo "{cat.nome}" {estado}.')
    flash(f'Tipo "{cat.nome}" {estado}. O que já saiu continua sendo cobrado.',
          'success')
    return redirect(url_for('complementos.baterias'))


def _aparencia_do_form():
    """Ícone e cor validados contra a LISTA BRANCA.

    🔴 Estes valores vão para dentro de `class` e de `style` no template. Aceitar
    o que o formulário mandar seria deixar o campo escrever CSS e markup na
    página. Valor fora da lista cai no padrão em silêncio — é enfeite, não pode
    virar erro na cara de quem só queria cadastrar uma bateria.
    """
    icone = request.form.get('icone')
    cor = request.form.get('cor')
    if icone not in [c for c, _r in BATERIA_ICONES]:
        icone = ICONE_BATERIA_PADRAO
    if cor not in [c for c, _r, _f, _b, _t in BATERIA_CORES]:
        cor = COR_BATERIA_PADRAO
    return {'icone': icone, 'cor': cor}


@complementos_bp.route('/baterias/tipo/<int:cat_id>/aparencia', methods=['POST'])
@permissao_required(_PERMISSAO)
def bateria_tipo_aparencia(cat_id):
    """Muda o ícone e a cor de um tipo que já existe.

    Os tipos criados antes desta fatia nasceram sem aparência escolhida, e sem
    esta rota ficariam presos ao padrão para sempre — obrigando a recriar o tipo,
    o que quebraria o histórico de movimentação preso a ele.
    """
    empresa_id = _empresa()
    cat = db.session.get(CategoriaComplemento, cat_id)
    if cat is None or not cat.e_bateria or (empresa_id is not None
                                            and cat.empresa_id != empresa_id):
        flash('Tipo de bateria não encontrado.', 'danger')
        return redirect(url_for('complementos.baterias'))

    for campo, valor in _aparencia_do_form().items():
        setattr(cat, campo, valor)

    # 🔴 A câmara também se conserta AQUI. Os tipos criados antes nasceram sem
    # ela, e sem câmara o portão do D6 devolve "atende" para qualquer coletor —
    # ou seja, deixa de proteger o congelado sem avisar ninguém. Só grava se o
    # formulário mandou o campo: quem não mandou não está querendo apagar.
    if 'camara_atendida' in request.form:
        camara = request.form.get('camara_atendida') or None
        if camara in (None, 'SECO', 'CLIMATIZADO'):
            cat.camara_atendida = camara

    # 🔴 E o equipamento, pelo mesmo motivo: eu acrescentei a pergunta só no
    # CADASTRO, então quem respondesse errado precisava desativar o tipo e criar
    # outro — perdendo o histórico de movimentação preso a ele. Achado pelo
    # Gabriel na própria tela, tentando consertar a bateria de headset.
    if 'equipamento' in request.form:
        equipamento = request.form.get('equipamento') or None
        if equipamento in (None,) + tuple(EQUIPAMENTOS):
            cat.equipamento = equipamento

    db.session.commit()
    # Não é mais só aparência: esta rota conserta câmara e equipamento, que são
    # regra de operação. A frase acompanha o que ela faz hoje.
    flash(f'Tipo "{cat.nome}" atualizado.', 'success')
    return redirect(url_for('complementos.baterias') + '#tipos')
