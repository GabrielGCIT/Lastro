"""
Rotas principais do sistema — dashboard, relatórios, perfil e administração.

Este blueprint concentra tudo que não é específico de coletores ou movimentação:
os KPIs da home, o relatório operacional, o gerenciamento
de usuários e as APIs auxiliares usadas pelo JS da tela de movimentação.

Controle de acesso: a maioria das rotas usa @login_required (qualquer usuário
autenticado pode acessar). Rotas sensíveis usam @permissao_required com o código
da permissão esperada — o decorator implementa lógica OR, então passando mais de
um código qualquer deles já libera o acesso.
"""

import csv
import io
import os
from datetime import datetime, date

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, send_from_directory, jsonify, abort
from sqlalchemy import func, case
from werkzeug.security import generate_password_hash, check_password_hash

from app import db, FOTO_FOLDER, CRACHA_FOLDER, ALLOWED_IMG_EXTENSIONS
from app.models import (Coletor, Movimentacao, LogAuditoria,
                        Usuario, Localidade, Grupo, Permissao, NIVEIS_ACESSO,
                        STATUS_FORA_DE_OPERACAO, STATUS_INDISPONIVEIS)
from app.helpers import (login_required, permissao_required, registrar_log,
                         get_filtro_localidade, empresa_visivel, criterio_empresa,
                         empresa_para_escrita, localidade_para_escrita, re_usuario_em_uso,
                         tema_seguro, cor_acento_segura,
                         CORES_ACENTO_SELECIONAVEIS,
                         normalizar_email, email_valido, email_em_uso, problema_na_senha,
                         SENHA_MINIMA)

dashboard_bp = Blueprint('dashboard', __name__)


# ---------------------------------------------------------------------------
# DASHBOARD PRINCIPAL
# ---------------------------------------------------------------------------

@dashboard_bp.route('/')
@login_required
def index():
    """
    Dashboard principal — visão geral da frota e alertas operacionais.

    Os KPIs de status (total, disponíveis, em uso, manutenção) são queries
    independentes e intencionalmente separadas. Unificá-las em uma única query
    com CASE/SUM seria mais eficiente em volume, mas sacrificaria legibilidade
    sem ganho prático no tamanho atual da frota. Se o banco crescer muito,
    revisar com uma query de agregação.
    """
    # P7 — filtrar KPIs pelo contexto ativo do usuário
    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is None:
        q_col = Coletor.query
    elif ids_permitidos:
        q_col = Coletor.query.filter(Coletor.localidade_id.in_(ids_permitidos))
    else:
        q_col = Coletor.query.filter(False)

    total       = q_col.count()
    disponiveis = q_col.filter_by(status='Disponível').count()
    em_uso      = q_col.filter_by(status='Em Uso').count()
    manutencao  = q_col.filter(
        Coletor.status.in_(STATUS_FORA_DE_OPERACAO)
    ).count()
    # Suspenso espera análise da TI, na tela de suspensos — linha própria,
    # destino próprio.
    suspensos_aguardando = q_col.filter(Coletor.status == 'Suspenso').count()

    if ids_permitidos is None:
        ultimas = Movimentacao.query.order_by(Movimentacao.data_saida.desc()).limit(5).all()
    elif ids_permitidos:
        ultimas = (
            Movimentacao.query
            .join(Coletor, Movimentacao.coletor_id == Coletor.id)
            .filter(Coletor.localidade_id.in_(ids_permitidos))
            .order_by(Movimentacao.data_saida.desc()).limit(5).all()
        )
    else:
        ultimas = []

    # P7 — Resumo por localidade para usuários acima de CD (dashboard consolidado)
    nivel = session.get('nivel_acesso', 'CD')
    localidades_resumo = []
    if nivel != 'CD':
        resumo_q = (
            db.session.query(
                Localidade,
                func.count(Coletor.id).label('total'),
                func.sum(case((Coletor.status == 'Disponível', 1), else_=0)).label('disponiveis'),
                func.sum(case((Coletor.status == 'Em Uso', 1), else_=0)).label('em_uso'),
                func.sum(case((Coletor.status.in_(STATUS_FORA_DE_OPERACAO),
                               1), else_=0)).label('manutencao'),
            )
            .outerjoin(Coletor, Coletor.localidade_id == Localidade.id)
            # GROUP BY estrito do SQL Server exige TODAS as colunas selecionadas.
            # Derivar do __table__ em vez de listar à mão: a lista manual quebrou
            # quando o T1 adicionou empresa_id (coluna nova ficava fora do GROUP BY).
            .group_by(*Localidade.__table__.columns)
        )
        if ids_permitidos is not None:
            resumo_q = resumo_q.filter(Localidade.id.in_(ids_permitidos))
        localidades_resumo = resumo_q.order_by(Localidade.sigla).all()

    # H4 — a dívida de complementos no lugar onde o gestor JÁ olha. Limitada
    # O painel mostra as 10 mais recentes, mas o TOTAL conta todas: um bloco que
    # diz "10" quando há 40 itens em falta mente para menos, e mentir para menos
    # num alerta de cobrança é pior do que não ter o alerta.
    from app.complementos import pendencias_abertas
    todas_pendencias = pendencias_abertas(ids_permitidos)
    pendencias_complemento = todas_pendencias[:10]
    total_pendencias = sum(linha.faltando for linha in todas_pendencias)

    return render_template('index.html',
                           pendencias_complemento=pendencias_complemento,
                           total_pendencias=total_pendencias,
                           suspensos_aguardando=suspensos_aguardando,
                           STATUS_INDISPONIVEIS=STATUS_INDISPONIVEIS,
                           total=total, disponiveis=disponiveis,
                           em_uso=em_uso, manutencao=manutencao,
                           movimentacoes=ultimas,
                           localidades_resumo=localidades_resumo)


# ---------------------------------------------------------------------------
# HISTÓRICO
# ---------------------------------------------------------------------------

