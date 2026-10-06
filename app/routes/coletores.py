"""
Blueprint de gestão do inventário de coletores e localidades.

Expõe duas áreas distintas, ambas restritas a perfis administrativos:
    - /coletores  : CRUD de coletores, com suporte a filtros e edição de status
    - /localidades: CRUD de localidades (áreas do CD onde os coletores ficam alocados)

Permissões exigidas:
    - 'admin.inventario_ativos' para acessar o gerenciamento de coletores
    - 'admin.localidades' para acessar o gerenciamento de localidades
"""

from datetime import datetime, date

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app import db
from app.models import (Coletor, Localidade, Movimentacao, Usuario,
                        STATUS_INDISPONIVEIS, STATUS_FORA_DE_OPERACAO,
                        STATUS_COLETOR)
from app.helpers import (login_required, permissao_required, registrar_log,
                         get_filtro_localidade, empresa_para_escrita,
                         localidade_para_escrita, empresa_visivel,
                         parse_int, voltar_seguro)

coletores_bp = Blueprint('coletores', __name__)


# ---------------------------------------------------------------------------
# INVENTÁRIO DE COLETORES
# ---------------------------------------------------------------------------

@coletores_bp.route('/coletores', methods=['GET', 'POST'])
@permissao_required('admin.inventario_ativos')
def gerenciar_coletores():
    """
    Lista, cria e edita coletores do inventário.

    GET: Retorna a lista de coletores com suporte a três filtros combinados via
         query string: localidade (sigla), status e tipo de alcance.

    POST: Opera no modo upsert — se 'id' vier no form, edita o coletor
          existente; caso contrário, cria um novo.

    Regras de negócio relevantes:
        - Ao forçar saída do status 'Em Uso' via admin, a movimentação aberta é
          fechada automaticamente com marcação 'Admin Inventário', garantindo
          que o histórico não fique com registros em aberto.
        - Ao marcar como 'Suspenso', os campos de defeito são preenchidos
          automaticamente com valores padrão de violação física.
        - Ao marcar como 'Disponível', os campos de defeito são limpos —
          não faz sentido ter um coletor disponível com causa de defeito registrada.
    """
    if request.method == 'POST':
        c_id        = request.form.get('id')
        serial      = request.form.get('serial_number')
        identificacao = request.form.get('numero_identificacao')
        modelo      = request.form.get('modelo', '').strip().upper() or 'MC9190G'
        status_novo = request.form.get('status')
        cat         = request.form.get('categoria_defeito')
        det         = request.form.get('detalhe_defeito')
        desc        = request.form.get('descricao_problema')
        num_patr    = request.form.get('numero_patrimonio', '').strip() or None
        loc_id_raw  = request.form.get('localidade_id') or None
        alcance     = request.form.get('tipo_alcance') or None
        camara      = request.form.get('camara') or None

        # Garantia — propriedade operacional do equipamento
        tem_garantia          = 'tem_garantia' in request.form
        validade_garantia_str = request.form.get('validade_garantia', '').strip()
        validade_garantia     = date.fromisoformat(validade_garantia_str) if validade_garantia_str else None

        if status_novo not in STATUS_COLETOR:
            flash('Status inválido.', 'danger')
            return redirect(url_for('coletores.gerenciar_coletores'))

        loc_id = int(loc_id_raw) if loc_id_raw else None
        # S1/família E — a localidade do coletor precisa pertencer ao escopo de
        # escrita da sessão; uma localidade de outro tenant é rejeitada.
        loc_ok, loc_id = localidade_para_escrita(loc_id)
        if not loc_ok:
            flash('Este CD não está entre os que você acessa.', 'danger')
            return redirect(url_for('coletores.gerenciar_coletores'))

        # 🔴 T3 — a localidade é a ÂNCORA DA EMPRESA do coletor, e sem ela o
        # equipamento não pertence a tenant nenhum: `get_filtro_localidade`
        # devolve ids, e `localidade_id IS NULL` não está em lista nenhuma. O
        # coletor existe, consome patrimônio e é invisível para todo usuário
        # comum — inclusive nível GLOBAL da própria empresa. Só o Owner enxerga,
        # que é justamente quem cadastra no treinamento e sai achando que
        # funcionou.
        if not loc_id:
            flash('Escolha o CD onde o coletor fica. Sem isso ele não aparece '
                  'para a equipe.', 'danger')
            return redirect(url_for('coletores.gerenciar_coletores'))

        # Normaliza campos de defeito para suspensão via interface admin —
        # mesma lógica usada na devolução com identificação violada, mantendo
        # consistência no banco independente de qual caminho gerou o Suspenso.
        if status_novo == 'Suspenso':
            cat, det, desc = 'FISICO', 'VIOLACAO', 'Suspensão automática por violação.'

        if c_id:
            # 🔴 O coletor editado precisa estar no escopo de quem edita (família
            # B). Validar só o CD de destino deixava puxar, pelo id, um coletor
            # de outro CD para o seu. Fora do escopo é tratado como inexistente.
            coletor = _coletor_para_acao(parse_int(c_id))
            if not coletor:
                flash('Coletor não encontrado.', 'danger')
                return redirect(url_for('coletores.gerenciar_coletores'))

            if coletor.status != status_novo:
                registrar_log('UPDATE_STATUS', f'Coletor {serial}: {coletor.status} → {status_novo}')

            # Fecha movimentação aberta se sair do "Em Uso" via admin
            if coletor.status == 'Em Uso' and status_novo != 'Em Uso':
                mov = Movimentacao.query.filter_by(coletor_id=coletor.id, data_retorno=None).first()
                if mov:
                    mov.data_retorno         = datetime.now()
                    mov.status_identificacao_retorno = 'Admin Inventário'
                    coletor.re_colaborador   = None
                    coletor.hora_saida       = None

            # Limpa campos de defeito ao retornar para Disponível —
            # sem isso, o coletor ficaria com causa de defeito "órfã" no banco.
            if status_novo == 'Disponível':
                cat, det, desc = None, None, None

            coletor.serial_number     = serial
            coletor.numero_identificacao = identificacao
            coletor.modelo            = modelo
            coletor.status            = status_novo
            coletor.categoria_defeito = cat
            coletor.detalhe_defeito   = det
            coletor.descricao_problema= desc
            coletor.numero_patrimonio = num_patr
            coletor.localidade_id     = loc_id
            coletor.tipo_alcance      = alcance
            coletor.camara            = camara
            coletor.tem_garantia      = tem_garantia
            coletor.validade_garantia = validade_garantia if tem_garantia else None
            flash(f'Coletor {serial} atualizado.', 'success')
        else:
            novo = Coletor(
                serial_number=serial, numero_identificacao=identificacao, modelo=modelo, status=status_novo,
                categoria_defeito=cat, detalhe_defeito=det, descricao_problema=desc,
                numero_patrimonio=num_patr, localidade_id=loc_id, tipo_alcance=alcance,
                camara=camara, tem_garantia=tem_garantia,
                validade_garantia=validade_garantia if tem_garantia else None,
            )
            db.session.add(novo)
            registrar_log('CREATE_ASSET', f'Novo coletor cadastrado: {serial}')
            flash('Novo coletor cadastrado.', 'success')

        db.session.commit()
        # Redireciona preservando os filtros ativos para que o admin não perca
        # o contexto de navegação após salvar.
        return redirect(url_for(
            'coletores.gerenciar_coletores',
            localidade=request.args.get('localidade', ''),
            status=request.args.get('status', ''),
            alcance=request.args.get('alcance', ''),
        ))

    # ── Filtros via query string ──
    filtro_loc     = request.args.get('localidade', '')
    filtro_status  = request.args.get('status', '')
    filtro_alcance = request.args.get('alcance', '')
    filtro_pendencia = request.args.get('pendencia', '')
    # U1 — colunas extras sob demanda. Na operação típica, alcance, diagnóstico e
    # observação ficam vazios na maioria das linhas e viram parede de traços; quem
    # precisa delas liga, e a escolha sobrevive na URL (dá para favoritar).
    colunas_extras = request.args.get('colunas') == 'tudo'

    # P7 — aplica hierarquia geográfica como base antes dos filtros manuais
    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is None:
        query = Coletor.query
    elif ids_permitidos:
        query = Coletor.query.filter(Coletor.localidade_id.in_(ids_permitidos))
    else:
        query = Coletor.query.filter(False)

    if filtro_loc:
        query = query.join(Localidade, Coletor.localidade_id == Localidade.id).filter(Localidade.sigla == filtro_loc)
    if filtro_status == STATUS_INDISPONIVEIS:
        # U2 — o dashboard conta "indisponíveis" agrupando cinco status. Sem este
        # grupo aqui, o KPI não teria para onde apontar: `status=` sozinho só
        # aceita um valor, e o número do painel não bateria com lista nenhuma.
        query = query.filter(Coletor.status.in_(STATUS_FORA_DE_OPERACAO))
    elif filtro_status:
        query = query.filter(Coletor.status == filtro_status)
    if filtro_alcance:
        query = query.filter(Coletor.tipo_alcance == filtro_alcance)

    coletores    = query.order_by(Coletor.id).all()

    # S3/família C — dropdowns escopados (o ponto sutil: um dropdown vazado
    # alimenta a atribuição cross-tenant via UI legítima). Reusa ids_permitidos
    # já calculado acima para o filtro de localidade.
    if ids_permitidos is None:
        localidades = Localidade.query.order_by(Localidade.sigla).all()
    elif ids_permitidos:
        localidades = Localidade.query.filter(Localidade.id.in_(ids_permitidos)).order_by(Localidade.sigla).all()
    else:
        localidades = []

    # H4 — o que cada coletor está devendo. Indexado por id para o template não
    # consultar por linha (N+1 numa listagem que pode ter centenas de coletores).
    from app.complementos import pendencias_por_coletor
    pendencias = pendencias_por_coletor(ids_permitidos)

    # O recorte é em memória de propósito: as pendências abertas são poucas
    # (dezenas), e refazer a query de coletores com um IN dessa lista custaria
    # mais do que filtrar o que já está carregado.
    if filtro_pendencia:
        coletores = [c for c in coletores if c.id in pendencias]

    # B6 — quais coletores nunca foram usados, para a tela oferecer EXCLUIR só
    # onde ele funciona. Em lote: uma consulta por relação, nunca uma por linha.
    from app.desativacao import ids_com_historico
    com_historico = ids_com_historico(Coletor, [c.id for c in coletores])

    # 🔴 Tela vazia tem TRÊS causas e só uma é "ainda não cadastraram": o
    # usuário sem localidade atribuída vê zero coletor com a base cheia. Dizer
    # "cadastre o primeiro" para ele produz cadastro duplicado — e é o caso que
    # mais aparece logo depois de criar os usuários.
    sem_alcance = ids_permitidos is not None and not ids_permitidos

    return render_template('coletores.html',
                           coletores=coletores,
                           sem_alcance=sem_alcance,
                           com_historico=com_historico,
                           pendencias_complemento=pendencias,
                           localidades=localidades,
                           filtro_loc=filtro_loc,
                           filtro_status=filtro_status,
                           filtro_alcance=filtro_alcance,
                           filtro_pendencia=filtro_pendencia,
                           colunas_extras=colunas_extras)


