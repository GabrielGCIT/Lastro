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
                        Usuario, Localidade, Grupo, Permissao, GrupoDeletado, America,
                        Pais, STATUS_FORA_DE_OPERACAO, STATUS_INDISPONIVEIS)
from app.helpers import (login_required, permissao_required, has_permissao, registrar_log,
                         get_filtro_localidade, empresa_visivel, criterio_empresa,
                         empresa_para_escrita, localidade_para_escrita, re_usuario_em_uso,
                         tema_seguro, cor_acento_segura,
                         CORES_ACENTO_SELECIONAVEIS,
                         normalizar_email, email_valido, email_em_uso)

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

    # Contador de contas criadas via mobile (tela de movimentação) que ainda
    # não passaram pela revisão da TI. Alimenta o alerta no topo do dashboard
    # para que o time de TI não esqueça de validar esses cadastros.
    # S3/família C — contagem por empresa: sem o filtro, o alerta somava contas
    # pendentes de TODOS os tenants (e apontava para uma listagem já escopada).
    usuarios_campo_q = (Usuario.query.filter_by(criado_em_campo=True)
                        .filter(criterio_empresa(Usuario.empresa_id)))
    usuarios_campo = usuarios_campo_q.count()

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
                           usuarios_campo=usuarios_campo,
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
            elif len(senha_nova) < 6:
                flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
            elif senha_nova != senha_conf:
                flash('As senhas não coincidem.', 'danger')
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
    # empresa_id/is_owner já vêm da sessão do login (o cache que has_permissao usa).
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

def _contexto_dentro_do_escopo(ctx: str, nivel: str) -> bool:
    """Verifica se o contexto solicitado está dentro do escopo autorizado do usuário.

    Chamada apenas para níveis AMERICA e PAIS — GLOBAL passa sem verificação.
    Retorna False para contextos com formato inválido ou que referenciam entidades
    fora do escopo do usuário.

    Separada como função privada para manter trocar_contexto legível e para
    facilitar testes unitários futuros sem precisar de request context completo.
    """
    america_id = session.get('america_id')
    pais_id    = session.get('pais_id')

    if nivel == 'PAIS':
        if ctx.startswith('PAIS:'):
            sigla = ctx.split(':', 1)[1]
            # Verifica que a sigla pertence exatamente ao país vinculado ao usuário
            return Pais.query.filter_by(sigla=sigla, id=pais_id).first() is not None

        if ctx.startswith('CD:'):
            try:
                loc = db.session.get(Localidade, int(ctx.split(':', 1)[1]))
                return loc is not None and loc.pais_id == pais_id
            except (ValueError, TypeError):
                return False

        return False  # PAIS não pode setar AMERICA: nem GLOBAL

    if nivel == 'AMERICA':
        if ctx.startswith('AMERICA:'):
            sigla = ctx.split(':', 1)[1]
            return America.query.filter_by(sigla=sigla, id=america_id).first() is not None

        if ctx.startswith('PAIS:'):
            sigla = ctx.split(':', 1)[1]
            pais  = Pais.query.filter_by(sigla=sigla).first()
            return pais is not None and pais.america_id == america_id

        if ctx.startswith('CD:'):
            try:
                loc  = db.session.get(Localidade, int(ctx.split(':', 1)[1]))
                if loc is None or loc.pais_id is None:
                    return False
                pais = db.session.get(Pais, loc.pais_id)
                return pais is not None and pais.america_id == america_id
            except (ValueError, TypeError):
                return False

        return False  # AMERICA não pode setar GLOBAL

    return False


