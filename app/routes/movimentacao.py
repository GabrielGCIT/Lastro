"""
Blueprint de movimentação de coletores — retirada, devolução e reporte de status.

Controla o fluxo operacional de check-out e check-in dos coletores no CD.
Todas as rotas exigem login, mas não necessariamente permissão de admin —
qualquer operador autenticado pode registrar retiradas e devoluções.

Ciclo de vida do status de um coletor:
    Disponível → (retirada) → Em Uso → (devolução OK) → Disponível
                                      → (devolução com identificação violada) → Suspenso
                                      → (análise TI + liberar_suspenso) → Disponível
    Disponível → (reporte da operação) → Manutenção / Suspenso

Sobre o campo RE do colaborador:
    É o Registro de Empregado da empresa, não o ID de usuário do sistema.
    Colaboradores não têm conta de acesso ao sistema — quem recebe um coletor é um
    Colaborador cadastrado, não um Usuario. A retirada valida o RE contra a
    tabela `colaboradores` no submit e grava o FK colaborador_id na Movimentacao,
    fechando a custódia num registro real em vez de texto livre.

    O campo continua sendo texto livre na interface (bipe do crachá) — a
    validação acontece no submit, nunca por dropdown ou seletor, para não
    custar tempo ao operador de piso.
"""

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify, abort

from app import db, FOTO_FOLDER
from app.models import (Coletor, Movimentacao, Localidade, DIAGNOSTICO_MAPA, STATUS_COLETOR,
                        ChecklistEntrega, Colaborador, ReativacaoIdentificacao,
                        CategoriaComplemento)
from app.helpers import (login_required, permissao_required, has_permissao, registrar_log,
                         salvar_foto, get_filtro_localidade, empresa_visivel,
                         localidade_no_escopo, criterio_empresa)

movimentacao_bp = Blueprint('movimentacao', __name__)


def _buscar_coletor_no_escopo(modo, valor):
    """Resolve serial/patrimônio de coletor SÓ dentro do escopo da sessão (S1/família E).

    O patrimônio não tem unicidade global (nem por empresa): com homônimos entre
    tenants — ou entre CDs, com o contexto estreitado — um .first() global podia
    devolver o coletor alheio, e o guard pós-lookup transformava isso em falso
    "não encontrado" para o dono legítimo. Filtrar a própria query resolve o
    homônimo certo; coletor de outro tenant segue "inexistente" (nunca 403) e o
    Owner (filtro None) enxerga tudo, incluindo coletor sem localidade.
    """
    campo = (Coletor.serial_number if modo == 'serial'
             else Coletor.numero_patrimonio)
    q = Coletor.query.filter(campo == valor)
    permitidas = get_filtro_localidade()
    if permitidas is not None:
        q = q.filter(Coletor.localidade_id.in_(permitidas)) if permitidas else q.filter(db.false())
    return q.first()


@movimentacao_bp.route('/operacao', methods=['GET'])
@login_required
def operacao_index():
    """Página principal da operação — exibe o formulário de retirada e devolução."""
    return render_template('movimentacao.html', mapa_diagnostico=DIAGNOSTICO_MAPA)