# ---------------------------------------------------------------------------
# LOCALIDADES
# ---------------------------------------------------------------------------

@coletores_bp.route('/localidades', methods=['GET', 'POST'])
@permissao_required('admin.localidades')
def gerenciar_localidades():
    """
    Gerencia as localidades do CD — áreas físicas onde os coletores são alocados.

    Todas as operações (criar, editar, excluir) são submetidas via POST com um
    campo 'acao' discriminando a intenção. Isso permite usar um único endpoint
    para as três ações sem proliferar rotas para operações simples de CRUD.

    Regras de negócio:
        - Sigla é normalizada para maiúsculas e deve ser única — é usada como
          chave de filtro nas outras telas, então duplicatas causariam ambiguidade.
        - Exclusão é bloqueada se houver coletores vinculados à localidade.
          Deletar com coletores pendentes deixaria FK órfãs ou forçaria cascade
          não intencional.
    """
    if request.method == 'POST':
        acao = request.form.get('acao')

        if acao == 'criar':
            sigla = request.form.get('sigla', '').upper().strip()
            nome  = request.form.get('nome', '').strip()
            desc  = request.form.get('descricao', '').strip() or None
            # S1/família A — a localidade é a âncora do tenant: nasce carimbada
            # com a empresa da sessão. Sem tenant, a criação é bloqueada.
            empresa_id = empresa_para_escrita()
            if not sigla or not nome:
                flash('Sigla e nome são obrigatórios.', 'danger')
            elif empresa_id is None:
                flash('Escolha uma empresa no topo da tela para criar um CD.', 'warning')
            elif Localidade.query.filter(
                Localidade.empresa_id == empresa_id,      # S1/família D — unicidade por empresa
                Localidade.sigla == sigla,
            ).first():
                flash(f'Já existe um CD com a sigla "{sigla}".', 'danger')
            else:
                db.session.add(Localidade(sigla=sigla, nome=nome, descricao=desc,
                                          empresa_id=empresa_id))
                registrar_log('CREATE_LOCALIDADE', f'CD {sigla} criado.')
                db.session.commit()
                flash(f'CD {sigla} — {nome} criado com sucesso.', 'success')

        elif acao == 'editar':
            # S2/família B — localidade de outro tenant é invisível (nunca 403).
            loc = db.session.get(Localidade, request.form.get('id'))
            if loc and empresa_visivel(loc.empresa_id):
                nova_sigla   = request.form.get('sigla', '').upper().strip()
                novo_nome    = request.form.get('nome', '').strip()

                if not nova_sigla or not novo_nome:
                    flash('Sigla e nome são obrigatórios.', 'danger')
                    return redirect(url_for('coletores.gerenciar_localidades'))

                # S1/família D — o conflito de sigla é dentro da MESMA empresa da
                # localidade (constraint uq_localidade_empresa_sigla).
                conflito = Localidade.query.filter(
                    Localidade.empresa_id == loc.empresa_id,
                    Localidade.sigla == nova_sigla,
                    Localidade.id != loc.id,
                ).first()
                if conflito:
                    flash(f'Já existe um CD com a sigla "{nova_sigla}".', 'danger')
                    return redirect(url_for('coletores.gerenciar_localidades'))

                loc.sigla     = nova_sigla
                loc.nome      = novo_nome
                loc.descricao = request.form.get('descricao', '').strip() or None
                registrar_log('UPDATE_LOCALIDADE', f'CD {loc.sigla} atualizado.')
                db.session.commit()
                flash(f'CD {loc.sigla} atualizado.', 'success')

        elif acao == 'excluir':
            # S2/família B — localidade de outro tenant é invisível (nunca 403).
            loc = db.session.get(Localidade, request.form.get('id'))
            if loc and empresa_visivel(loc.empresa_id):
                if loc.coletores:
                    # Protege integridade referencial: só permite excluir
                    # localidades sem coletores vinculados.
                    flash(f'Não é possível excluir: {len(loc.coletores)} coletor(es) vinculado(s) a {loc.sigla}.', 'danger')
                else:
                    sigla = loc.sigla
                    db.session.delete(loc)
                    registrar_log('DELETE_LOCALIDADE', f'CD {sigla} excluído.')
                    db.session.commit()
                    flash(f'CD {sigla} excluído.', 'success')

        return redirect(url_for('coletores.gerenciar_localidades'))

    loc_q = Localidade.query

    # S3/família C — leitura escopada por empresa (fail-closed); Owner vê todas.
    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is not None:
        loc_q = loc_q.filter(Localidade.id.in_(ids_permitidos)) if ids_permitidos else loc_q.filter(False)
    localidades = loc_q.order_by(Localidade.sigla).all()

    # Contagem de usuários por localidade feita no banco (GROUP BY) para evitar
    # N+1 — sem isso, cada linha da tabela dispararia uma query de contagem.
    # Escopada pelas mesmas localidades visíveis — usuarios_por_loc era global.
    from sqlalchemy import func as _func
    contagem_q = db.session.query(Usuario.localidade_id, _func.count(Usuario.id)).filter(
        Usuario.localidade_id.isnot(None)
    )
    if ids_permitidos is not None:
        contagem_q = contagem_q.filter(Usuario.localidade_id.in_(ids_permitidos)) if ids_permitidos else contagem_q.filter(False)
    usuarios_por_loc = dict(contagem_q.group_by(Usuario.localidade_id).all())

    return render_template('localidades.html', localidades=localidades,
                           usuarios_por_loc=usuarios_por_loc)