@dashboard_bp.route('/historico')
@login_required
def historico_completo():
    """
    Lista as últimas 500 movimentações em ordem cronológica decrescente.

    Não há paginação — decisão consciente para manter a tela simples e evitar
    estado de navegação no servidor. O limite de 500 registros cobre semanas de
    operação normal e torna o scroll suficiente. Se o volume crescer a ponto de
    isso ser lento, o próximo passo é adicionar paginação com page/per_page ou
    filtros por período no próprio template.
    """
    ids_permitidos = get_filtro_localidade()
    q = Movimentacao.query.join(Coletor, Movimentacao.coletor_id == Coletor.id)
    if ids_permitidos is None:
        pass  # GLOBAL — vê tudo
    elif ids_permitidos:
        q = q.filter(Coletor.localidade_id.in_(ids_permitidos))
    else:
        q = q.filter(False)  # usuário sem localidade atribuída — nada
    movimentacoes = q.order_by(Movimentacao.data_saida.desc()).limit(500).all()
    return render_template('historico.html', movimentacoes=movimentacoes)


@dashboard_bp.route('/coletor/<int:coletor_id>/historico')
@login_required
def historico_coletor(coletor_id):
    """
    Histórico completo de um coletor específico: movimentações, complementos
    pendentes e as peças vinculadas a ele.
    """
    coletor = db.get_or_404(Coletor, coletor_id)

    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is not None and coletor.localidade_id not in ids_permitidos:
        abort(403)

    movimentacoes = (
        Movimentacao.query
        .filter_by(coletor_id=coletor_id)
        .order_by(Movimentacao.data_saida.desc())
        .all()
    )

    # H3 — o que saiu com este coletor e não voltou. Indexado por movimentação
    # para o template não fazer uma query por linha.
    from app.complementos import pendencias_do_coletor
    pendencias = {}
    for linha in pendencias_do_coletor(coletor_id):
        pendencias.setdefault(linha.movimentacao_id, []).append(linha)

    # B4 — o vínculo com as peças passa a se decidir AQUI, na página do coletor:
    # "o que sai junto com este aqui?" é pergunta sobre o equipamento, não sobre
    # o catálogo. As duas listas vêm do domínio, cada uma numa consulta só.
    from app.complementos import pecas_do_coletor, pecas_para_vincular

    return render_template('historico_coletor.html',
                           coletor=coletor,
                           pendencias_complemento=pendencias,
                           complementos=pecas_do_coletor(coletor),
                           complementos_disponiveis=pecas_para_vincular(coletor),
                           movimentacoes=movimentacoes,
                           hoje=date.today())


# ---------------------------------------------------------------------------
# RELATÓRIO OPERACIONAL
# ---------------------------------------------------------------------------

@dashboard_bp.route('/relatorio/operacional')
@login_required
def relatorio_operacional():
    """
    Relatório operacional da frota — foco em movimentação e uso dos coletores.

    Acessível a qualquer usuário autenticado: supervisores de turno precisam
    consultar quem está com coletor, o que está disponível e o tempo médio de uso
    sem depender de permissão especial — é dado operacional do dia a dia.

    Os rankings (top coletores e top colaboradores) usam GROUP BY + ORDER BY no
    banco. Trazer todas as movimentações e agrupar em Python seria equivalente em
    resultado, mas custoso conforme o histórico cresce.
    """
    now = datetime.now()

    # P7 — filtrar dados pelo contexto ativo do usuário
    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is None:
        _q_col = Coletor.query
        _q_mov = Movimentacao.query
    elif ids_permitidos:
        _q_col = Coletor.query.filter(Coletor.localidade_id.in_(ids_permitidos))
        _q_mov = (
            Movimentacao.query
            .join(Coletor, Movimentacao.coletor_id == Coletor.id)
            .filter(Coletor.localidade_id.in_(ids_permitidos))
        )
    else:
        _q_col = Coletor.query.filter(False)
        _q_mov = Movimentacao.query.filter(False)

    # ── KPIs ──
    total_movimentacoes = _q_mov.count()
    em_uso_count        = _q_mov.filter(Movimentacao.data_retorno == None).count()
    disponiveis         = _q_col.filter_by(status='Disponível').count()

    # Tempo médio calculado em horas (não dias) porque o ciclo de uso de um
    # coletor no CD é intradiário — o supervisor quer saber se está saindo por
    # 2h ou por 8h, não por quantos dias.
    movs_fechadas = _q_mov.filter(Movimentacao.data_retorno.isnot(None)).all()
    tempo_medio_horas = 0
    if movs_fechadas:
        deltas = [
            (m.data_retorno - m.data_saida).total_seconds() / 3600
            for m in movs_fechadas
            if m.data_retorno and m.data_saida
        ]
        if deltas:
            tempo_medio_horas = round(sum(deltas) / len(deltas), 1)

    # ── Top 10 coletores mais movimentados ──
    top_coletores_q = (
        db.session.query(Coletor.id, func.count(Movimentacao.id).label('qtd'))
        .join(Movimentacao, Movimentacao.coletor_id == Coletor.id)
    )
    if ids_permitidos is not None:
        top_coletores_q = top_coletores_q.filter(Coletor.localidade_id.in_(ids_permitidos))
    _col_rows = (
        top_coletores_q
        .group_by(Coletor.id)
        .order_by(func.count(Movimentacao.id).desc())
        .limit(10).all()
    )
    _col_ids = [r[0] for r in _col_rows]
    _col_map = {c.id: c for c in Coletor.query.filter(Coletor.id.in_(_col_ids)).all()} if _col_ids else {}
    top_coletores = [(_col_map[r[0]], r[1]) for r in _col_rows if r[0] in _col_map]

    # ── Top 10 colaboradores que mais retiraram ──
    top_colaboradores_q = (
        db.session.query(
            Movimentacao.re_colaborador,
            func.count(Movimentacao.id).label('qtd')
        )
    )
    if ids_permitidos is not None:
        top_colaboradores_q = (
            top_colaboradores_q
            .join(Coletor, Movimentacao.coletor_id == Coletor.id)
            .filter(Coletor.localidade_id.in_(ids_permitidos))
        )
    top_colaboradores = (
        top_colaboradores_q
        .group_by(Movimentacao.re_colaborador)
        .order_by(func.count(Movimentacao.id).desc())
        .limit(10).all()
    )

    # ── Coletores em uso agora (movimentações abertas) ──
    em_uso_lista = (
        _q_mov
        .filter(Movimentacao.data_retorno == None)
        .order_by(Movimentacao.data_saida.asc())
        .all()
    )

    # ── Distribuição da frota por localidade ──
    por_localidade_q = (
        db.session.query(Localidade, func.count(Coletor.id).label('total'))
        .join(Coletor, Coletor.localidade_id == Localidade.id)
        # GROUP BY derivado do __table__ (SQL Server estrito) — a lista manual
        # quebrava a cada coluna nova no model (empresa_id do T1 ficou de fora).
        .group_by(*Localidade.__table__.columns)
    )
    if ids_permitidos is not None:
        por_localidade_q = por_localidade_q.filter(Localidade.id.in_(ids_permitidos))
    por_localidade = por_localidade_q.order_by(func.count(Coletor.id).desc()).all()

    # ── Distribuição de status da frota ──
    status_dist_q = (
        db.session.query(Coletor.status, func.count(Coletor.id))
        .group_by(Coletor.status)
    )
    if ids_permitidos is not None:
        status_dist_q = status_dist_q.filter(Coletor.localidade_id.in_(ids_permitidos))
    status_dist = dict(status_dist_q.all())

    # ── Categoria de defeito mais frequente ──
    # Coletores com status 'Suspenso' têm categoria 'VIOLACAO_DIRETA', que não
    # tem label de exibição no template — geraria "?" no gráfico. Excluímos
    # suspensos aqui porque não representam defeito técnico reportável.
    top_defeitos_q = (
        db.session.query(
            Coletor.categoria_defeito,
            func.count(Coletor.id).label('qtd')
        )
        .filter(
            Coletor.categoria_defeito.isnot(None),
            Coletor.status != 'Suspenso',
        )
    )
    if ids_permitidos is not None:
        top_defeitos_q = top_defeitos_q.filter(Coletor.localidade_id.in_(ids_permitidos))
    top_defeitos = (
        top_defeitos_q
        .group_by(Coletor.categoria_defeito)
        .order_by(func.count(Coletor.id).desc())
        .all()
    )

    # ── Dados para gráficos ──
    grafico_coletores_labels = [
        c.rotulo[:12]
        for c, _ in top_coletores
    ]
    grafico_coletores_dados  = [int(qtd) for _, qtd in top_coletores]
    grafico_colab_labels     = [re for re, _ in top_colaboradores]
    grafico_colab_dados      = [int(qtd) for _, qtd in top_colaboradores]

    return render_template('relatorio_operacional.html',
                           now=now,
                           total_movimentacoes=total_movimentacoes,
                           em_uso_count=em_uso_count,
                           disponiveis=disponiveis,
                           tempo_medio_horas=tempo_medio_horas,
                           top_coletores=top_coletores,
                           top_colaboradores=top_colaboradores,
                           em_uso_lista=em_uso_lista,
                           por_localidade=por_localidade,
                           status_dist=status_dist,
                           top_defeitos=top_defeitos,
                           grafico_coletores_labels=grafico_coletores_labels,
                           grafico_coletores_dados=grafico_coletores_dados,
                           grafico_colab_labels=grafico_colab_labels,
                           grafico_colab_dados=grafico_colab_dados)