@movimentacao_bp.route('/operacao/retirar', methods=['POST'])
@login_required
def realizar_retirada():
    """
    Registra a retirada de um coletor para um colaborador.

    Validações em ordem:
        1. Resolve o RE contra a tabela `colaboradores`. RE não cadastrado ou
           de colaborador desligado não recebe equipamento — a custódia precisa
           apontar para uma pessoa real e vigente. Esta é a fonte de verdade da
           identidade, e a checagem roda no servidor mesmo que o card da tela
           já tenha confirmado o RE (protege contra POST direto).
        2. Bloqueia se o colaborador já tem uma movimentação aberta — uma
           pessoa, um coletor. Evita que o mesmo RE acumule custódias.
        3. Verifica se o serial existe no sistema.
        4. Faz o update atômico no banco — só muda o status se ainda estiver
           'Disponível'. Isso protege contra race condition em ambientes com
           múltiplos operadores logados simultaneamente: se dois tentarem
           retirar o mesmo coletor ao mesmo tempo, apenas um vai conseguir
           e o outro recebe o status real como feedback.

    A foto na retirada é opcional — se não enviada, o campo fica None no
    registro da movimentação, mas a operação segue normalmente.
    """
    busca_valor    = request.form.get('busca_valor', '').strip().upper()
    busca_modo     = request.form.get('busca_modo', 'patrimonio')
    re_colaborador = (request.form.get('re_colaborador') or '').strip()

    if not re_colaborador or not re_colaborador.isdigit() or len(re_colaborador) < 4:
        flash('ERRO: RE do colaborador inválido.', 'danger')
        return redirect(url_for('movimentacao.operacao_index'))

    # S1/família E + T4a — o RE é resolvido só dentro do tenant da sessão
    # (fail-closed): um operador não retira em nome de um colaborador de outra
    # empresa. Owner vê todos; não-owner sem empresa → zero (db.false()).
    _q_colab = (Colaborador.query.filter_by(re=re_colaborador)
                .filter(criterio_empresa(Colaborador.empresa_id)))
    colaborador = _q_colab.first()
    if not colaborador:
        flash(f'BLOQUEADO: RE {re_colaborador} não está cadastrado como colaborador. '
              'Cadastre-o pelo atalho da tela antes de registrar a retirada.', 'warning')
        return redirect(url_for('movimentacao.operacao_index'))
    if not colaborador.ativo:
        flash(f'BLOQUEADO: {colaborador.nome} (RE {re_colaborador}) está inativo. '
              'Colaborador desligado não pode retirar equipamento.', 'danger')
        return redirect(url_for('movimentacao.operacao_index'))

    # Bloqueia colaborador que já está com um coletor em aberto
    pendencia = Movimentacao.query.filter_by(re_colaborador=re_colaborador, data_retorno=None).first()
    if pendencia:
        coletor_em_maos  = db.session.get(Coletor, pendencia.coletor_id)
        pat_em_maos      = coletor_em_maos.rotulo if coletor_em_maos else 'Desconhecido'
        flash(f'BLOQUEADO: {re_colaborador} já está com o coletor {pat_em_maos}. Devolva antes.', 'warning')
        return redirect(url_for('movimentacao.operacao_index'))

    # S1/família E — o coletor é resolvido só dentro do escopo da sessão;
    # um coletor de outro tenant é tratado como "não encontrado" (nunca 403).
    if busca_modo == 'serial':
        coletor_alvo = _buscar_coletor_no_escopo('serial', busca_valor)
        if not coletor_alvo:
            flash(f'Coletor com serial "{busca_valor}" não encontrado no sistema.', 'danger')
            return redirect(url_for('movimentacao.operacao_index'))
    else:
        coletor_alvo = _buscar_coletor_no_escopo('patrimonio', busca_valor)
        if not coletor_alvo:
            flash(f'Coletor com patrimônio "{busca_valor}" não encontrado no sistema.', 'danger')
            return redirect(url_for('movimentacao.operacao_index'))

    # B6 — coletor DESATIVADO não sai do balcão. Ele continua existindo, com o
    # histórico inteiro, mas foi tirado de operação de propósito: emprestá-lo
    # seria desfazer a decisão por acidente, do lugar mais movimentado do
    # sistema. A mensagem diz o MOTIVO registrado — quem está no balcão precisa
    # saber se aquilo é definitivo ou se alguém desativou por engano.
    if not coletor_alvo.esta_ativo:
        flash(f'BLOQUEADO: O coletor {coletor_alvo.rotulo} está '
              f'desativado ({coletor_alvo.desativado_motivo or "sem motivo registrado"}). '
              f'Fale com a TI antes de emprestá-lo.', 'danger')
        return redirect(url_for('movimentacao.operacao_index'))

    # P3 — Restrição de câmara: bloqueia se ambos têm câmara definida e são diferentes.
    # A câmara vem do Colaborador (quem opera a câmara), não do Usuario de TI.
    # Se coletor.camara ou colaborador.camara for NULL, passa sem aviso — transição gradual.
    if coletor_alvo.camara and colaborador.camara and colaborador.camara != coletor_alvo.camara:
        flash(
            f'BLOQUEADO: O coletor {coletor_alvo.rotulo} pertence à câmara {coletor_alvo.camara}, '
            f'mas o colaborador RE {re_colaborador} opera na câmara {colaborador.camara}.',
            'danger'
        )
        return redirect(url_for('movimentacao.operacao_index'))

    # P3 — Validar checklist de entrega (backend guard — não depende só do JS).
    _campos_checklist = ['estado_visual', 'bateria_ok', 'leitura_ok', 'identificacao_ok', 'equipamento_limpo']
    for _campo in _campos_checklist:
        if request.form.get(_campo) != 'true':
            flash('Checklist de entrega incompleto. Todos os itens são obrigatórios.', 'danger')
            return redirect(url_for('movimentacao.operacao_index'))

    # Atomic check-and-set — o WHERE no status garante que o update só acontece
    # se o coletor ainda estiver Disponível no momento da escrita. Se outro
    # operador pegou o coletor nesse mesmo instante, linhas_afetadas será 0
    # e tratamos o conflito logo abaixo.
    linhas_afetadas = Coletor.query.filter(
        Coletor.id == coletor_alvo.id,
        Coletor.status == 'Disponível',
    ).update({'status': 'Em Uso', 're_colaborador': re_colaborador, 'hora_saida': datetime.now()})

    if linhas_afetadas == 0:
        db.session.rollback()
        # Recarrega do banco para mostrar o status real, não o que estava em cache.
        status_real = db.session.get(Coletor, coletor_alvo.id).status
        flash(f'ERRO: Não foi possível retirar. O status atual é {status_real}.', 'danger')
        return redirect(url_for('movimentacao.operacao_index'))

    foto_filename = salvar_foto(request.files.get('foto_retirada'), f'RET_{busca_valor}')

    nova_mov = Movimentacao(
        coletor_id=coletor_alvo.id,
        re_colaborador=re_colaborador,
        colaborador_id=colaborador.id,
        data_saida=datetime.now(),
        foto_retirada=foto_filename,
    )
    db.session.add(nova_mov)
    db.session.flush()  # obtém nova_mov.id antes do commit para a FK do checklist

    db.session.add(ChecklistEntrega(
        movimentacao_id   = nova_mov.id,
        coletor_id        = coletor_alvo.id,
        analista_re       = session.get('re'),
        analista_nome     = session.get('nome'),
        estado_visual     = True,
        bateria_ok        = True,
        leitura_ok        = True,
        identificacao_ok  = True,
        equipamento_limpo = True,
    ))

    # H2 — o que saiu JUNTO com o coletor (baterias, headset pareado, suporte).
    # Dentro da mesma transação de propósito: complemento gravado sem a
    # movimentação — ou o inverso — seria dívida que ninguém consegue cobrar.
    # O portão de compatibilidade (D6) recusa aqui, antes do commit.
    from app.complementos import (ComplementoRecusado, ler_selecoes_do_form,
                                  registrar_saida)
    try:
        complementos = registrar_saida(nova_mov, coletor_alvo,
                                       ler_selecoes_do_form(request.form),
                                       operador=session.get('nome'))
    except ComplementoRecusado as recusa:
        db.session.rollback()
        flash(f'BLOQUEADO: {recusa}', 'danger')
        return redirect(url_for('movimentacao.operacao_index'))

    resumo = ''
    if complementos:
        partes = []
        for linha in complementos:
            cat = db.session.get(CategoriaComplemento, linha.categoria_id)
            nome = cat.nome if cat else linha.categoria_id
            partes.append(f'{linha.qtd_saida}x {nome}'
                          + (' [DISPENSA]' if linha.dispensa_motivo else ''))
        resumo = ' + complementos: ' + ', '.join(partes)

    registrar_log('CHECK_OUT', f'Retirada pat.{coletor_alvo.numero_patrimonio} ({coletor_alvo.serial_number}) para {colaborador.nome} (RE {re_colaborador})' + (' [com foto]' if foto_filename else '') + resumo)
    db.session.commit()

    flash(f'SUCESSO: Coletor {coletor_alvo.rotulo} liberado para {colaborador.nome} (RE {re_colaborador}).', 'success')
    return redirect(url_for('movimentacao.operacao_index'))