# ---------------------------------------------------------------------------
# AÇÕES EM LOTE
# ---------------------------------------------------------------------------

@coletores_bp.route('/coletores/mover', methods=['POST'])
@login_required
@permissao_required('admin.inventario_ativos')
def mover_em_lote():
    """Move os coletores marcados para outro CD (U1).

    Primeira ação em lote do inventário. Escolhida por ser a mais segura das
    candidatas: mexe num campo só, não toca em status, e o guarda de
    escopo já existe e é testado (`localidade_para_escrita`, família E do S1).

    🔴 O destino E a origem são validados. Validar só o destino deixaria alguém
    mover um coletor de OUTRO tenant para dentro do seu — bastaria forjar o id na
    lista. É a mesma família B que o S2 fechou, e num POST em lote ela é mais
    tentadora porque os ids chegam em massa.
    """
    ids = request.form.getlist('coletor_ids')
    destino_raw = request.form.get('localidade_destino')
    voltar = voltar_seguro(url_for('coletores.gerenciar_coletores'))

    if not ids:
        flash('Marque ao menos um coletor para mover.', 'warning')
        return redirect(voltar)

    # 🔴 `parse_int` NÃO é decoração: o helper compara o id contra uma lista de
    # ints, e uma string do form nunca casa ("2" in [2] é falso). Sem isto a ação
    # em lote recusaria TODO destino para quem não é Owner — e passaria batido em
    # teste feito como Owner, que cai no outro ramo do helper.
    ok, destino_id = localidade_para_escrita(parse_int(destino_raw))
    if not ok or destino_id is None:
        flash('Escolha um CD de destino válido.', 'danger')
        return redirect(voltar)

    permitidas = get_filtro_localidade()
    movidos, barrados = 0, 0
    for cid in ids:
        coletor = db.session.get(Coletor, parse_int(cid))
        # Fora do escopo é tratado como inexistente, mesmo padrão do T3.
        if coletor is None or (permitidas is not None
                               and coletor.localidade_id not in permitidas):
            barrados += 1
            continue
        coletor.localidade_id = destino_id
        movidos += 1

    if movidos:
        db.session.commit()
        destino = db.session.get(Localidade, destino_id)
        registrar_log('COLETOR_MOVER_LOTE',
                      f'{movidos} coletor(es) movido(s) para {destino.sigla}.')
        flash(f'{movidos} coletor(es) movido(s) para {destino.sigla}.', 'success')

    if barrados:
        flash(f'{barrados} não foram movidos: fora dos CDs que você acessa.', 'warning')
    return redirect(voltar)