def _contexto_na_empresa(ctx: str) -> bool:
    """True se o contexto pedido é compatível com a empresa da sessão (T3).

    Vetor cross-tenant (classe do BUG P8c): um usuário GLOBAL da empresa X
    poderia setar CD:<id de outra empresa> e passar a operar dados alheios. A
    referência CD é a única que aponta uma localidade concreta, então é a que
    precisa pertencer à empresa. PAIS:/AMERICA: são referências geográficas
    COMPARTILHADAS entre empresas — a interseção com o base set da empresa no
    get_filtro_localidade já garante o isolamento na leitura, então passam.
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
    return True  # PAIS:/AMERICA:/GLOBAL — isolados via base set na leitura


@dashboard_bp.route('/contexto/trocar', methods=['POST'])
@login_required
def trocar_contexto():
    """Permite usuário acima de CD escolher o contexto de visualização.

    O contexto é persistido na sessão Flask — não no banco. O usuário pode
    trocar dentro dos limites do seu nivel_acesso sem afetar outros usuários.

    Segurança: o contexto é validado contra o escopo autorizado antes de ser
    salvo. Sem isso, um usuário PAIS poderia enviar POST com contexto=PAIS:CR
    e passar a ver dados de outro país (authorization bypass via form crafting).
    GLOBAL não tem restrição de escopo — pode ver qualquer contexto.
    Tentativas inválidas são rejeitadas com flash + log de auditoria.
    """
    nivel = session.get('nivel_acesso', 'CD')
    if nivel == 'CD':
        flash('Você não tem permissão para mudar o que está vendo.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))

    novo_ctx = request.form.get('contexto', '').strip()
    if not novo_ctx:
        return redirect(request.referrer or url_for('dashboard.index'))

    # GLOBAL pode setar qualquer contexto GEOGRÁFICO — sem restrição de escopo.
    # Para AMERICA e PAIS, valida que o contexto pertence ao escopo do usuário.
    if nivel != 'GLOBAL' and not _contexto_dentro_do_escopo(novo_ctx, nivel):
        registrar_log(
            'SECURITY_ALERT',
            f'{session.get("re")} tentou trocar contexto para "{novo_ctx}" '
            f'(nivel={nivel}, america_id={session.get("america_id")}, '
            f'pais_id={session.get("pais_id")})',
        )
        flash('Essa opção está fora do que você pode ver.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))

    # T3 — recorte por empresa vale ATÉ para GLOBAL (que é global DA empresa,
    # não do banco). Só o Owner cruza tenants. Um CD de outra empresa aqui é
    # tentativa de acesso cross-tenant — recusa e registra.
    if not session.get('is_owner') and not _contexto_na_empresa(novo_ctx):
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
    """
    Tela de gestão de grupos e permissões — exclusivo para TI_MASTER ('admin.grupos').

    Exibe todos os grupos com suas permissões atuais agrupadas por módulo.
    Grupos protegidos (TI_MASTER) são somente leitura. Para os demais, o TI_MASTER
    pode editar as permissões via modal com checkboxes.

    As permissões são agrupadas por módulo em Python (não no template) para
    manter o template limpo. O dict modulos preserva a ordem de inserção do
    Python 3.7+ — na ordem em que os módulos aparecem nas permissões.
    """
    # S3/família C + T4a — leitura escopada por empresa (fail-closed); Owner vê todos.
    grupos = (Grupo.query.filter(criterio_empresa(Grupo.empresa_id))
              .order_by(Grupo.id).all())
    permissoes = Permissao.query.order_by(Permissao.modulo, Permissao.codigo).all()

    modulos = {}
    for p in permissoes:
        modulos.setdefault(p.modulo, []).append(p)

    usuarios_por_grupo = {g.id: len(g.usuarios) for g in grupos}
    # T4a — os deletados também são por empresa: sem o escopo, o tenant veria
    # (e restauraria) grupos que outro tenant excluiu.
    grupos_deletados = (GrupoDeletado.query
                        .filter(criterio_empresa(GrupoDeletado.empresa_id))
                        .order_by(GrupoDeletado.data_delecao.desc()).all())

    return render_template(
        'grupos.html',
        grupos=grupos,
        modulos=modulos,
        usuarios_por_grupo=usuarios_por_grupo,
        grupos_deletados=grupos_deletados,
    )


@dashboard_bp.route('/grupos/<nome>/restaurar', methods=['POST'])
@permissao_required('admin.grupos')
def restaurar_grupo(nome):
    """
    Restaura um grupo padrão que foi explicitamente deletado via UI.

    Fluxo:
      1. Valida que é um grupo padrão (existe em GRUPOS_DEFAULTS)
      2. Valida que está registrado em GrupoDeletado
      3. Recria o Grupo com permissões e descrição padrão do GRUPOS_DEFAULTS
      4. Remove o registro de GrupoDeletado (seed pode ignorá-lo no próximo boot)

    Permissões restauradas vêm sempre do GRUPOS_DEFAULTS — não há backup das
    permissões anteriores à exclusão. Se o grupo havia sido editado via UI
    antes de ser deletado, essas customizações são perdidas.

    O caso de estado inconsistente (grupo já existe no banco mas ainda está em
    GrupoDeletado) é tratado graciosamente: apenas limpa o registro de deletado.
    """
    from app.startup import GRUPOS_DEFAULTS

    if nome not in GRUPOS_DEFAULTS:
        flash(f'"{nome}" não é um grupo padrão e não pode ser restaurado.', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    # S1/família A — o grupo restaurado nasce carimbado com o tenant da sessão,
    # como em criar_grupo. Sem o carimbo ele nascia órfão: invisível na listagem
    # escopada (S3) até o backfill do próximo boot o mandar para a MB — que pode
    # nem ser a empresa de quem restaurou.
    empresa_id = empresa_para_escrita()
    if empresa_id is None:
        flash('Escolha uma empresa no topo da tela para restaurar um grupo.', 'warning')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    # T4a — o registro de deleção é por (empresa_id, nome); a PK deixou de ser
    # o nome global. Busca o da empresa da sessão.
    registro = GrupoDeletado.query.filter_by(empresa_id=empresa_id, nome=nome).first()
    if not registro:
        flash(f'O grupo {nome.replace("_", " ")} não está na lista de grupos desativados.', 'warning')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    # Estado inconsistente: grupo já existe mas o registro de deletado não foi limpo.
    # Resolve silenciosamente para não travar a interface. O check é por empresa
    # (uq_grupos_empresa_nome) — um homônimo de outro tenant não conta como "já existe".
    if Grupo.query.filter_by(empresa_id=empresa_id, nome=nome).first():
        db.session.delete(registro)
        db.session.commit()
        flash(f'Grupo {nome.replace("_", " ")} já existe. Registro de desativação removido.', 'info')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    cfg = GRUPOS_DEFAULTS[nome]
    codigos = cfg.get('permissoes', [])
    permissoes = Permissao.query.filter(Permissao.codigo.in_(codigos)).all() if codigos else []

    novo = Grupo(nome=nome, descricao=cfg['descricao'], protegido=cfg['protegido'],
                 empresa_id=empresa_id)
    novo.permissoes = permissoes
    db.session.add(novo)
    db.session.delete(registro)

    registrar_log(
        'GRUPO_RESTAURADO',
        f'Grupo "{nome}" restaurado por {session.get("nome")} '
        f'com {len(permissoes)} permissão(ões) padrão.'
    )
    db.session.commit()

    flash(f'Grupo {nome.replace("_", " ")} restaurado com as permissões padrão.', 'success')
    return redirect(url_for('dashboard.gerenciar_grupos'))


@dashboard_bp.route('/grupos/criar', methods=['POST'])
@permissao_required('admin.grupos')
def criar_grupo():
    """
    Cria um novo grupo com nome, descrição e permissões iniciais.

    O nome é normalizado para maiúsculas com underscores — padrão de todos os
    grupos do sistema. A validação rejeita nomes com caracteres especiais para
    evitar problemas no seed e na UI.
    """
    nome_raw  = request.form.get('nome', '').strip().upper().replace(' ', '_')
    descricao = request.form.get('descricao', '').strip() or None

    if not nome_raw:
        flash('Nome do grupo é obrigatório.', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    if not all(c.isalpha() or c.isdigit() or c == '_' for c in nome_raw):
        flash('Nome deve conter apenas letras, números e underscore.', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    if len(nome_raw) > 30:
        flash('Nome deve ter no máximo 30 caracteres.', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    # S1/família A — o grupo nasce carimbado com o tenant da sessão (cada empresa
    # tem seu próprio TI_MASTER/GERENTE); sem tenant, a criação é bloqueada.
    empresa_id = empresa_para_escrita()
    if empresa_id is None:
        flash('Escolha uma empresa no topo da tela para criar um grupo.', 'warning')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    # S1/família D — nome único POR empresa (constraint uq_grupos_empresa_nome).
    if Grupo.query.filter_by(empresa_id=empresa_id, nome=nome_raw).first():
        flash(f'Já existe um grupo com o nome "{nome_raw}".', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    ids_raw      = request.form.getlist('permissao_ids')
    ids_validos  = [int(i) for i in ids_raw if i.isdigit()]
    permissoes   = Permissao.query.filter(Permissao.id.in_(ids_validos)).all() if ids_validos else []

    novo = Grupo(nome=nome_raw, descricao=descricao, protegido=False, empresa_id=empresa_id)
    novo.permissoes = permissoes
    db.session.add(novo)

    registrar_log(
        'GRUPO_CREATE',
        f'Grupo "{nome_raw}" criado por {session.get("nome")} com {len(permissoes)} permissão(ões).'
    )
    db.session.commit()

    flash(f'Grupo {nome_raw.replace("_", " ")} criado com sucesso.', 'success')
    return redirect(url_for('dashboard.gerenciar_grupos'))


@dashboard_bp.route('/grupos/<int:grupo_id>/excluir', methods=['POST'])
@permissao_required('admin.grupos')
def excluir_grupo(grupo_id):
    """
    Exclui um grupo sem usuários vinculados.

    Grupos protegidos e grupos com usuários ativos são bloqueados — sem
    exceção, mesmo via POST manual. Um grupo com usuários que fosse excluído
    deixaria esses usuários sem grupo_id, quebrando o acesso ao sistema.
    """
    # S2/família B — grupo de outro tenant é tratado como inexistente.
    grupo = db.session.get(Grupo, grupo_id)
    if not grupo or not empresa_visivel(grupo.empresa_id):
        flash('Grupo não encontrado.', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    if grupo.protegido:
        flash('Grupos protegidos não podem ser excluídos.', 'danger')
        registrar_log('SECURITY_ALERT',
                      f'{session.get("re")} tentou excluir o grupo protegido "{grupo.nome}".')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    if grupo.usuarios:
        flash(
            f'O grupo {grupo.nome} possui {len(grupo.usuarios)} usuário(s) vinculado(s). '
            'Mova ou remova esses usuários antes de excluir o grupo.',
            'danger'
        )
        return redirect(url_for('dashboard.gerenciar_grupos'))

    nome = grupo.nome
    db.session.delete(grupo)

    # Se for um grupo padrão do sistema, registra na lista de deletados para que
    # o seed de boot não o recrie. Grupos criados manualmente via UI não precisam
    # desse registro — o seed nunca tentaria recriá-los de qualquer forma.
    from app.startup import GRUPOS_DEFAULTS
    if nome in GRUPOS_DEFAULTS:
        # T4a — deleção por empresa: registra o par (empresa, nome). O dedup
        # também é por empresa (a PK não é mais o nome global), senão o registro
        # de um tenant bloquearia o de outro.
        ja_registrado = (GrupoDeletado.query
                         .filter_by(empresa_id=grupo.empresa_id, nome=nome).first())
        if not ja_registrado:
            db.session.add(GrupoDeletado(
                nome=nome,
                empresa_id=grupo.empresa_id,
                deletado_por=session.get('nome'),
            ))

    registrar_log('GRUPO_DELETE', f'Grupo "{nome}" excluído por {session.get("nome")}.')
    db.session.commit()

    flash(f'Grupo {nome.replace("_", " ")} excluído com sucesso.', 'success')
    return redirect(url_for('dashboard.gerenciar_grupos'))


@dashboard_bp.route('/grupos/<int:grupo_id>/permissoes', methods=['POST'])
@permissao_required('admin.grupos')
def salvar_permissoes_grupo(grupo_id):
    """
    Atualiza as permissões de um grupo não-protegido.

    Recebe uma lista de IDs de permissão via checkbox (permissao_ids[]).
    Se nenhum checkbox marcado, a lista fica vazia — o grupo perde todas as
    permissões, o que é intencional (ex: grupo temporário sem acesso).

    O log de auditoria registra exatamente o que mudou (adicionadas e removidas)
    para rastreabilidade. A verificação de protegido no backend garante que
    um POST manual não consiga alterar TI_MASTER mesmo sem a UI.
    """
    # S2/família B — grupo de outro tenant é tratado como inexistente.
    grupo = db.session.get(Grupo, grupo_id)
    if not grupo or not empresa_visivel(grupo.empresa_id):
        flash('Grupo não encontrado.', 'danger')
        return redirect(url_for('dashboard.gerenciar_grupos'))

    if grupo.protegido:
        flash('Este grupo é protegido e não pode ser editado pela interface.', 'danger')
        registrar_log(
            'SECURITY_ALERT',
            f'{session.get("re")} tentou editar permissões do grupo protegido {grupo.nome}.'
        )
        return redirect(url_for('dashboard.gerenciar_grupos'))

    ids_raw = request.form.getlist('permissao_ids')
    ids_validos = [int(i) for i in ids_raw if i.isdigit()]
    novas_permissoes = Permissao.query.filter(Permissao.id.in_(ids_validos)).all() if ids_validos else []

    codigos_antes = {p.codigo for p in grupo.permissoes}
    codigos_depois = {p.codigo for p in novas_permissoes}
    adicionadas = codigos_depois - codigos_antes
    removidas   = codigos_antes - codigos_depois

    grupo.permissoes = novas_permissoes

    registrar_log(
        'GRUPO_PERM_UPDATE',
        f'Permissões de "{grupo.nome}" atualizadas por {session.get("nome")}. '
        f'Adicionadas: {sorted(adicionadas) or "nenhuma"}. '
        f'Removidas: {sorted(removidas) or "nenhuma"}.'
    )
    db.session.commit()

    flash(f'Permissões do grupo {grupo.nome.replace("_", " ")} atualizadas com sucesso.', 'success')
    return redirect(url_for('dashboard.gerenciar_grupos'))


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

    - A checagem de TI_MASTER é uma camada extra de segurança no backend: mesmo
      que o frontend esconda a opção para quem não tem 'admin.grupos', um usuário
      mal-intencionado poderia montar o POST manualmente. O log de SECURITY_ALERT
      registra tentativas para auditoria posterior.

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
        america_id_raw = request.form.get('america_id') or None
        pais_id_raw    = request.form.get('pais_id_scope') or None
        try:
            scope_america_id = int(america_id_raw) if america_id_raw else None
        except (ValueError, TypeError):
            scope_america_id = None
        try:
            scope_pais_id = int(pais_id_raw) if pais_id_raw else None
        except (ValueError, TypeError):
            scope_pais_id = None

        if not nome or not re:
            flash('Nome e RE são obrigatórios.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        if not re.isdigit() or len(re) < 4:
            flash('RE deve conter apenas números e ter pelo menos 4 dígitos.', 'danger')
            return redirect(url_for('dashboard.gerenciar_usuarios'))

        # T2 — e-mail é a credencial de login. Opcional aqui de propósito:
        # OPERADOR não faz login e fica sem e-mail; para quem loga, é este
        # campo que o admin preenche na transição RE → e-mail.
        email = normalizar_email(request.form.get('email'))
        if email and not email_valido(email):
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

        # TI não pode atribuir/editar grupo TI_MASTER — só TI_MASTER pode
        if grupo.nome == 'TI_MASTER' and not has_permissao('admin.grupos'):
            flash('Apenas TI Master pode atribuir o grupo TI Master.', 'danger')
            registrar_log('SECURITY_ALERT', f'{session.get("re")} tentou atribuir TI_MASTER a um usuário.')
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

        # 🔴 Os três níveis geográficos viram `set()` em `_escopo_geografico`
        # quando o campo de escopo fica vazio, e `set()` fecha TODOS os módulos:
        # o usuário entra, navega, e toda tela aparece sem uma linha sequer.
        # Como o formulário nasce em "CD" com "— Sem localidade definida —",
        # criar um usuário sem tocar em nada produz exatamente esse usuário cego.
        # `localidade_para_escrita` devolve ok=True para vazio ("ausência
        # legítima"), então a checagem acima não pegava o caso.
        exigencia = {
            'CD':      (loc_id,           'Escolha o CD deste usuário.'),
            'PAIS':    (scope_pais_id,    'Escolha o país deste usuário.'),
            'AMERICA': (scope_america_id, 'Escolha a região deste usuário.'),
        }.get(nivel_acesso)
        if exigencia and not exigencia[0]:
            aviso = (exigencia[1] + ' Sem isso ele entra no sistema e não '
                     'enxerga nenhum equipamento.')
            flash(aviso, 'danger')
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
            if nivel_acesso == 'CD':
                user.localidade_id = loc_id
                user.pais_id = None
                user.america_id = None
            elif nivel_acesso == 'PAIS':
                user.localidade_id = None
                user.pais_id = scope_pais_id
                user.america_id = None
            elif nivel_acesso == 'AMERICA':
                user.localidade_id = None
                user.pais_id = None
                user.america_id = scope_america_id
            else:  # GLOBAL
                user.localidade_id = None
                user.pais_id = None
                user.america_id = None
            if senha:
                user.senha_hash = generate_password_hash(senha, method='scrypt')
            _salvar_foto_cracha(user)
            flash(f'Usuário {nome} atualizado.', 'success')
            registrar_log('USER_UPDATE', f'Usuario {re} alterado por {session.get("nome")} → grupo {grupo.nome}, nível {nivel_acesso}')
        else:
            # T4c — a empresa do novo usuário é a EFETIVA da sessão (a impersonada,
            # quando o Owner entra num tenant), não a session['empresa_id'] crua, que
            # o _sync_permissoes fixa na empresa do próprio Owner a cada request. Sem
            # isso, o Owner criando dentro de um cliente gravava o usuário na MB —
            # espelha o que grupos/fornecedores já faziam com empresa_para_escrita().
            empresa_alvo = empresa_para_escrita()
            if empresa_alvo is None:
                flash('Escolha uma empresa no topo da tela para criar um usuário.', 'warning')
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
                    grupo_id=grupo.id,
                    localidade_id=loc_id if nivel_acesso == 'CD' else None,
                    pais_id=scope_pais_id if nivel_acesso == 'PAIS' else None,
                    america_id=scope_america_id if nivel_acesso == 'AMERICA' else None,
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
    # S3/família C — leitura escopada por empresa (fail-closed); Owner vê todos.
    # Grupos e localidades são os dropdowns do form de criar/editar — vazados,
    # deixariam atribuir um usuário a um grupo/localidade de outro tenant.
    usuarios_q  = Usuario.query.options(
        selectinload(Usuario.localidade),
        selectinload(Usuario.pais),
        selectinload(Usuario.america),
    )
    grupos_q    = Grupo.query
    ids_permitidos = get_filtro_localidade()
    if not session.get('is_owner'):
        empresa_id = session.get('empresa_id')
        usuarios_q = usuarios_q.filter(Usuario.empresa_id == empresa_id)
        grupos_q   = grupos_q.filter(Grupo.empresa_id == empresa_id)
    if ids_permitidos is None:
        localidades = Localidade.query.order_by(Localidade.sigla).all()
    elif ids_permitidos:
        localidades = Localidade.query.filter(Localidade.id.in_(ids_permitidos)).order_by(Localidade.sigla).all()
    else:
        localidades = []
    usuarios    = usuarios_q.order_by(Usuario.nome).all()
    grupos      = grupos_q.order_by(Grupo.nome).all()
    americas    = America.query.order_by(America.sigla).all()
    paises      = Pais.query.join(America, Pais.america_id == America.id).order_by(America.sigla, Pais.nome).all()
    return render_template('usuarios.html', usuarios=usuarios, grupos=grupos, localidades=localidades,
                           americas=americas, paises=paises)


@dashboard_bp.route('/usuarios/<int:user_id>/revisar', methods=['POST'])
@permissao_required('admin.revisar_campo')
def revisar_usuario(user_id):
    """
    Marca um usuário criado em campo como revisado pela TI.

    Simplesmente remove o flag criado_em_campo — isso indica que alguém da TI
    verificou os dados do cadastro (nome, RE, foto do crachá) e confirmou que
    está correto. O alerta no dashboard some assim que todos os pendentes forem
    revisados. Não há alteração de senha, grupo ou qualquer outro campo aqui —
    a revisão é apenas uma confirmação de que o cadastro foi validado.

    Exige permissão 'admin.revisar_campo' para separar essa responsabilidade
    de 'admin.usuarios' — é possível dar ao supervisor de TI permissão de revisar
    sem dar acesso total ao CRUD de usuários.
    """
    # S2/família B — usuário de outro tenant é tratado como inexistente.
    user = db.session.get(Usuario, user_id)
    if not user or not empresa_visivel(user.empresa_id):
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('dashboard.gerenciar_usuarios'))
    user.criado_em_campo = False
    db.session.commit()
    registrar_log('USER_REVISADO', f'Conta {user.re} ({user.nome}) marcada como revisada por {session.get("nome")}.')
    flash(f'Conta de {user.nome} marcada como revisada.', 'success')
    return redirect(url_for('dashboard.gerenciar_usuarios'))


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
