"""
Testes da baixa de complemento DEPOIS que o coletor já voltou.

🔴 Até 21/09/2026 o sistema inteiro tinha UM caminho para baixar complemento:
`conferir_devolucao`, chamado só na devolução do coletor. Bateria que volta no
dia seguinte é rotina de operação, não exceção — e não havia onde registrar.

O teste que dá nome ao arquivo é `test_a_falta_sai_da_tela_quando_a_peca_volta`:
sem ele a linha vermelha ficava para sempre, e o saldo do CD ficava
permanentemente errado para menos, porque a bateria seguia contada como "na
rua". A saída que restava era recontar a prateleira e ajustar na mão — o que
apaga de quem era a cobrança.

Achado pelo Gabriel testando a demo na véspera do treinamento.
"""
from datetime import datetime, timedelta

import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import (ComplementoRecusado, disponivel_por_categoria,
                              pendencias_abertas, registrar_devolucao_atrasada)
from app.models import (CategoriaComplemento, Colaborador, Coletor, Empresa,
                        EstoqueComplemento, Movimentacao, MovimentacaoComplemento,
                        COMPLEMENTO_QUANTIDADE)


@pytest.fixture()
def falta(app, loc):
    """Um coletor que JÁ VOLTOU e uma bateria que não voltou com ele."""
    from types import SimpleNamespace
    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()

    cat = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA PADRAO',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    coletor = Coletor(serial_number='SN-FALTA', numero_patrimonio='001',
                      localidade_id=loc.id, status='Disponível')
    db.session.add_all([cat, coletor,
                        Colaborador(re='20101', nome='Anderson Souza', empresa_id=mb.id)])
    db.session.flush()
    db.session.add(EstoqueComplemento(empresa_id=mb.id, categoria_id=cat.id,
                                      localidade_id=loc.id, qtd_cadastrada=26))

    mov = Movimentacao(coletor_id=coletor.id, re_colaborador='20101',
                       data_saida=datetime.now() - timedelta(days=2),
                       data_retorno=datetime.now() - timedelta(days=1))
    db.session.add(mov)
    db.session.flush()
    linha = MovimentacaoComplemento(movimentacao_id=mov.id, categoria_id=cat.id,
                                    qtd_saida=1, qtd_devolvida=0)
    db.session.add(linha)
    db.session.commit()
    return SimpleNamespace(mb=mb, cat=cat, coletor=coletor, mov=mov,
                           linha=linha, loc=loc)


def test_a_falta_sai_da_tela_quando_a_peca_volta(app, admin_client, falta):
    """🔴 O nome do arquivo, pelo caminho real da tela."""
    assert len(pendencias_abertas()) == 1, 'o cenário precisa começar com a falta'

    admin_client.post(f'/complementos/pendencia/{falta.linha.id}/devolver',
                      follow_redirects=True)

    assert pendencias_abertas() == [], 'a falta continua na tela depois de devolvida'


def test_o_saldo_do_cd_volta_ao_lugar(app, admin_client, falta):
    """O dano silencioso: a bateria seguia contada como "na rua" para sempre."""
    antes = disponivel_por_categoria(falta.cat.id, falta.loc.id)

    admin_client.post(f'/complementos/pendencia/{falta.linha.id}/devolver',
                      follow_redirects=True)

    assert disponivel_por_categoria(falta.cat.id, falta.loc.id) == antes + 1


def test_devolver_parcial_deixa_o_resto_cobrado(app, admin_client, falta):
    """Saíram 3, voltaram 2: a cobrança continua pela que falta."""
    falta.linha.qtd_saida = 3
    db.session.commit()

    admin_client.post(f'/complementos/pendencia/{falta.linha.id}/devolver',
                      data={'quantidade': '2'}, follow_redirects=True)

    db.session.refresh(falta.linha)
    assert falta.linha.faltando == 1
    assert len(pendencias_abertas()) == 1


def test_nao_da_para_devolver_mais_do_que_saiu(app, falta):
    """Devolver 5 de uma saída de 1 criaria bateria do nada no estoque."""
    registrar_devolucao_atrasada(falta.linha, 99)

    assert falta.linha.qtd_devolvida == falta.linha.qtd_saida


def test_pendencia_ja_quitada_recusa(app, falta):
    """Dois cliques no mesmo botão não podem devolver a peça duas vezes.

    Com QUANTIDADE explícita de propósito: sem ela, `pedido` nasce igual a
    `faltando` (zero) e quem recusa é a checagem de quantidade, não a de
    pendência quitada — o teste passaria mesmo sem esta guarda, provando outra
    coisa. O clique do botão manda quantidade quando o operador digita.
    """
    registrar_devolucao_atrasada(falta.linha)

    with pytest.raises(ComplementoRecusado):
        registrar_devolucao_atrasada(falta.linha, 1)


def test_nao_alcanca_pendencia_de_cd_alheio(app, cliente_logado, falta, duas_empresas):
    """Mesma regra do resto da operação: fora do escopo é inexistente."""
    outro = cliente_logado(duas_empresas.userB)

    outro.post(f'/complementos/pendencia/{falta.linha.id}/devolver',
               follow_redirects=True)

    db.session.refresh(falta.linha)
    assert falta.linha.faltando == 1, 'usuário de outra empresa baixou a pendência'


def test_o_botao_aparece_no_dashboard(admin_client, falta):
    """Rota que a tela não alcança é rota que não existe para o operador."""
    html = admin_client.get('/').get_data(as_text=True)

    assert f'/complementos/pendencia/{falta.linha.id}/devolver' in html


def test_o_botao_aparece_no_historico_do_coletor(admin_client, falta):
    """O outro lugar onde alguém encontra a falta: a ficha do coletor."""
    html = admin_client.get(f'/coletor/{falta.coletor.id}/historico').get_data(as_text=True)

    assert f'/complementos/pendencia/{falta.linha.id}/devolver' in html