# ---------------------------------------------------------------------------
# B6 — DESATIVAR, REATIVAR e EXCLUIR
# ---------------------------------------------------------------------------
# A regra mora em `app/desativacao.py`: item com histórico não se exclui, só se
# desativa, e a desativação exige motivo. Aqui ficam só o escopo (T3/S2) e a
# trava que é do coletor.

def _coletor_para_acao(coletor_id):
    """Carrega o coletor se ele estiver no escopo da sessão (S2/família B).

    Fora do escopo volta None, e o chamador trata como inexistente — nunca 403,
    mesmo padrão do resto do T3.
    """
    coletor = db.session.get(Coletor, coletor_id)
    if not coletor:
        return None
    permitidas = get_filtro_localidade()
    if permitidas is not None and coletor.localidade_id not in permitidas:
        return None
    return coletor


def _impedimento_operacional(coletor):
    """A situação em que tirar o coletor de operação seria mentira.

    🔴 EM USO: alguém está com ele. Desativar deixaria a movimentação aberta sem
    tela para fechá-la — o coletor sumiria do balcão com a dívida em aberto, que
    é exatamente o oposto do que a H4 construiu.
    """
    if coletor.status == 'Em Uso':
        return ('Este coletor está com um colaborador. Registre a devolução '
                'antes de desativá-lo.')
    return None