def colaborador_nome_da(mov):
    """Nome de quem levou, para a mensagem de pendência. RE quando não há vínculo."""
    if mov.colaborador:
        return f'{mov.colaborador.nome} (RE {mov.re_colaborador})'
    return f'RE {mov.re_colaborador}'


@movimentacao_bp.route('/operacao/devolver', methods=['POST'])
@login_required
def realizar_devolucao():
    """
    Registra a devolução de um coletor e atualiza seu status conforme o estado da identificação.

    Fluxo de status na devolução:
        - Identificação OK      → coletor volta para 'Disponível'
        - Identificação ROMPIDA → coletor vai para 'Suspenso' (aguarda análise da TI)

    Comportamento especial:
        - Se o coletor constar como 'Em Uso' mas não houver movimentação aberta
          no banco, há uma inconsistência de estado (pode ter sido causada por
          edição direta via admin sem fechar o registro). Nesse caso, corrige
          automaticamente e libera o coletor sem criar um registro incompleto.
        - Foto é obrigatória quando a identificação está ROMPIDA — serve como evidência
          para a análise da TI. Sem foto, a devolução com violação é bloqueada.
          Para identificação OK, a foto é opcional.
    """
    busca_valor       = request.form.get('busca_valor', '').strip().upper()
    busca_modo        = request.form.get('busca_modo', 'patrimonio')
    status_identificacao = request.form.get('status_identificacao')
    voltou_danificado = request.form.get('voltou_danificado') == 'on'
    cat               = request.form.get('categoria_defeito', '').strip() or None
    det               = request.form.get('detalhe_defeito', '').strip() or None
    desc              = request.form.get('descricao_problema', '').strip() or None

    # S1/família E — coletor de outro tenant é tratado como "não encontrado".
    if busca_modo == 'serial':
        coletor = _buscar_coletor_no_escopo('serial', busca_valor)
        if not coletor:
            flash(f'ERRO: Coletor com serial "{busca_valor}" não encontrado no sistema.', 'danger')
            return redirect(url_for('movimentacao.operacao_index'))
    else:
        coletor = _buscar_coletor_no_escopo('patrimonio', busca_valor)
        if not coletor:
            flash(f'ERRO: Coletor com patrimônio "{busca_valor}" não encontrado no sistema.', 'danger')
            return redirect(url_for('movimentacao.operacao_index'))

    if coletor.status != 'Em Uso':
        flash(f'AVISO: O coletor {coletor.rotulo} já consta como {coletor.status}. Não é necessário devolver.', 'warning')
        return redirect(url_for('movimentacao.operacao_index'))

    foto_filename = salvar_foto(request.files.get('foto_devolucao'), f'DEV_{busca_valor}')

    # Foto obrigatória quando identificação violada ou equipamento danificado —
    # Validação ANTES do UPDATE atômico: garante que qualquer saída antecipada
    # aconteça sem nenhum write pendente no banco (sem risco de estado preso).
    if (status_identificacao == 'ROMPIDO' or voltou_danificado) and not foto_filename:
        flash('ERRO: Foto obrigatória ao reportar identificação violada ou equipamento danificado. Tire a foto e tente novamente.', 'danger')
        return redirect(url_for('movimentacao.operacao_index'))

    # Atomic check-and-set — o WHERE no status garante que o update só acontece
    # se o coletor ainda estiver 'Em Uso' no momento da escrita. Se outro
    # operador devolveu nesse mesmo instante, linhas_afetadas será 0.
    # 'Processando' é um estado intermediário transiente: todas as saídas
    # restantes abaixo fazem commit (status final) ou rollback — nunca retornam
    # sem resolver, então o coletor não fica preso neste estado.
    linhas_afetadas = Coletor.query.filter(
        Coletor.id == coletor.id,
        Coletor.status == 'Em Uso',
    ).update({'status': 'Processando'})
    if linhas_afetadas == 0:
        db.session.rollback()
        flash(f'AVISO: O coletor {coletor.rotulo} não está mais em uso ou já foi devolvido por outro operador.', 'warning')
        return redirect(url_for('movimentacao.operacao_index'))

    mov = Movimentacao.query.filter_by(coletor_id=coletor.id, data_retorno=None).first()
    if not mov:
        # Estado inconsistente — coletor marcado como Em Uso mas sem
        # movimentação aberta. Corrige automaticamente sem criar registro
        # incompleto, que poluiria o histórico.
        coletor.status         = 'Disponível'
        coletor.re_colaborador = None
        coletor.hora_saida     = None
        db.session.commit()
        flash(f'Correção automática: O coletor {coletor.rotulo} estava travado e foi liberado.', 'info')
        return redirect(url_for('movimentacao.operacao_index'))

    mov.data_retorno         = datetime.now()
    mov.status_identificacao_retorno = status_identificacao

    # H3 — confere o que voltou junto. A devolução NUNCA é bloqueada por falta:
    # o coletor volta de qualquer jeito e o que não voltou fica registrado como
    # pendência. Travar a devolução porque o headset sumiu deixaria o coletor
    # preso em campo — punindo o inventário para cobrar um acessório.
    from app.complementos import conferir_devolucao, ler_conferencias_do_form
    faltantes = conferir_devolucao(mov, ler_conferencias_do_form(request.form))
    mov.foto_devolucao               = foto_filename
    coletor.re_colaborador   = None
    coletor.hora_saida       = None

    if status_identificacao == 'ROMPIDO':
        # Identificação violada tem precedência — suspende o coletor para análise da TI.
        # Se também veio danificado, o diagnóstico do analista é preservado;
        # caso contrário, usa os valores padrão de violação.
        coletor.status            = 'Suspenso'
        coletor.categoria_defeito = cat or 'FISICO'
        coletor.detalhe_defeito   = det or 'VIOLACAO'
        coletor.descricao_problema= desc or 'Suspenso na devolução: Identificação Violada.'
        registrar_log('CHECK_IN_VIOLADO', f'Devolução pat.{coletor.numero_patrimonio} ({coletor.serial_number}) com IDENTIFICAÇÃO VIOLADA [foto: {foto_filename}].')
        flash(f'ALERTA: Coletor {coletor.rotulo} recebido com IDENTIFICAÇÃO VIOLADA. Suspenso e foto registrada.', 'warning')
    elif voltou_danificado:
        coletor.status            = 'Manutenção'
        coletor.categoria_defeito = cat
        coletor.detalhe_defeito   = det
        coletor.descricao_problema= desc or 'Defeito reportado na devolução.'
        registrar_log('CHECK_IN_DANIFICADO', f'Devolução pat.{coletor.numero_patrimonio} com defeito reportado [foto: {foto_filename}]. Cat: {cat} / Det: {det}')
        flash(f'ATENÇÃO: Coletor {coletor.rotulo} recebido com defeito. Encaminhado para Manutenção.', 'warning')
    else:
        coletor.status = 'Disponível'
        registrar_log('CHECK_IN', f'Devolução pat.{coletor.numero_patrimonio} OK.' + (' [com foto]' if foto_filename else ''))
        flash(f'Sucesso: Coletor {coletor.rotulo} devolvido e pronto para uso.', 'success')

    # H3 — o aviso de pendência vale para QUALQUER desfecho (devolvido, suspenso
    # ou para manutenção): o complemento que não voltou é dívida do colaborador,
    # independente do estado em que o coletor chegou. Por isso fica depois do
    # if/elif/else, e não dentro de um ramo.
    if faltantes:
        pendencias = ', '.join(
            f'{l.faltando}x {l.categoria.nome if l.categoria else "?"}'
            + (f' ({l.item.identificador})' if l.item else '')
            for l in faltantes)
        flash(f'PENDÊNCIA: {colaborador_nome_da(mov)} não devolveu {pendencias}. '
              f'Registrado no histórico do coletor.', 'warning')

    db.session.commit()
    return redirect(url_for('movimentacao.operacao_index'))


