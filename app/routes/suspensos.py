"""
Blueprint de gestão de coletores suspensos por violação de identificação.

Exclusivo para grupos TI e TI_MASTER — protegido pela permissão
'inventario.liberar_suspenso'. Nenhum outro grupo tem acesso.

Fluxo de reativação (dois fatores obrigatórios):
  1. TI instala nova identificação → registra no sistema com foto e declaração de responsabilidade
  2. Segundo TI (diferente do primeiro) revisa e aprova
  3. Coletor vai para Disponível somente após a aprovação

Esse blueprint é completamente separado dos lotes de manutenção —
uma reativação de identificação não é manutenção técnica e não deve aparecer
no fluxo de /manutencao.
"""

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from sqlalchemy.orm import selectinload

from app import db, FOTO_FOLDER
from app.models import Coletor, ReativacaoIdentificacao
from app.helpers import (login_required, permissao_required, has_permissao, registrar_log,
                         salvar_foto, get_filtro_localidade, localidade_no_escopo)

suspensos_bp = Blueprint('suspensos', __name__)


# ---------------------------------------------------------------------------
# TELA PRINCIPAL
# ---------------------------------------------------------------------------

@suspensos_bp.route('/suspensos', methods=['GET'])
@permissao_required('inventario.liberar_suspenso')
def suspensos_index():
    """
    Exibe os coletores atualmente suspensos e o histórico completo de reativações.

    Duas seções separadas:
      - Suspensos ativos: coletores com status 'Suspenso' aguardando análise da TI
      - Histórico: todos os registros de reativação (aprovados e pendentes)

    Para cada suspenso, pré-carregamos a última movimentação para exibir
    quem estava com o equipamento antes da suspensão — sem query extra por coletor
    porque o volume de suspensos é sempre baixo (dezenas no máximo).
    """
    # P7 — filtrar suspensos pelo contexto ativo do usuário
    ids_permitidos = get_filtro_localidade()
    # selectinload pré-carrega as movimentações de todos os suspensos em 2 queries
    # flat (SELECT IN), evitando N+1 no loop ultimo_mov abaixo.
    _opts = Coletor.query.options(selectinload(Coletor.movimentacoes))
    if ids_permitidos is None:
        _q_sus = _opts.filter_by(status='Suspenso')
    elif ids_permitidos:
        _q_sus = (_opts
                  .filter_by(status='Suspenso')
                  .filter(Coletor.localidade_id.in_(ids_permitidos)))
    else:
        _q_sus = Coletor.query.filter(False)

    suspensos = (
        _q_sus
        .order_by(Coletor.id)
        .all()
    )

    # Última movimentação de cada suspenso — indica quem estava com o equipamento.
    # Feito aqui em Python (não no template) para manter o template limpo.
    ultimo_mov = {}
    for c in suspensos:
        movs = sorted(c.movimentacoes, key=lambda m: m.data_saida, reverse=True)
        ultimo_mov[c.id] = movs[0] if movs else None

    historico_q = ReativacaoIdentificacao.query.join(Coletor)
    if ids_permitidos is not None:
        historico_q = historico_q.filter(Coletor.localidade_id.in_(ids_permitidos))

    historico = (
        historico_q
        .order_by(ReativacaoIdentificacao.data_reativacao.desc())
        .all()
    )

    # RE do usuário logado — usado no template para esconder botão Aprovar
    # de quem criou a reativação (a verificação real é feita no backend também).
    re_atual = session.get('re')

    return render_template(
        'suspensos.html',
        suspensos=suspensos,
        ultimo_mov=ultimo_mov,
        historico=historico,
        re_atual=re_atual,
    )


# ---------------------------------------------------------------------------
# INICIAR REATIVAÇÃO
# ---------------------------------------------------------------------------