# ---------------------------------------------------------------------------
# PERFIL DO USUÁRIO
# ---------------------------------------------------------------------------

@dashboard_bp.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    """
    Gerenciamento do perfil do usuário autenticado: contato, foto e senha.

    Todas as ações do formulário passam pela mesma rota POST. O campo hidden
    'acao' no form determina o que fazer — em vez de ter URLs separadas como
    /perfil/foto, /perfil/senha, etc. A vantagem é manter o template simples
    com um único <form> por seção e evitar lógica de CSRF duplicada. A
    desvantagem é que o if/elif cresce conforme as ações aumentam — se chegar
    a mais de 5 ou 6 ações, vale refatorar para um dispatcher.

    Fluxo do upload de foto:
    1. Valida a extensão antes de salvar (ALLOWED_IMG_EXTENSIONS)
    2. Salva o arquivo em FOTO_FOLDER com nome padronizado (perfil_{id}.{ext})
    3. Atualiza user.foto_perfil no banco e faz commit
    O arquivo antigo não é removido automaticamente se a extensão mudar —
    o novo sobrescreve apenas se o nome for idêntico, caso contrário ficam dois
    arquivos no disco. Baixo impacto no volume atual, mas vale limpar se necessário.
    """
    user = db.session.get(Usuario, session['user_id'])

    if request.method == 'POST':
        acao = request.form.get('acao')

        # ── Atualiza dados de contato ──
        if acao == 'contato':
            # T2 — e-mail é credencial de login: normalizado, formato conferido
            # e unicidade GLOBAL app-level (padrão numero_serie).
            email = normalizar_email(request.form.get('email'))
            if email and not email_valido(email):
                flash('E-mail em formato inválido.', 'danger')
                return redirect(url_for('dashboard.perfil'))
            if email and email_em_uso(email, excluir_id=user.id):
                flash('Este e-mail já está em uso por outro usuário.', 'danger')
                return redirect(url_for('dashboard.perfil'))
            user.email    = email
            user.telefone = request.form.get('telefone', '').strip() or None
            db.session.commit()
            registrar_log('PERFIL_UPDATE', f'{user.nome} atualizou email/telefone.')
            flash('Dados de contato atualizados.', 'success')

        # ── Upload de foto de perfil ──
        elif acao == 'foto':
            arquivo = request.files.get('foto_perfil')
            if arquivo and arquivo.filename:
                ext = arquivo.filename.rsplit('.', 1)[-1].lower()
                if ext not in ALLOWED_IMG_EXTENSIONS:
                    flash('Formato inválido. Use JPG, PNG ou WEBP.', 'danger')
                    return redirect(url_for('dashboard.perfil'))
                nome_arquivo = f'perfil_{user.id}.{ext}'
                arquivo.save(os.path.join(FOTO_FOLDER, nome_arquivo))
                user.foto_perfil = nome_arquivo
                db.session.commit()
                registrar_log('PERFIL_FOTO', f'{user.nome} atualizou a foto de perfil.')
                flash('Foto de perfil atualizada.', 'success')
            else:
                flash('Nenhum arquivo selecionado.', 'warning')

        # ── Remover foto ──
        elif acao == 'remover_foto':
            if user.foto_perfil:
                path = os.path.join(FOTO_FOLDER, user.foto_perfil)
                if os.path.exists(path):
                    os.remove(path)
                user.foto_perfil = None
                db.session.commit()
                flash('Foto removida.', 'success')

        # ── Preferências visuais (tema + cor de acento) ──
        elif acao == 'preferencias':
            novo_tema = tema_seguro(request.form.get('tema', '').strip())
            if novo_tema:
                user.tema = novo_tema
                session['tema'] = novo_tema

            # cor_acento: '' (ou 'reset') volta ao verde padrão da marca (NULL).
            # Só aceita hex da paleta CURADA — o campo cru é rejeitado, cobrindo
            # tanto contraste (não roda livre) quanto CSS injection (não casa o
            # regex → nem chega aqui). A validação de RENDER é a segunda barreira.
            cor_raw = request.form.get('cor_acento', '').strip()
            if cor_raw in ('', 'reset'):
                user.cor_acento = None
                session['cor_acento'] = None
            elif cor_raw in CORES_ACENTO_SELECIONAVEIS:
                user.cor_acento = cor_raw
                session['cor_acento'] = cor_raw
            else:
                # Hex malformado ou fora da paleta — não grava e avisa.
                db.session.rollback()
                flash('Cor de acento inválida.', 'danger')
                return redirect(url_for('dashboard.perfil'))

            db.session.commit()
            registrar_log('PERFIL_PREFS',
                          f'{user.nome} atualizou tema={user.tema} acento={user.cor_acento}.')
            flash('Preferências de aparência atualizadas.', 'success')

        # ── Alterar senha ──
        elif acao == 'senha':
            senha_atual = request.form.get('senha_atual', '')
            senha_nova  = request.form.get('senha_nova', '')
            senha_conf  = request.form.get('senha_confirmacao', '')
            if not check_password_hash(user.senha_hash, senha_atual):
                flash('Senha atual incorreta.', 'danger')
            elif problema_na_senha(senha_nova, senha_conf):
                flash(problema_na_senha(senha_nova, senha_conf), 'danger')
            else:
                user.senha_hash = generate_password_hash(senha_nova, method='scrypt')
                db.session.commit()
                registrar_log('SENHA_UPDATE', f'{user.nome} alterou a própria senha.')
                flash('Senha alterada com sucesso.', 'success')

        return redirect(url_for('dashboard.perfil'))

    return render_template('perfil.html', user=user,
                           cores_acento=CORES_ACENTO_SELECIONAVEIS)


