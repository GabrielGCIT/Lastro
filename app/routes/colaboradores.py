"""
Blueprint de gestão de Colaboradores.

Colaboradores são funcionários operacionais que recebem coletores mas não
têm acesso ao sistema.

Permissões:
    - 'admin.colaboradores' — CRUD completo
"""

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify,
)
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Colaborador, CAMARA_CHOICES, TURNO_CHOICES
from app.helpers import (login_required, permissao_required, registrar_log,
                         re_colaborador_em_uso, empresa_visivel, criterio_empresa,
                         empresa_para_escrita)

colaboradores_bp = Blueprint('colaboradores', __name__, url_prefix='/admin/colaboradores')


# ---------------------------------------------------------------------------
# LISTAGEM + CRUD
# ---------------------------------------------------------------------------

@colaboradores_bp.route('/')
@permissao_required('admin.colaboradores')
def lista():
    filtro_q      = request.args.get('q', '').strip()
    filtro_status = request.args.get('status', 'ativo')   # ativo | inativo | todos

    # S3/família C + T4a — leitura escopada por empresa (fail-closed); Owner vê todos.
    q = Colaborador.query.filter(criterio_empresa(Colaborador.empresa_id))
    if filtro_q:
        like = f'%{filtro_q}%'
        q = q.filter(db.or_(
            Colaborador.re.ilike(like),
            Colaborador.nome.ilike(like),
        ))
    if filtro_status == 'ativo':
        q = q.filter_by(ativo=True)
    elif filtro_status == 'inativo':
        q = q.filter_by(ativo=False)

    colaboradores = q.order_by(Colaborador.nome).all()
    return render_template(
        'colaboradores_lista.html',
        colaboradores=colaboradores,
        camara_choices=CAMARA_CHOICES,
        turno_choices=TURNO_CHOICES,
        filtro_q=filtro_q,
        filtro_status=filtro_status,
    )


@colaboradores_bp.route('/novo', methods=['POST'])
@permissao_required('admin.colaboradores')
def novo():
    re   = request.form.get('re', '').strip().upper()
    nome = request.form.get('nome', '').strip()

    if not re or not nome:
        flash('RE e Nome são obrigatórios.', 'danger')
        return redirect(url_for('colaboradores.lista'))

    # T4c — nasce na empresa EFETIVA (a impersonada, quando o Owner opera dentro de
    # um tenant), não na session['empresa_id'] crua que o _sync_permissoes fixa na
    # empresa do Owner. Espelha grupos/fornecedores; sem isso o Owner cadastrando
    # dentro de um cliente gravava o colaborador na MB.
    empresa_alvo = empresa_para_escrita()
    if empresa_alvo is None:
        flash('Escolha uma empresa no topo da tela para cadastrar um colaborador.', 'warning')
        return redirect(url_for('colaboradores.lista'))

    # S1/família D — RE único POR empresa (constraint uq_colaboradores_empresa_re):
    # um mesmo RE pode existir em tenants diferentes sem conflito.
    if re_colaborador_em_uso(re, empresa_alvo):
        flash(f'Já existe um colaborador com o RE {re}.', 'warning')
        return redirect(url_for('colaboradores.lista'))

    administrativo = request.form.get('administrativo') == '1'
    colaborador = Colaborador(
        re=re,
        nome=nome,
        cargo=request.form.get('cargo', '').strip() or None,
        departamento=request.form.get('departamento', '').strip() or None,
        administrativo=administrativo,
        camara=None if administrativo else (request.form.get('camara') or None),
        turno=None if administrativo else (request.form.get('turno') or None),
        empresa_id=empresa_alvo,
    )
    db.session.add(colaborador)
    db.session.commit()
    registrar_log('COLABORADOR_CRIAR', f'Colaborador RE:{re} — {nome} cadastrado.')
    flash(f'Colaborador {nome} cadastrado.', 'success')
    return redirect(url_for('colaboradores.lista'))