@movimentacao_bp.route('/operacao/inventario', methods=['GET', 'POST'])
@login_required
def operacao_inventario():
    """
    Permite que operadores reportem problemas ou alterem o status de coletores.

    Diferente do gerenciamento admin em /coletores, essa tela é voltada para
    o operador de piso — menos campos, sem acesso a patrimônio/localidade,
    focado em reportar ocorrências durante o turno.

    Restrições importantes:
        - Liberar um equipamento em status Manutenção ou Suspenso para
          Disponível exige a permissão 'inventario.liberar_suspenso'. Essa
          permissão normalmente é concedida apenas ao time de TI — é a
          confirmação de que o equipamento foi analisado e está apto para uso.
        - Se o coletor estava Em Uso e é reportado com outro status, a
          movimentação aberta é fechada automaticamente com marcação
          'Reporte: <status>' para rastreabilidade.
    """
    if request.method == 'POST':
        c_id        = request.form.get('id')
        status_novo = request.form.get('status')
        cat         = request.form.get('categoria_defeito')
        det         = request.form.get('detalhe_defeito')
        desc        = request.form.get('descricao_problema')

        if status_novo not in STATUS_COLETOR:
            flash('Status inválido.', 'danger')
            return redirect(url_for('movimentacao.operacao_inventario'))

        # 🔴 Coletor fora do escopo de quem reporta é inexistente (família B):
        # sem isto, um operador de um CD alterava o status de coletor de outro.
        coletor = db.session.get(Coletor, c_id)
        permitidas = get_filtro_localidade()
        if not coletor or (permitidas is not None and coletor.localidade_id not in permitidas):
            abort(404)

        # Liberar equipamento suspenso/manutenção exige permissão específica da TI.
        # Sem essa verificação, qualquer operador poderia colocar em uso um
        # equipamento que ainda não passou pela análise técnica.
        if coletor.status in ['Manutenção', 'Suspenso'] and status_novo == 'Disponível':
            if not has_permissao('inventario.liberar_suspenso'):
                flash('ERRO: Você não tem permissão para liberar um equipamento em Manutenção/Suspenso.', 'danger')
                registrar_log('BLOCK_BYPASS', f'{session.get("nome")} tentou liberar {coletor.serial_number} sem autorização.')
                return redirect(url_for('movimentacao.operacao_inventario'))

        # Padroniza os campos de defeito ao suspender via reporte da operação —
        # mantém consistência com os mesmos valores usados na devolução com identificação violada.
        if status_novo == 'Suspenso':
            cat, det, desc = 'FISICO', 'VIOLACAO', 'SUSPENSÃO: Identificação violada reportada pela operação.'

        # Se o coletor está Em Uso e vai para outro status, fecha a movimentação
        # aberta para não deixar registro pendente no histórico.
        if coletor.status == 'Em Uso' and status_novo != 'Em Uso':
            mov = Movimentacao.query.filter_by(coletor_id=coletor.id, data_retorno=None).first()
            if mov:
                mov.data_retorno         = datetime.now()
                mov.status_identificacao_retorno = f'Reporte: {status_novo}'
            coletor.re_colaborador = None
            coletor.hora_saida     = None

        coletor.status            = status_novo
        coletor.categoria_defeito = cat
        coletor.detalhe_defeito   = det
        coletor.descricao_problema= desc

        registrar_log('REPORT_OP', f'Status alterado {coletor.serial_number} → {status_novo} por {session.get("nome")}.')
        db.session.commit()
        flash('Status atualizado com sucesso!', 'success')
        return redirect(url_for('movimentacao.operacao_inventario'))

    # Filtros opcionais via query string — mesma mecânica da tela de Inventário Ativos.
    filtro_status = request.args.get('status', '').strip()
    filtro_loc    = request.args.get('localidade', '').strip()

    # Base: coletores filtrados pelo contexto do usuário.
    # P7 — aplica hierarquia geográfica; CD não vê coletores de outros CDs.
    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is None:
        q = Coletor.query
    elif ids_permitidos:
        q = Coletor.query.filter(Coletor.localidade_id.in_(ids_permitidos))
    else:
        q = Coletor.query.filter(False)

    if filtro_status:
        q = q.filter(Coletor.status == filtro_status)
    if filtro_loc:
        loc_obj = Localidade.query.filter_by(sigla=filtro_loc).first()
        if loc_obj:
            q = q.filter(Coletor.localidade_id == loc_obj.id)

    coletores   = q.order_by(Coletor.id).all()
    localidades = Localidade.query.order_by(Localidade.sigla).all()

    return render_template('operacao_inventario.html',
                           coletores=coletores,
                           localidades=localidades,
                           filtro_status=filtro_status,
                           filtro_loc=filtro_loc)