@dashboard_bp.route('/uploads/perfis/<filename>')
@login_required
def foto_perfil_file(filename):
    """Serve foto de perfil — tenant-aware (T3).

    Resolve o DONO do filename (Usuario.foto_perfil) e só serve se a empresa
    dele for visível para a sessão. Não achou dono ou empresa diverge → 404
    (não 403: não revelar a existência do arquivo de outro tenant).
    """
    dono = Usuario.query.filter_by(foto_perfil=filename).first()
    if not dono or not empresa_visivel(dono.empresa_id):
        abort(404)
    return send_from_directory(FOTO_FOLDER, filename)


# ---------------------------------------------------------------------------
# API — Busca de usuário por RE (para movimentação)
# ---------------------------------------------------------------------------

@dashboard_bp.route('/api/usuario/re/<re_busca>')
@login_required
def api_usuario_por_re(re_busca):
    """
    Lookup de colaborador por RE — chamada pelo JS da tela de movimentação.

    Quando o operador digita o RE na abertura de uma movimentação, o frontend
    faz um fetch nesta rota para exibir nome, grupo e foto em tempo real, antes
    de confirmar a retirada. Isso evita erros de RE digitado errado passando
    despercebidos.

    Retorna 'grupo' (nome do grupo do usuário no sistema RBAC) em vez de
    'permissao' — o campo 'permissao' existia na versão anterior do modelo e
    foi removido na migração para RBAC. O JS do frontend já espera 'grupo'.
    """
    # T3/T4a — lookup escopado por empresa: um tenant não resolve RE de outro.
    # criterio_empresa é fail-closed de verdade: não-owner sem empresa_id na
    # sessão devolve db.false() (zero linhas), não IS NULL — que casaria com os
    # órfãos. Esta rota está sob /api/ e o _sync_permissoes a ignora, mas
    # empresa_id já vem da sessão do login (o cache que has_permissao usa).
    q = Usuario.query.filter_by(re=re_busca).filter(criterio_empresa(Usuario.empresa_id))
    user = q.first()
    if not user:
        return jsonify({'encontrado': False}), 404
    foto_url = url_for('dashboard.foto_perfil_file', filename=user.foto_perfil) if user.foto_perfil else None
    return jsonify({
        'encontrado': True,
        'nome': user.nome,
        'grupo': user.grupo.nome if user.grupo else '—',
        'foto': foto_url,
    })


@dashboard_bp.route('/api/polling/snapshot')
@login_required
def api_polling_snapshot():
    """Status da frota em tempo real — consumido pelos setIntervals de dashboard, operação e inventário."""
    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is not None and not ids_permitidos:
        return jsonify({'total': 0, 'disponiveis': 0, 'em_uso': 0, 'manutencao': 0, 'ultima_mov_ts': None})

    if ids_permitidos is None:
        q_col = Coletor.query
    else:
        q_col = Coletor.query.filter(Coletor.localidade_id.in_(ids_permitidos))

    total       = q_col.count()
    disponiveis = q_col.filter(Coletor.status == 'Disponível').count()
    em_uso      = q_col.filter(Coletor.status == 'Em Uso').count()
    manutencao  = q_col.filter(Coletor.status.in_(STATUS_FORA_DE_OPERACAO)).count()

    if ids_permitidos is None:
        ultima_q = db.session.query(func.max(Movimentacao.data_saida))
    else:
        ultima_q = (db.session.query(func.max(Movimentacao.data_saida))
                    .join(Coletor, Movimentacao.coletor_id == Coletor.id)
                    .filter(Coletor.localidade_id.in_(ids_permitidos)))
    ultima_ts = ultima_q.scalar()

    return jsonify({
        'total':        total,
        'disponiveis':  disponiveis,
        'em_uso':       em_uso,
        'manutencao':   manutencao,
        'ultima_mov_ts': ultima_ts.isoformat() if ultima_ts else None,
    })


