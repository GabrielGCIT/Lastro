"""
Testes da H4 — a pendência ESCANCARADA no inventário e no dashboard.

A H3 já registrava a falta no histórico do coletor. Não bastava: ninguém abre o
histórico de um coletor por acaso — abre quando já desconfia. Uma dívida que só
existe onde quem a criou não vai olhar é uma dívida que não cobra ninguém.

O teste que dá nome à fatia é `test_dashboard_conta_todas_e_mostra_as_recentes`:
o painel corta a LISTA em 10, mas o TOTAL conta tudo. Um alerta de cobrança que
mente para menos é pior do que não existir.
"""
import pytest

from app import db
from app.complementos import (descrever_pendencia, pendencias_abertas,
                              pendencias_por_coletor)
from app.models import (CategoriaComplemento, Coletor, Colaborador, Empresa,
                        ItemComplementar, Movimentacao, MovimentacaoComplemento,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE)


@pytest.fixture()
def devedor(app, loc):
    """Um coletor que JÁ voltou devendo: 2 baterias e o headset pareado.

    Devolvido (data_retorno preenchida) de propósito — é o caso que a tela
    precisa gritar. O que ainda está em campo também é dívida em aberto, mas o
    prazo dele não venceu.
    """
    from datetime import datetime
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    bat = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    hs_cat = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                  controle=COMPLEMENTO_UNIDADE)
    db.session.add_all([bat, hs_cat])
    db.session.flush()

    coletor = Coletor(serial_number='SN-H4', numero_patrimonio='PAT-H4',
                      localidade_id=loc.id, status='Disponível', camara='SECO')
    colab = Colaborador(re='4400', nome='Rita Devedora', empresa_id=mb.id)
    db.session.add_all([coletor, colab])
    db.session.flush()

    hs = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                          identificador='HS-H4', coletor_id=coletor.id,
                          localidade_id=loc.id)
    db.session.add(hs)
    db.session.flush()

    mov = Movimentacao(coletor_id=coletor.id, re_colaborador='4400',
                       colaborador_id=colab.id,
                       data_retorno=datetime(2026, 8, 27, 14, 30))
    db.session.add(mov)
    db.session.flush()
    # saiu com 3 baterias, voltou com 1 → faltam 2; o headset não voltou
    db.session.add_all([
        MovimentacaoComplemento(movimentacao_id=mov.id, categoria_id=bat.id,
                                qtd_saida=3, qtd_devolvida=1),
        MovimentacaoComplemento(movimentacao_id=mov.id, categoria_id=hs_cat.id,
                                item_id=hs.id, qtd_saida=1, qtd_devolvida=0),
    ])
    db.session.commit()
    return SimpleNamespace(mb=mb, loc=loc, coletor=coletor, colab=colab,
                           mov=mov, bat=bat, hs_cat=hs_cat, hs=hs)


# ---------------------------------------------------------------------------
# domínio
# ---------------------------------------------------------------------------

def test_so_entra_quem_esta_devendo(app, devedor):
    """Linha quitada não é pendência — o filtro é `devolvida < saida`."""
    linhas = pendencias_abertas()
    assert len(linhas) == 2

    quitada = MovimentacaoComplemento.query.filter_by(
        movimentacao_id=devedor.mov.id, categoria_id=devedor.bat.id).first()
    quitada.qtd_devolvida = 3
    db.session.commit()

    restantes = pendencias_abertas()
    assert len(restantes) == 1
    assert restantes[0].categoria.nome == 'HEADSET'


def test_escopo_vazio_nao_devolve_nada(app, devedor):
    """Fail-closed como o get_filtro_localidade do T3.

    Lista vazia significa "este usuário não alcança localidade nenhuma". Tratar
    isso como "sem recorte" transformaria o guarda em vazamento — exatamente a
    família C que o S3 fechou.
    """
    assert pendencias_abertas([]) == []
    assert pendencias_por_coletor([]) == {}
    assert len(pendencias_abertas(None)) == 2          # None = Owner, sem recorte
    assert len(pendencias_abertas([devedor.loc.id])) == 2


def test_descricao_e_a_mesma_frase_em_toda_tela(app, devedor):
    """A quantidade que aparece é a que FALTA, não a que saiu."""
    por_nome = {l.categoria.nome: l for l in pendencias_abertas()}
    assert descrever_pendencia(por_nome['BATERIA SECO']) == '2x BATERIA SECO'
    # item por unidade carrega o identificador: "1x HEADSET" sozinho não diz qual
    assert descrever_pendencia(por_nome['HEADSET']) == '1x HEADSET (HS-H4)'


def test_indice_por_coletor_agrupa_as_duas_faltas(app, devedor):
    indice = pendencias_por_coletor()
    assert list(indice) == [devedor.coletor.id]
    assert len(indice[devedor.coletor.id]) == 2