@colaboradores_bp.route('/<int:colab_id>/editar', methods=['POST'])
@permissao_required('admin.colaboradores')
def editar(colab_id):
    # S2/família B — colaborador de outro tenant é tratado como inexistente.
    colab = db.session.get(Colaborador, colab_id)
    if not colab or not empresa_visivel(colab.empresa_id):
        flash('Colaborador não encontrado.', 'danger')
        return redirect(url_for('colaboradores.lista'))

    re   = request.form.get('re', '').strip().upper()
    nome = request.form.get('nome', '').strip()

    if not re or not nome:
        flash('RE e Nome são obrigatórios.', 'danger')
        return redirect(url_for('colaboradores.lista'))

    # S1/família D — duplicidade de RE checada dentro da empresa do colaborador
    # (exceto o próprio registro); RE homônimo em outro tenant não bloqueia.
    if re_colaborador_em_uso(re, colab.empresa_id, excluir_id=colab_id):
        flash(f'RE {re} já está em uso por outro colaborador.', 'warning')
        return redirect(url_for('colaboradores.lista'))

    administrativo = request.form.get('administrativo') == '1'
    colab.re            = re
    colab.nome          = nome
    colab.cargo         = request.form.get('cargo', '').strip() or None
    colab.departamento  = request.form.get('departamento', '').strip() or None
    colab.administrativo = administrativo
    colab.camara        = None if administrativo else (request.form.get('camara') or None)
    colab.turno         = None if administrativo else (request.form.get('turno') or None)

    db.session.commit()
    registrar_log('COLABORADOR_EDITAR', f'Colaborador #{colab_id} RE:{re} atualizado.')
    flash('Colaborador atualizado.', 'success')
    return redirect(url_for('colaboradores.lista'))


@colaboradores_bp.route('/<int:colab_id>/toggle', methods=['POST'])
@permissao_required('admin.colaboradores')
def toggle_ativo(colab_id):
    # S2/família B — colaborador de outro tenant é tratado como inexistente.
    colab = db.session.get(Colaborador, colab_id)
    if not colab or not empresa_visivel(colab.empresa_id):
        flash('Colaborador não encontrado.', 'danger')
        return redirect(url_for('colaboradores.lista'))

    colab.ativo = not colab.ativo
    db.session.commit()
    status = 'reativado' if colab.ativo else 'desativado'
    registrar_log('COLABORADOR_TOGGLE', f'Colaborador #{colab_id} RE:{colab.re} {status}.')
    flash(f'Colaborador {colab.nome} {status}.', 'success')
    return redirect(url_for('colaboradores.lista'))


# ---------------------------------------------------------------------------
# API — busca para live search nos modais de movimentação
# ---------------------------------------------------------------------------

@colaboradores_bp.route('/api/buscar')
@login_required
def api_buscar():
    """Busca colaboradores por RE ou nome para o live search dos modais.

    Retorna apenas colaboradores ativos. Limitado a 10 resultados.
    Endpoint sob /admin/colaboradores/api/buscar — inclui o prefix do blueprint.
    Como começa com /admin (não /api/), o _sync_permissoes roda normalmente.
    """
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    like = f'%{q}%'
    qy = Colaborador.query.filter(
        Colaborador.ativo == True,  # noqa: E712
        db.or_(
            Colaborador.re.ilike(like),
            Colaborador.nome.ilike(like),
        )
    )
    # T3/T4a — a busca só enxerga colaboradores da própria empresa. Owner vê
    # todos; criterio_empresa é fail-closed (não-owner sem empresa → zero).
    qy = qy.filter(criterio_empresa(Colaborador.empresa_id))
    resultados = qy.order_by(Colaborador.nome).limit(10).all()
    return jsonify([
        {
            'id':    c.id,
            're':    c.re,
            'nome':  c.nome,
            'cargo': c.cargo or '',
            'label': f'{c.re} — {c.nome}',
        }
        for c in resultados
    ])