# --- Servir arquivos de upload (com proteção de login) ---

@movimentacao_bp.route('/uploads/fotos/<filename>')
@login_required
def foto_file(filename):
    """
    Serve fotos de retirada/devolução de coletor — tenant-aware (T3).

    Fotos ficam em FOTO_FOLDER e podem conter evidências de violação. Resolve o
    DONO do filename (Movimentacao foto_retirada/devolucao, ou a foto de
    reativação de identificação) → Coletor → localidade → empresa. Empresa
    visível → serve; senão 404. O Owner enxerga tudo.
    """
    dono_emp = None
    achou = False
    mov = (Movimentacao.query
           .filter(db.or_(Movimentacao.foto_retirada == filename,
                          Movimentacao.foto_devolucao == filename))
           .first())
    if mov is not None:
        achou = True
        dono_emp = (mov.coletor.localidade.empresa_id
                    if (mov.coletor and mov.coletor.localidade) else None)
    else:
        reat = ReativacaoIdentificacao.query.filter_by(foto_identificacao=filename).first()
        if reat is not None:
            achou = True
            dono_emp = (reat.coletor.localidade.empresa_id
                        if (reat.coletor and reat.coletor.localidade) else None)

    if not achou or not empresa_visivel(dono_emp):
        abort(404)
    return send_from_directory(FOTO_FOLDER, filename)