@suspensos_bp.route('/suspensos/reativar', methods=['POST'])
@permissao_required('inventario.liberar_suspenso')
def reativar():
    """
    Registra a instalação de uma nova identificação em um coletor suspenso.

    Cria o registro de ReativacaoIdentificacao com status PENDENTE_APROVACAO —
    o coletor ainda permanece Suspenso até que um segundo TI aprove.

    Validações obrigatórias:
      - Declaração de responsabilidade deve estar marcada
      - Foto da identificação instalada é obrigatória (evidência física)
    """
    coletor_id = request.form.get('coletor_id')
    declaracao = request.form.get('declaracao') == 'on'

    if not declaracao:
        flash('ERRO: Você precisa confirmar a declaração de responsabilidade para prosseguir.', 'danger')
        return redirect(url_for('suspensos.suspensos_index'))

    # S2/família B — coletor de outro tenant é tratado como inexistente.
    coletor = db.session.get(Coletor, coletor_id)
    if not coletor or not localidade_no_escopo(coletor.localidade_id):
        flash('Coletor não encontrado.', 'danger')
        return redirect(url_for('suspensos.suspensos_index'))

    if coletor.status != 'Suspenso':
        flash(f'AVISO: O coletor {coletor.rotulo} não está mais suspenso.', 'warning')
        return redirect(url_for('suspensos.suspensos_index'))

    # Verifica se já existe uma reativação pendente para este coletor —
    # evita registros duplicados caso o TI clique duas vezes.
    pendente = ReativacaoIdentificacao.query.filter_by(
        coletor_id=coletor.id, status='PENDENTE_APROVACAO'
    ).first()
    if pendente:
        flash(f'Este coletor já possui uma reativação pendente de aprovação (#{pendente.id}).', 'warning')
        return redirect(url_for('suspensos.suspensos_index'))

    foto_filename = salvar_foto(request.files.get('foto_identificacao'), f'ID_{coletor.serial_number}')
    if not foto_filename:
        flash('ERRO: Foto do coletor com a identificação instalada é obrigatória.', 'danger')
        return redirect(url_for('suspensos.suspensos_index'))

    reativacao = ReativacaoIdentificacao(
        coletor_id          = coletor.id,
        reativado_por_re    = session.get('re'),
        reativado_por_nome  = session.get('nome'),
        foto_identificacao  = foto_filename,
        declaracao_aceita   = True,
    )
    db.session.add(reativacao)
    registrar_log(
        'REATIVACAO_INICIADA',
        f'Reativação de {coletor.serial_number} iniciada por {session.get("nome")}. Aguardando aprovação.'
    )
    db.session.commit()

    flash(
        f'Reativação do coletor {coletor.rotulo} registrada com sucesso. '
        'Aguardando aprovação de outro membro da TI.',
        'info'
    )
    return redirect(url_for('suspensos.suspensos_index'))


# ---------------------------------------------------------------------------
# APROVAR REATIVAÇÃO
# ---------------------------------------------------------------------------

@suspensos_bp.route('/suspensos/reativacao/<int:reativacao_id>/aprovar', methods=['POST'])
@permissao_required('inventario.liberar_suspenso')
def aprovar_reativacao(reativacao_id):
    """
    Aprova uma reativação pendente e libera o coletor para uso.

    Regra de negócio central: quem iniciou a reativação NÃO pode aprovar.
    Essa verificação é feita tanto no frontend (botão escondido) quanto aqui
    no backend — dupla proteção contra bypass via POST direto.

    Ao aprovar:
      - Status do coletor → Disponível
      - Campos de defeito limpos (categoria, detalhe, descrição)
      - Reativacao → APROVADO com data e responsável registrados
    """
    # S2/família B — reativação de coletor de outro tenant é inexistente.
    reativacao = db.session.get(ReativacaoIdentificacao, reativacao_id)
    if not reativacao or not localidade_no_escopo(reativacao.coletor.localidade_id):
        flash('Reativação não encontrada.', 'danger')
        return redirect(url_for('suspensos.suspensos_index'))

    # Proteção de negócio — não pode auto-aprovar
    if reativacao.reativado_por_re == session.get('re'):
        flash('ERRO: Você não pode aprovar uma reativação que você mesmo iniciou.', 'danger')
        registrar_log(
            'SECURITY_ALERT',
            f'{session.get("re")} tentou auto-aprovar a reativação #{reativacao_id}.'
        )
        return redirect(url_for('suspensos.suspensos_index'))

    if reativacao.status != 'PENDENTE_APROVACAO':
        flash('Esta reativação já foi processada.', 'warning')
        return redirect(url_for('suspensos.suspensos_index'))

    # Aprova e libera o coletor
    reativacao.status           = 'APROVADO'
    reativacao.aprovado_por_re  = session.get('re')
    reativacao.aprovado_por_nome = session.get('nome')
    reativacao.data_aprovacao   = datetime.now()

    coletor = reativacao.coletor
    coletor.status             = 'Disponível'
    coletor.categoria_defeito  = None
    coletor.detalhe_defeito    = None
    coletor.descricao_problema = None

    registrar_log(
        'REATIVACAO_APROVADA',
        f'Coletor {coletor.serial_number} reativado — aprovado por {session.get("nome")}. '
        f'Iniciado por {reativacao.reativado_por_nome}.'
    )
    db.session.commit()

    flash(
        f'Coletor {coletor.rotulo} aprovado e disponível para uso.',
        'success'
    )
    return redirect(url_for('suspensos.suspensos_index'))