@dashboard_bp.route('/uploads/crachas/<filename>')
@login_required
def foto_cracha_file(filename):
    """Serve foto de crachá — tenant-aware (T3), via Usuario.foto_cracha → empresa."""
    dono = Usuario.query.filter_by(foto_cracha=filename).first()
    if not dono or not empresa_visivel(dono.empresa_id):
        abort(404)
    return send_from_directory(CRACHA_FOLDER, filename)


# ---------------------------------------------------------------------------
# CONTEXTO MULTI-TENANCY
# ---------------------------------------------------------------------------

def _contexto_valido(ctx: str) -> bool:
    """True se o contexto tem um formato que este sistema sabe ler.

    Com a hierarquia geográfica fora, existem exatamente dois: todos os CDs, ou
    um CD. Validar o FORMATO aqui não é preciosismo — uma string herdada
    ("PAIS:BR") passaria pela checagem de empresa, seria gravada na sessão, e o
    escopo cairia no fallback fail-closed: a pessoa veria zero coletores sem
    nenhuma mensagem explicando por quê.
    """
    if ctx == 'GLOBAL':
        return True
    if ctx.startswith('CD:'):
        try:
            int(ctx.split(':', 1)[1])
            return True
        except (ValueError, IndexError):
            return False
    return False


def _contexto_na_empresa(ctx: str) -> bool:
    """True se o contexto pedido é compatível com a empresa da sessão (T3).

    Vetor cross-tenant (classe do BUG P8c): um usuário GLOBAL da empresa X
    poderia setar CD:<id de outra empresa> e passar a operar dados alheios. CD é
    a única referência que aponta uma localidade concreta, então é a que precisa
    pertencer à empresa; GLOBAL é global DA empresa, recortado na leitura.
    """
    emp = session.get('empresa_id')
    if not emp:
        return False
    if ctx.startswith('CD:'):
        try:
            loc = db.session.get(Localidade, int(ctx.split(':', 1)[1]))
        except (ValueError, TypeError):
            return False
        return loc is not None and loc.empresa_id == emp
    return True  # GLOBAL — recortado pelo base set da empresa na leitura