@movimentacao_bp.route('/api/coletor/buscar')
@login_required
def api_buscar_coletor():
    """
    Lookup de coletor para o card de feedback em tempo real na tela de movimentação.

    Parâmetros:
        q    — valor digitado/bipado (patrimônio ou serial)
        modo — 'patrimonio' (padrão) ou 'serial'

    Retorna JSON com dados suficientes para renderizar o card no frontend.
    S3/família C — escopado por empresa (fail-closed): um coletor de outro
    tenant é tratado como não encontrado, mesmo padrão da retirada/devolução.
    """
    q    = request.args.get('q', '').strip().upper()
    modo = request.args.get('modo', 'patrimonio')

    if not q:
        return jsonify({'encontrado': False})

    coletor = _buscar_coletor_no_escopo('serial' if modo == 'serial' else 'patrimonio', q)
    if not coletor:
        return jsonify({'encontrado': False})

    pode_ver_re = has_permissao('painel_op.ver')
    return jsonify({
        'encontrado':  True,
        'id':          coletor.id,
        'patrimonio':  coletor.patrimonio_display,
        # o card do balcão usa 'rotulo' no título: com patrimônio vazio,
        # 'patrimonio' vem '—' e o operador fica olhando um traço em negrito.
        'rotulo':      coletor.rotulo,
        'serial':      coletor.serial_number,
        'modelo':      coletor.modelo or 'MC9190G',
        'status':      coletor.status,
        'camara':      coletor.camara or None,
        'em_uso_por':  coletor.re_colaborador if pode_ver_re else None,
        'hora_saida':  coletor.hora_saida.strftime('%d/%m %H:%M') if (coletor.hora_saida and pode_ver_re) else None,
        'localidade':  coletor.localidade.nome if coletor.localidade else None,
    })