@colaboradores_bp.route('/api/re/<re_busca>')
@login_required
def api_por_re(re_busca):
    """Lookup de colaborador por RE exato — card de confirmação da tela de movimentação.

    Deliberadamente NÃO é um live search: recebe o RE completo (bipado ou
    digitado) e responde sim/não. Não devolve lista, não sugere, não abre
    dropdown — o operador de piso não pode perder tempo escolhendo numa lista.

    Colaborador inativo retorna encontrado=False com o motivo, para que o card
    explique o bloqueio em vez de deixar o operador submeter e tomar o erro
    só depois — a mesma regra que realizar_retirada aplica no servidor.
    """
    # T3/T4a — lookup escopado por empresa (fail-closed): um tenant não confirma
    # o RE de outro. Owner vê todos; não-owner sem empresa → zero (db.false()).
    q = (Colaborador.query.filter_by(re=re_busca.strip().upper())
         .filter(criterio_empresa(Colaborador.empresa_id)))
    colab = q.first()
    if not colab:
        return jsonify({'encontrado': False, 'motivo': 'nao_cadastrado'}), 404
    if not colab.ativo:
        return jsonify({'encontrado': False, 'motivo': 'inativo', 'nome': colab.nome}), 404
    return jsonify({
        'encontrado': True,
        'id':         colab.id,
        're':         colab.re,
        'nome':       colab.nome,
        'cargo':      colab.cargo or '—',
    })


@colaboradores_bp.route('/api/criar-campo', methods=['POST'])
@login_required
def api_criar_campo():
    """Cadastro rápido de colaborador direto da tela de movimentação (mobile/campo).

    Existe porque colaboradores novos chegam ao CD sem cadastro prévio e o
    operador precisa registrar a retirada na hora, sem depender da TI. Pede só
    nome e RE — cargo, departamento, câmara e turno ficam para a TI completar
    depois pela tela de administração.

    Exige apenas login, não a permissão 'admin.colaboradores': quem cadastra é
    o operador de piso. O registro no log de auditoria é a trilha de revisão —
    é por ele que a TI encontra os cadastros feitos às pressas.
    """
    nome = request.form.get('nome', '').strip()
    re   = request.form.get('re', '').strip().upper()

    if not nome or not re:
        return jsonify({'ok': False, 'erro': 'Informe nome e RE.'})
    if not re.isdigit() or len(re) < 4:
        return jsonify({'ok': False, 'erro': 'RE deve ter ao menos 4 dígitos e conter apenas números.'})

    # T4c — empresa efetiva (a impersonada quando o Owner opera dentro de um tenant).
    empresa_alvo = empresa_para_escrita()
    if empresa_alvo is None:
        return jsonify({'ok': False, 'erro': 'Sem contexto de empresa para o cadastro.'})

    # S1/família D — RE único por empresa: checa na empresa em que o colaborador
    # vai nascer. RE de outro tenant não conflita.
    existente = Colaborador.query.filter_by(
        re=re, empresa_id=empresa_alvo
    ).first()
    if existente:
        # Reativar é decisão da TI (tem contexto do desligamento) — aqui só informa.
        if not existente.ativo:
            return jsonify({'ok': False, 'erro': f'RE {re} pertence a {existente.nome}, que está inativo. Procure a TI.'})
        return jsonify({'ok': False, 'erro': f'RE {re} já está cadastrado para {existente.nome}.'})

    colaborador = Colaborador(re=re, nome=nome, empresa_id=empresa_alvo)
    db.session.add(colaborador)
    try:
        db.session.commit()
    except IntegrityError:
        # Dois operadores cadastrando o mesmo RE ao mesmo tempo — a checagem
        # acima não é atômica. O UNIQUE do banco é quem decide; aqui só
        # traduzimos a violação para uma mensagem em vez de estourar um 500.
        db.session.rollback()
        return jsonify({'ok': False, 'erro': f'RE {re} acabou de ser cadastrado por outro operador. Digite o RE novamente.'})

    registrar_log('COLABORADOR_CRIAR_CAMPO', f'Colaborador RE:{re} — {nome} cadastrado em campo pela tela de movimentação.')
    return jsonify({'ok': True, 'id': colaborador.id, 're': colaborador.re, 'nome': colaborador.nome})