@dashboard_bp.route('/contexto/trocar', methods=['POST'])
@login_required
def trocar_contexto():
    """Permite usuário acima de CD escolher o contexto de visualização.

    O contexto é persistido na sessão Flask — não no banco. O usuário pode
    trocar dentro dos limites do seu nivel_acesso sem afetar outros usuários.

    Segurança: o contexto é validado antes de ser salvo — formato primeiro,
    depois a empresa. Sem a checagem de empresa, um usuário GLOBAL da empresa X
    mandaria CD:<id de outra empresa> e passaria a operar dados alheios
    (authorization bypass via form crafting). Tentativa inválida é recusada com
    flash e log de auditoria.
    """
    nivel = session.get('nivel_acesso', 'CD')
    if nivel == 'CD':
        flash('Você não tem permissão para mudar o que está vendo.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))

    novo_ctx = request.form.get('contexto', '').strip()
    if not novo_ctx:
        return redirect(request.referrer or url_for('dashboard.index'))

    if not _contexto_valido(novo_ctx):
        registrar_log(
            'SECURITY_ALERT',
            f'{session.get("re")} tentou trocar contexto para "{novo_ctx}" '
            f'(formato desconhecido, nivel={nivel})',
        )
        flash('Essa opção está fora do que você pode ver.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))

    # T3 — recorte por empresa vale ATÉ para GLOBAL (que é global DA empresa,
    # não do banco). Só o Owner cruza tenants. Um CD de outra empresa aqui é
    # tentativa de acesso cross-tenant — recusa e registra.
    if not _contexto_na_empresa(novo_ctx):
        registrar_log(
            'SECURITY_ALERT',
            f'{session.get("re")} tentou trocar contexto para "{novo_ctx}" '
            f'fora da empresa {session.get("empresa_id")} (cross-tenant).',
        )
        flash('Essa opção está fora do que você pode ver.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))

    session['view_context'] = novo_ctx
    return redirect(request.referrer or url_for('dashboard.index'))


@dashboard_bp.route('/idioma/trocar', methods=['POST'])
@login_required
def trocar_idioma():
    """Troca o idioma ativo do usuário na sessão e persiste no banco.

    Aceita apenas os idiomas suportados ('pt', 'en', 'es') — qualquer outro
    valor é ignorado silenciosamente para não expor erros desnecessários.
    A persistência no banco garante que o idioma seja restaurado na próxima
    sessão via _sync_permissoes.
    """
    novo_idioma = request.form.get('idioma', '').strip()
    if novo_idioma in ('pt', 'en', 'es'):
        session['idioma'] = novo_idioma
        u = db.session.get(Usuario, session.get('user_id'))
        if u:
            u.idioma = novo_idioma
            db.session.commit()
    return redirect(request.referrer or url_for('dashboard.index'))


@dashboard_bp.route('/tema/trocar', methods=['POST'])
@login_required
def trocar_tema():
    """Persiste o tema (light|dark|auto) do usuário — chamado pelo toggle da navbar via fetch.

    Espelha trocar_idioma: valida contra os temas suportados e grava em
    Usuario.tema, mantendo o banco como fonte da verdade (o anti-flash lê essa
    preferência no próximo carregamento). Valor inválido é ignorado.
    Responde 204 (sem corpo) — o front já aplicou a mudança localmente.
    """
    novo_tema = tema_seguro(request.form.get('tema', '').strip())
    if novo_tema:
        session['tema'] = novo_tema
        u = db.session.get(Usuario, session.get('user_id'))
        if u:
            u.tema = novo_tema
            db.session.commit()
    return ('', 204)


# ---------------------------------------------------------------------------
# EXPORTAÇÃO
# ---------------------------------------------------------------------------

@dashboard_bp.route('/exportar')
@login_required
@permissao_required('relatorio.exportar')
def exportar_excel():
    """
    Exporta todo o histórico de movimentações como CSV (separado por ponto-e-vírgula).

    O nome da rota diz 'excel' por clareza para o usuário final, mas o formato
    é CSV com delimitador ';' — compatível com Excel no padrão pt-BR sem precisar
    configurar importação. Não há filtro de período aqui; se o volume crescer,
    considerar exportação filtrada por data.
    """
    si = io.StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(['ID', 'Serial', 'Colaborador', 'Saida', 'Retorno', 'Status Retorno'])

    ids_permitidos = get_filtro_localidade()
    if ids_permitidos is None:
        movs = Movimentacao.query.all()
    elif ids_permitidos:
        movs = (
            Movimentacao.query
            .join(Coletor, Movimentacao.coletor_id == Coletor.id)
            .filter(Coletor.localidade_id.in_(ids_permitidos))
            .all()
        )
    else:
        movs = []

    for m in movs:
        serial  = m.coletor.serial_number if m.coletor else 'Excluido'
        saida   = m.data_saida.strftime('%d/%m/%Y %H:%M') if m.data_saida else '-'
        retorno = m.data_retorno.strftime('%d/%m/%Y %H:%M') if m.data_retorno else 'Em Uso'
        cw.writerow([m.id, serial, m.re_colaborador, saida, retorno, m.status_identificacao_retorno or '-'])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=historico_movimentacoes.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# ---------------------------------------------------------------------------
# ADMINISTRAÇÃO
# ---------------------------------------------------------------------------

@dashboard_bp.route('/grupos', methods=['GET'])
@permissao_required('admin.grupos')
def gerenciar_grupos():
    """O que cada um dos quatro perfis pode fazer — tela SÓ DE CONSULTA.

    Os perfis são fixos (startup.GRUPOS_DEFAULTS) e o seed os impõe a cada boot.
    A tela existe para a TI escolher o perfil certo ao criar um usuário, não
    para redistribuir permissões.
    """
    from app.startup import GRUPOS_DEFAULTS, TODAS_PERMISSOES
    ordem = list(GRUPOS_DEFAULTS)
    grupos = sorted(Grupo.query.filter(criterio_empresa(Grupo.empresa_id)).all(),
                    key=lambda g: ordem.index(g.nome) if g.nome in ordem else len(ordem))
    # A ordem de leitura é a da declaração (operação → consulta → administração),
    # não a alfabética: quem compara perfis começa pelo trabalho do dia a dia.
    ordem_perm = [c for c, _, _ in TODAS_PERMISSOES]
    permissoes = sorted(Permissao.query.all(),
                        key=lambda p: ordem_perm.index(p.codigo) if p.codigo in ordem_perm else len(ordem_perm))

    modulos = {}
    for p in permissoes:
        modulos.setdefault(p.modulo, []).append(p)

    usuarios_por_grupo = {g.id: len(g.usuarios) for g in grupos}
    return render_template('grupos.html', grupos=grupos, modulos=modulos,
                           usuarios_por_grupo=usuarios_por_grupo)


@dashboard_bp.route('/auditoria')
@permissao_required('auditoria.ver')
def auditoria():
    """Log de auditoria com paginação, busca e filtros.

    A query é construída dinamicamente com base nos parâmetros da query string,
    todos opcionais. O resultado é paginado server-side — correto para um log
    que pode acumular milhares de registros ao longo do tempo.

    Segurança: per_page é validado contra uma lista de valores permitidos para
    evitar que o usuário solicite um número arbitrariamente alto de registros
    de uma vez e cause pressure de memória/CPU.
    """
    page         = request.args.get('page', 1, type=int)
    per_page     = request.args.get('per_page', 100, type=int)
    busca        = request.args.get('busca', '').strip()
    filtro_acao  = request.args.get('acao', '').strip()
    data_inicio  = request.args.get('data_inicio', '').strip()
    data_fim     = request.args.get('data_fim', '').strip()

    # Sanitiza per_page — aceita apenas valores da lista permitida
    if per_page not in (50, 100, 200, 300, 400, 500):
        per_page = 100

    # T3/T4a — a empresa vê só o seu log; o Owner vê o de todos os tenants.
    # criterio_empresa é fail-closed: não-owner sem empresa devolve db.false().
    q = LogAuditoria.query.filter(criterio_empresa(LogAuditoria.empresa_id))

    if busca:
        q = q.filter(
            db.or_(
                LogAuditoria.usuario.ilike(f'%{busca}%'),
                LogAuditoria.detalhe.ilike(f'%{busca}%'),
            )
        )

    if filtro_acao:
        q = q.filter(LogAuditoria.acao == filtro_acao)

    if data_inicio:
        try:
            q = q.filter(LogAuditoria.data_hora >= datetime.strptime(data_inicio, '%Y-%m-%d'))
        except ValueError:
            pass  # data inválida — ignora o filtro silenciosamente

    if data_fim:
        try:
            from datetime import timedelta
            dt_fim = datetime.strptime(data_fim, '%Y-%m-%d') + timedelta(days=1)
            q = q.filter(LogAuditoria.data_hora < dt_fim)
        except ValueError:
            pass

    q = q.order_by(LogAuditoria.data_hora.desc())
    paginacao = q.paginate(page=page, per_page=per_page, error_out=False)

    # Tipos de ação distintos para o dropdown de filtro — mesmo recorte por
    # empresa da listagem, para não vazar quais ações outro tenant registrou.
    acoes_q = (db.session.query(LogAuditoria.acao).distinct()
               .filter(criterio_empresa(LogAuditoria.empresa_id)))
    acoes_disponiveis = [r[0] for r in acoes_q.order_by(LogAuditoria.acao).all()]

    return render_template('auditoria.html',
                           logs=paginacao.items,
                           paginacao=paginacao,
                           acoes_disponiveis=acoes_disponiveis,
                           busca=busca,
                           filtro_acao=filtro_acao,
                           data_inicio=data_inicio,
                           data_fim=data_fim,
                           per_page=per_page)


@dashboard_bp.route('/usuarios', methods=['GET', 'POST'])
@permissao_required('admin.usuarios')
def gerenciar_usuarios():
    """
    CRUD de usuários do sistema — exclusivo para quem tem 'admin.usuarios'.

    No POST, o mesmo formulário serve para criação e edição: se 'id' vier
    preenchido, edita o usuário existente; caso contrário, cria um novo.
    Esse padrão evita duplicar a lógica de validação entre dois endpoints.

    Proteções relevantes:

    - O try/except nos int() de grupo_id e localidade_id protege contra inputs
      malformados enviados diretamente via form ou ferramentas externas. Sem isso,
      uma string não-numérica no campo grupo_id causaria um ValueError não tratado.

    - Senha definida aqui é PROVISÓRIA: o dono troca no próximo login (ver
      auth.trocar_senha). Vale para a criação e para a redefinição — é o caminho
      de recuperação de senha do sistema, que não tem servidor de e-mail. A
      exceção é a própria senha de quem está editando.

    A função interna _salvar_foto_cracha evita repetir a lógica de upload tanto
    na criação quanto na edição — vale manter assim enquanto os dois caminhos
    usam exatamente a mesma validação.
    """
    if request.method == 'POST':
        user_id      = request.form.get('id')
        nome         = request.form.get('nome', '').strip()
        re           = request.form.get('re', '').strip()
        senha        = request.form.get('senha')
        grupo_id_raw = request.form.get('grupo_id')
        loc_id_raw     = request.form.get('localidade_id') or None
        camara         = request.form.get('camara') or None
        turno          = request.form.get('turno') or None
        nivel_acesso   = request.form.get('nivel_acesso', 'CD') or 'CD'
        # A coluna é String e aceita qualquer coisa; o escopo é fail-closed e
        # cala. Juntando as duas, um nível fora da lista gravaria sem erro e
        # produziria um usuário que entra e não enxerga nada. Recusa aqui.
        if nivel_acesso not in NIVEIS_ACESSO:
            flash('Nível de acesso inválido.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        if not nome or not re:
            flash('Nome e RE são obrigatórios.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        # Criar exige senha; editar só valida se uma senha nova veio. Sem isto,
        # criar sem senha estourava 500 (generate_password_hash(None)).
        if senha or not user_id:
            problema = problema_na_senha(senha)
            if problema:
                flash(problema, 'danger')
                return redirect(url_for('dashboard.gerenciar_usuarios'))

        if not re.isdigit() or len(re) < 4:
            flash('RE deve conter apenas números e ter pelo menos 4 dígitos.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        email = normalizar_email(request.form.get('email'))
        # Todo perfil entra no sistema, e entra pelo e-mail: usuário sem e-mail
        # é um cadastro que não consegue fazer nada.
        if not email:
            flash('Informe o e-mail — é com ele que a pessoa entra no sistema.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))
        if not email_valido(email):
            flash('E-mail em formato inválido.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))
        try:
            excluir_id = int(user_id) if user_id else None
        except (ValueError, TypeError):
            excluir_id = None
        if email and email_em_uso(email, excluir_id=excluir_id):
            flash('Erro: e-mail já cadastrado para outro usuário.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        # Proteção contra grupo_id não-numérico (input direto via POST, por exemplo).
        try:
            grupo = db.session.get(Grupo, int(grupo_id_raw)) if grupo_id_raw else None
        except (ValueError, TypeError):
            grupo = None
        if not grupo:
            flash('Erro: Grupo inválido.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        try:
            loc_id = int(loc_id_raw) if loc_id_raw else None
        except (ValueError, TypeError):
            loc_id = None
        # S1/família E — a localidade só é persistida no nível CD; ali ela precisa
        # pertencer ao escopo de escrita da sessão (localidade de outro tenant é
        # rejeitada). Nos demais níveis o loc_id é descartado, então não valida
        # para não gerar rejeição falsa por um campo residual do form.
        loc_ok, loc_id = localidade_para_escrita(loc_id)
        if nivel_acesso == 'CD' and not loc_ok:
            flash('Este CD não está entre os que você acessa.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        # 🔴 Nível CD sem CD vira `set()` em `_escopo_geografico`, e `set()`
        # fecha TODOS os módulos: o usuário entra, navega, e toda tela aparece
        # sem uma linha sequer. Como o formulário nasce em "CD" com "— Sem
        # localidade definida —", criar um usuário sem tocar em nada produz
        # exatamente esse usuário cego. `localidade_para_escrita` devolve
        # ok=True para vazio ("ausência legítima"), então a checagem acima não
        # pegava o caso.
        if nivel_acesso == 'CD' and not loc_id:
            flash('Escolha o CD deste usuário. Sem isso ele entra no sistema '
                  'e não enxerga nenhum equipamento.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        def _salvar_foto_cracha(user_obj):
            foto_cracha = request.files.get('foto_cracha')
            if foto_cracha and foto_cracha.filename:
                ext = foto_cracha.filename.rsplit('.', 1)[-1].lower()
                if ext in ALLOWED_IMG_EXTENSIONS:
                    nome_arquivo = f'cracha_{user_obj.id}.{ext}'
                    foto_cracha.save(os.path.join(CRACHA_FOLDER, nome_arquivo))
                    user_obj.foto_cracha = nome_arquivo

        if user_id:
            # S2/família B — usuário de outro tenant (ou id inexistente) é tratado
            # como inexistente; sem o guard, user None estourava 500.
            user = db.session.get(Usuario, user_id)
            if not user or not empresa_visivel(user.empresa_id):
                flash('Usuário não encontrado.', 'danger')
                return redirect(url_for('dashboard.gerenciar_usuarios'))
            user.nome         = nome
            user.re           = re
            user.email        = email
            user.grupo_id     = grupo.id
            user.camara       = camara
            user.turno        = turno
            user.nivel_acesso = nivel_acesso
            # GLOBAL não guarda CD: o alcance é a empresa inteira, e um
            # localidade_id residual confundiria quem lê a ficha depois.
            user.localidade_id = loc_id if nivel_acesso == 'CD' else None
            if senha:
                user.senha_hash = generate_password_hash(senha, method='scrypt')
                # Redefinida pela TI → provisória. A própria senha, não.
                user.senha_provisoria = user.id != session.get('user_id')
                user.login_tentativas    = 0
                user.login_bloqueado_ate = None
            _salvar_foto_cracha(user)
            flash(f'Usuário {nome} atualizado.', 'success')
            registrar_log('USER_UPDATE', f'Usuario {re} alterado por {session.get("nome")} → grupo {grupo.nome}, nível {nivel_acesso}')
        else:
            empresa_alvo = empresa_para_escrita()
            if empresa_alvo is None:
                flash('Sessão sem empresa. Saia e entre de novo.', 'warning')
                return redirect(url_for('dashboard.gerenciar_usuarios'))
            # S1/família D — RE (matrícula) único POR empresa; login global é por
            # e-mail (checado acima). RE homônimo em outro tenant não conflita.
            if re_usuario_em_uso(re, empresa_alvo):
                flash('Erro: RE já cadastrado.', 'danger')
            else:
                novo = Usuario(
                    nome=nome, re=re,
                    email=email,
                    senha_hash=generate_password_hash(senha, method='scrypt'),
                    senha_provisoria=True,
                    grupo_id=grupo.id,
                    localidade_id=loc_id if nivel_acesso == 'CD' else None,
                    nivel_acesso=nivel_acesso,
                    camara=camara,
                    turno=turno,
                    empresa_id=empresa_alvo,
                )
                db.session.add(novo)
                db.session.flush()
                _salvar_foto_cracha(novo)
                flash(f'Usuário {nome} criado.', 'success')
                registrar_log('USER_CREATE', f'Usuario {re} criado por {session.get("nome")} com grupo {grupo.nome}')

        db.session.commit()
        return redirect(url_for('dashboard.gerenciar_usuarios'))

    from sqlalchemy.orm import selectinload
    # S3/família C — leitura escopada por empresa (fail-closed).
    # Grupos e localidades são os dropdowns do form de criar/editar — vazados,
    # deixariam atribuir um usuário a um grupo/localidade de outro tenant.
    usuarios_q  = Usuario.query.options(
        selectinload(Usuario.localidade),
    )
    grupos_q    = Grupo.query
    ids_permitidos = get_filtro_localidade()
    usuarios_q = usuarios_q.filter(criterio_empresa(Usuario.empresa_id))
    grupos_q   = grupos_q.filter(criterio_empresa(Grupo.empresa_id))
    if ids_permitidos is None:
        localidades = Localidade.query.order_by(Localidade.sigla).all()
    elif ids_permitidos:
        localidades = Localidade.query.filter(Localidade.id.in_(ids_permitidos)).order_by(Localidade.sigla).all()
    else:
        localidades = []
    usuarios    = usuarios_q.order_by(Usuario.nome).all()
    grupos      = grupos_q.order_by(Grupo.nome).all()
    from app.startup import GRUPOS_DEFAULTS
    ordem = list(GRUPOS_DEFAULTS)
    grupos = sorted(grupos, key=lambda g: ordem.index(g.nome) if g.nome in ordem else len(ordem))
    return render_template('usuarios.html', usuarios=usuarios, grupos=grupos,
                           localidades=localidades, niveis_acesso=NIVEIS_ACESSO,
                           senha_minima=SENHA_MINIMA)


@dashboard_bp.route('/glossario')
@login_required
def glossario():
    """As palavras que o sistema usa, explicadas (U4).

    🔴 Nem toda palavra dá para trocar. "Coletor", "câmara" e "RE" são o
    vocabulário REAL do CD — traduzi-los deixaria a tela mais estranha, não mais
    clara. O que faltava era um lugar para quem chega novo descobrir o que cada
    um significa NESTE sistema, sem depender de alguém do lado explicando.

    Sem permissão específica: um glossário que só algumas pessoas podem abrir
    falha justamente com quem mais precisa dele.
    """
    termos = [
        ('CD',
         'Centro de distribuição. Cada CD tem seu próprio parque de equipamentos '
         'e seu próprio estoque.',
         'No sistema, aparece pela sigla: GR é Guarulhos.'),
        ('Coletor',
         'O aparelho que o colaborador leva para a pista para bipar os produtos.',
         'É o equipamento que esta plataforma existe para controlar.'),
        ('Patrimônio',
         'O número que identifica o coletor dentro da empresa. Vem sempre com a '
         'sigla do CD na frente.',
         '"GR 1001" é o patrimônio 1001, de Guarulhos.'),
        ('RE',
         'O registro do colaborador — o número dele no RH. É por ele que a '
         'retirada é identificada no balcão.',
         None),
        ('Movimentação',
         'Uma retirada e a devolução que vem depois. É o empréstimo do coletor '
         'para uma pessoa.',
         'Enquanto não voltar, o coletor está "em campo".'),
        ('Câmara',
         'O ambiente onde o coletor vai trabalhar: seco, resfriado ou congelado. '
         'Define que bateria pode sair com ele.',
         'Bateria de ambiente seco não aguenta o congelado.'),
        ('Alcance',
         'O tipo de antena do coletor — curta ou longa distância.',
         None),
        ('Complemento',
         'O que sai junto com o coletor: bateria, headset, suporte.',
         'Alguns são contados; outros têm patrimônio próprio.'),
        ('Em falta',
         'Complemento que saiu com um coletor e não voltou.',
         'Aparece em vermelho no inventário e no painel.'),
        ('Fora do prazo',
         'Complemento que continua em campo depois do prazo de devolução.',
         'O prazo padrão é de um dia, e é configurável por empresa.'),
    ]
    return render_template('glossario.html', glossario=termos)