@movimentacao_bp.route('/api/coletor/<int:coletor_id>/complementos')
@login_required
def api_complementos_do_coletor(coletor_id):
    """O que deve sair junto com ESTE coletor — alimenta o card da retirada.

    O sistema já sabe qual coletor é, então propõe: a bateria compatível com a
    câmara dele (D5) e o headset pareado do voice picking (D2). O operador
    confirma em vez de digitar — é o que mantém o balcão rápido.

    S3/família C: coletor fora do escopo é tratado como inexistente, mesmo
    padrão das outras APIs desta tela.
    """
    from app.complementos import BATERIAS_PADRAO, sugerir_para_coletor

    coletor = db.session.get(Coletor, coletor_id)
    if coletor and not localidade_no_escopo(coletor.localidade_id):
        coletor = None
    if not coletor:
        return jsonify({'encontrado': False}), 404

    empresa_id = coletor.localidade.empresa_id if coletor.localidade else None
    sugestao = sugerir_para_coletor(empresa_id, coletor)
    sugestao['encontrado'] = True
    sugestao['baterias_padrao'] = BATERIAS_PADRAO
    return jsonify(sugestao)


@movimentacao_bp.route('/api/coletor/<int:coletor_id>/em-campo')
@login_required
def api_em_campo(coletor_id):
    """O que saiu com este coletor e ainda não voltou — alimenta a conferência.

    O operador não precisa lembrar o que o colaborador levou: o sistema mostra.
    É a diferença entre cobrar e torcer para alguém lembrar.
    """
    from app.complementos import em_campo_da_movimentacao

    coletor = db.session.get(Coletor, coletor_id)
    if coletor and not localidade_no_escopo(coletor.localidade_id):
        coletor = None
    if not coletor:
        return jsonify({'encontrado': False}), 404

    mov = Movimentacao.query.filter_by(coletor_id=coletor.id, data_retorno=None).first()
    if not mov:
        return jsonify({'encontrado': True, 'pendentes': []})
    return jsonify({'encontrado': True, 'pendentes': em_campo_da_movimentacao(mov)})


@movimentacao_bp.route('/api/coletor/status/<int:coletor_id>')
@login_required
def api_coletor_status(coletor_id):
    """Status atual de um coletor específico por ID — usado pelo polling de 15s da tela de movimentação.

    S3/família C — escopado por empresa (fail-closed): coletor de outro tenant
    é tratado como não encontrado.
    """
    coletor = db.session.get(Coletor, coletor_id)
    if coletor and not localidade_no_escopo(coletor.localidade_id):
        coletor = None
    if not coletor:
        return jsonify({'encontrado': False}), 404
    pode_ver_re = has_permissao('painel_op.ver')
    return jsonify({
        'encontrado':  True,
        'id':          coletor.id,
        'status':      coletor.status,
        'em_uso_por':  coletor.re_colaborador if pode_ver_re else None,
        'hora_saida':  coletor.hora_saida.strftime('%d/%m %H:%M') if (coletor.hora_saida and pode_ver_re) else None,
    })
