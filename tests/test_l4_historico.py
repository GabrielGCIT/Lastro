"""
A tela de Histórico: quem pegou, em letras, e sem mil queries.

🔴 Dois problemas na mesma tela, achados percorrendo a L4:

1. A coluna "Colaborador" imprimia `m.re_colaborador` — o RE cru. O dashboard,
   na mesma base, já mostrava o nome. O histórico é justamente onde alguém vai
   perguntar "quem pegou esse coletor naquele dia", e um número de matrícula não
   responde sem uma segunda consulta.

2. O template lê `m.coletor.rotulo` em toda linha, e a rota traz até 500
   movimentações sem eager load: 500 queries lazy só para os coletores. Mostrar
   o nome do colaborador dobraria isso para mil. O N+1 não quebra nada — a tela
   abre, só devagar, e devagar não aparece em teste nenhum que não conte query.

O teste que dá nome ao arquivo é `test_historico_nao_faz_n_mais_1`: ele conta as
queries de verdade, porque é o único jeito de um N+1 ficar vermelho.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import db
from app.models import Colaborador, Coletor, Empresa, Movimentacao


@pytest.fixture()
def movimentacoes(app, loc):
    """10 movimentações encerradas, cada uma de um colaborador diferente."""
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    agora = datetime.now()
    for i in range(10):
        c = Coletor(serial_number=f'SNH{i:03d}', numero_patrimonio=f'9{i:02d}',
                    localidade_id=loc.id, status='Disponível')
        col = Colaborador(re=f'7{i:04d}', nome=f'Operador {i}', empresa_id=mb.id)
        db.session.add_all([c, col])
        db.session.flush()
        db.session.add(Movimentacao(
            coletor_id=c.id, colaborador_id=col.id, re_colaborador=col.re,
            data_saida=agora - timedelta(days=i + 1),
            data_retorno=agora - timedelta(days=i + 1) + timedelta(hours=4)))
    db.session.commit()


def test_a_tela_diz_o_nome_de_quem_pegou(admin_client, movimentacoes):
    """🔴 O RE sozinho não responde "quem"."""
    html = admin_client.get('/historico').get_data(as_text=True)

    assert 'Operador 3' in html, 'a tela não mostra o nome do colaborador'
    assert 'RE 70003' in html, 'o RE sumiu — ele ainda identifica a pessoa'


def test_movimentacao_antiga_sem_vinculo_ainda_aparece(admin_client, movimentacoes, loc):
    """Registros anteriores ao vínculo só têm o RE em texto livre.

    Sem este teste, trocar a coluna por `m.colaborador.nome` seco deixaria a
    célula VAZIA para esses casos — some a informação e ninguém percebe, porque
    a linha continua lá.
    """
    c = Coletor(serial_number='SN-ANTIGO', numero_patrimonio='888',
                localidade_id=loc.id, status='Disponível')
    db.session.add(c)
    db.session.flush()
    db.session.add(Movimentacao(coletor_id=c.id, re_colaborador='99999',
                                colaborador_id=None,
                                data_saida=datetime.now() - timedelta(hours=2),
                                data_retorno=datetime.now()))
    db.session.commit()

    html = admin_client.get('/historico').get_data(as_text=True)

    assert '99999' in html, 'movimentação sem vínculo perdeu o identificador'


def test_historico_nao_faz_n_mais_1(admin_client, movimentacoes):
    """🔴 O nome do arquivo: contar query é o único jeito de ver um N+1.

    Com eager load são poucas queries e o número NÃO cresce com as linhas. Sem
    ele, são 2 por movimentação (coletor + colaborador).
    """
    queries = []

    def contar(conn, cursor, stmt, params, context, executemany):
        queries.append(stmt)

    event.listen(db.engine, 'before_cursor_execute', contar)
    try:
        resposta = admin_client.get('/historico')
    finally:
        event.remove(db.engine, 'before_cursor_execute', contar)

    assert resposta.status_code == 200
    # 10 movimentações; com N+1 seriam 20+ SELECTs só de coletor/colaborador.
    # O teto é folgado de propósito — o que importa é não escalar com as linhas.
    assert len(queries) < 15, (
        f'{len(queries)} queries para 10 movimentações — N+1 de volta?\n'
        + '\n'.join(q[:90] for q in queries[:12]))


# ---------------------------------------------------------------------------
# PAINEL OPERACIONAL — o gráfico que a gerência olha
# ---------------------------------------------------------------------------

def test_grafico_de_colaboradores_usa_nome_e_nao_matricula(admin_client, movimentacoes):
    """🔴 O eixo do gráfico "Top Colaboradores" imprimia a MATRÍCULA.

    Num gráfico feito para decidir, "70003" não diz nada: quem lê teria de abrir
    a lista de colaboradores e traduzir barra por barra. O agrupamento continua
    pelo RE — é o identificador estável, e movimentação antiga só tem ele —, mas
    o rótulo passa a ser o nome.
    """
    html = admin_client.get('/relatorio/operacional').get_data(as_text=True)

    assert 'Operador 3' in html, 'o gráfico não mostra o nome'


def test_grafico_cai_no_RE_quando_o_colaborador_sumiu(admin_client, loc):
    """Movimentação cujo RE não está mais na tabela de colaboradores.

    Sem o fallback, o rótulo viria vazio e a barra ficaria anônima — pior que a
    matrícula, porque some a única pista de quem foi.
    """
    c = Coletor(serial_number='SN-ORFA', numero_patrimonio='777',
                localidade_id=loc.id, status='Disponível')
    db.session.add(c)
    db.session.flush()
    db.session.add(Movimentacao(coletor_id=c.id, re_colaborador='55555',
                                colaborador_id=None,
                                data_saida=datetime.now() - timedelta(hours=3),
                                data_retorno=datetime.now()))
    db.session.commit()

    html = admin_client.get('/relatorio/operacional').get_data(as_text=True)

    assert '55555' in html, 'barra sem rótulo: sumiu a única pista de quem foi'


def test_o_grafico_nao_pega_o_nome_do_colaborador_de_outra_empresa(
        app, cliente_logado, duas_empresas):
    """🔴 RE igual em duas empresas: o nome tem de vir da empresa certa.

    O RE é único POR EMPRESA, não globalmente — duas empresas podem ter o
    colaborador 20101. A agregação do gráfico é escopada por localidade, então
    só as movimentações da empresa certa entram; mas o rótulo vem de uma
    consulta SEPARADA, por RE. Sem `criterio_empresa` nela, `Colaborador.re
    .in_(['20101'])` traz os DOIS e o dicionário fica com o que vier por último:
    a barra da MB podendo exibir o nome de alguém da Acme.

    A primeira versão deste teste punha a movimentação na localidade da OUTRA
    empresa — e passava com a guarda sabotada, porque aquele RE nunca chegava à
    busca de nomes. O que faz a guarda morder é o RE COLIDIR.
    """
    du = duas_empresas
    RE = 'COLIDE'
    daqui = Colaborador(re=RE, nome='Nome Da MB', empresa_id=du.mb.id)
    dali  = Colaborador(re=RE, nome='Nome Da Acme', empresa_id=du.acme.id)
    c1 = Coletor(serial_number='SN-COL1', numero_patrimonio='C01',
                 localidade_id=du.locA.id, status='Disponível')
    db.session.add_all([daqui, dali, c1])
    db.session.flush()
    db.session.add(Movimentacao(coletor_id=c1.id, re_colaborador=RE,
                                colaborador_id=daqui.id,
                                data_saida=datetime.now() - timedelta(hours=2),
                                data_retorno=datetime.now()))
    db.session.commit()

    c = cliente_logado(du.userA)   # GLOBAL da MB — global DA empresa dele
    html = c.get('/relatorio/operacional').get_data(as_text=True)

    assert 'Nome Da Acme' not in html, 'o rótulo trouxe o nome da outra empresa'
    assert 'Nome Da MB' in html, 'o rótulo perdeu o nome certo'