def test_uma_consulta_serve_a_listagem_inteira(app, devedor):
    """Anti-N+1: o inventário desenha as pendências sem voltar ao banco.

    A listagem pode ter centenas de coletores. Se o template resolvesse coletor,
    colaborador e categoria por linha, a tela pagaria consultas por pendência —
    o mesmo custo que já obrigou a reescrever o histórico uma vez.
    """
    from sqlalchemy import event

    linhas = pendencias_abertas()
    consultas = []
    engine = db.session.get_bind()

    def ouvir(*args, **kwargs):
        consultas.append(1)

    event.listen(engine, 'before_cursor_execute', ouvir)
    try:
        for linha in linhas:                     # o que o template faz por linha
            _ = (linha.categoria.nome,
                 linha.movimentacao.coletor.patrimonio_display,
                 linha.movimentacao.colaborador.nome,
                 linha.faltando)
            if linha.item:
                _ = linha.item.identificador
    finally:
        event.remove(engine, 'before_cursor_execute', ouvir)

    assert consultas == [], f'{len(consultas)} consulta(s) extra(s) — o joinedload caiu'


# ---------------------------------------------------------------------------
# inventário
# ---------------------------------------------------------------------------

def test_inventario_marca_a_linha_e_diz_o_que_falta(admin_client, devedor):
    """Não basta marcar: 'faltando' sem dizer O QUE e COM QUEM não cobra."""
    html = admin_client.get('/coletores').get_data(as_text=True)
    assert 'VOLTOU FALTANDO' in html
    assert 'table-danger' in html
    assert '2x BATERIA SECO' in html
    assert '1x HEADSET (HS-H4)' in html
    assert 'Rita Devedora' in html
    assert '27/08' in html


def test_inventario_em_dia_nao_grita(admin_client, devedor):
    for linha in MovimentacaoComplemento.query.all():
        linha.qtd_devolvida = linha.qtd_saida
    db.session.commit()

    html = admin_client.get('/coletores').get_data(as_text=True)
    assert 'VOLTOU FALTANDO' not in html


def test_filtro_recorta_so_quem_esta_devendo(admin_client, devedor, loc):
    """O banner promete um recorte; o recorte precisa existir de verdade."""
    limpo = Coletor(serial_number='SN-OK', numero_patrimonio='PAT-OK',
                    localidade_id=loc.id, status='Disponível')
    db.session.add(limpo)
    db.session.commit()

    tudo = admin_client.get('/coletores').get_data(as_text=True)
    assert 'PAT-OK' in tudo and 'PAT-H4' in tudo

    so_devendo = admin_client.get('/coletores?pendencia=1').get_data(as_text=True)
    assert 'PAT-H4' in so_devendo
    assert 'PAT-OK' not in so_devendo


def test_pendencia_de_outro_tenant_nao_aparece(cliente_logado, duas_empresas, devedor):
    """A dívida é da MB; o usuário da Acme não pode enxergá-la.

    Vale o mesmo princípio da família C: a pendência sai de uma tabela por
    tenant, mas chega à tela por relacionamento — o recorte tem de ser aplicado
    na consulta, não confiado ao template.
    """
    html = cliente_logado(duas_empresas.userB).get('/coletores').get_data(as_text=True)
    assert 'VOLTOU FALTANDO' not in html
    assert 'Rita Devedora' not in html


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

def test_dashboard_escancara_a_falta(admin_client, devedor):
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Complementos que não voltaram' in html
    assert 'PAT-H4' in html
    assert '2x BATERIA SECO' in html
    assert 'Rita Devedora' in html


def test_dashboard_conta_todas_e_mostra_as_recentes(admin_client, devedor, loc):
    """O corte é da LISTA, nunca do TOTAL.

    Um painel que diz "10" havendo 40 itens em falta mente para menos, e mentir
    para menos num alerta de cobrança é pior do que não ter o alerta.
    """
    from datetime import datetime

    for i in range(12):
        c = Coletor(serial_number=f'SN-X{i}', numero_patrimonio=f'PAT-X{i}',
                    localidade_id=loc.id, status='Disponível')
        db.session.add(c)
        db.session.flush()
        m = Movimentacao(coletor_id=c.id, re_colaborador='4400',
                         colaborador_id=devedor.colab.id,
                         data_retorno=datetime(2026, 8, 20, 9, 0))
        db.session.add(m)
        db.session.flush()
        db.session.add(MovimentacaoComplemento(movimentacao_id=m.id,
                                               categoria_id=devedor.bat.id,
                                               qtd_saida=1, qtd_devolvida=0))
    db.session.commit()

    # 2 baterias + 1 headset do devedor, mais 12 baterias novas
    assert sum(l.faltando for l in pendencias_abertas()) == 15

    html = admin_client.get('/').get_data(as_text=True)
    assert '15 item(ns) em falta' in html
    assert 'A lista completa está no inventário' in html


def test_dashboard_sem_pendencia_nao_mostra_o_bloco(admin_client, loc):
    """Mira a TABELA do bloco, não uma frase solta.

    A U2 acrescentou um KPI "Em falta" que aparece sempre, inclusive zerado — e
    o teste amarrado ao texto passou a acusar o cartão em vez do bloco. Checar a
    coluna, que só existe dentro da tabela, diz exatamente o que se quer saber.
    """
    html = admin_client.get('/').get_data(as_text=True)
    assert 'pendcomp.col_coletor' not in html
    assert 'O que falta' not in html