@coletores_bp.route('/coletores/<int:coletor_id>/desativar', methods=['POST'])
@permissao_required('admin.inventario_ativos')
def desativar_coletor(coletor_id):
    """Tira o coletor de operação. Reversível, com motivo obrigatório."""
    from app.desativacao import ExclusaoRecusada, desativar, usuario_da_sessao

    voltar = voltar_seguro(url_for('coletores.gerenciar_coletores'))
    coletor = _coletor_para_acao(coletor_id)
    if not coletor:
        flash('Coletor não encontrado.', 'danger')
        return redirect(voltar)

    impedimento = _impedimento_operacional(coletor)
    if impedimento:
        flash(impedimento, 'warning')
        return redirect(voltar)

    try:
        desativar(coletor, request.form.get('motivo'), usuario_da_sessao())
    except ExclusaoRecusada as e:
        flash(str(e), 'danger')
        return redirect(voltar)

    registrar_log('COLETOR_DESATIVAR',
                  f'Coletor {coletor.rotulo} desativado. '
                  f'Motivo: {coletor.desativado_motivo}')
    flash(f'Coletor {coletor.rotulo} desativado. '
          f'O histórico dele continua inteiro.', 'success')
    return redirect(voltar)


@coletores_bp.route('/coletores/<int:coletor_id>/voltar-operacao', methods=['POST'])
@permissao_required('admin.inventario_ativos')
def voltar_operacao_coletor(coletor_id):
    """Desfaz a desativação — o coletor volta para as telas de operação.

    "Voltar para operação" e não "reativar": reativar já é o nome da liberação
    de um coletor suspenso (/suspensos/reativar), e a mesma palavra para duas
    ações diferentes confunde quem opera as duas telas.
    """
    from app.desativacao import reativar

    voltar = voltar_seguro(url_for('coletores.gerenciar_coletores'))
    coletor = _coletor_para_acao(coletor_id)
    if not coletor:
        flash('Coletor não encontrado.', 'danger')
        return redirect(voltar)

    reativar(coletor)
    registrar_log('COLETOR_VOLTA_OPERACAO',
                  f'Coletor {coletor.rotulo} voltou para a operação.')
    flash(f'Coletor {coletor.rotulo} voltou para a operação.', 'success')
    return redirect(voltar)


@coletores_bp.route('/coletores/<int:coletor_id>/excluir', methods=['POST'])
@permissao_required('admin.inventario_ativos')
def excluir_coletor(coletor_id):
    """Apaga de vez — só o que nunca foi usado, e só com a senha confirmada."""
    from app.desativacao import ExclusaoRecusada, excluir, usuario_da_sessao

    voltar = voltar_seguro(url_for('coletores.gerenciar_coletores'))
    coletor = _coletor_para_acao(coletor_id)
    if not coletor:
        flash('Coletor não encontrado.', 'danger')
        return redirect(voltar)

    # guardado ANTES do delete: depois dele o objeto já não responde
    etiqueta = coletor.rotulo
    try:
        excluir(coletor, usuario_da_sessao(), request.form.get('senha'),
                empresa_para_escrita())
    except ExclusaoRecusada as e:
        flash(str(e), 'danger')
        return redirect(voltar)

    flash(f'Coletor {etiqueta} excluído definitivamente. '
          f'O registro da exclusão ficou na auditoria.', 'success')
    return redirect(voltar)
