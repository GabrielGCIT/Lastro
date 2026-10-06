"""
Leitura do POST do balcão: uma seleção por categoria.

🔴 `ler_selecoes_do_form` montava uma seleção para CADA ocorrência de
`complemento_categoria`, mas lia quantidade, item e dispensa com `form.get()` —
um valor só, nomeado pela categoria. Então a categoria repetida não descrevia
duas peças diferentes: descrevia a MESMA seleção duas vezes, e cada repetição
virava uma linha de saída. Dois `complemento_categoria=1` com `qtd_1=2` davam
4 baterias em 2 linhas, e a devolução pedia ao operador para conferir
"BATERIA PADRAO" duas vezes, sem dizer que eram a mesma coisa.

Achado percorrendo o balcão na L3, por acidente: dois POSTs sem recarregar a
página deixaram os campos ocultos acumulados no form.
"""
from werkzeug.datastructures import MultiDict

from app.complementos import ler_selecoes_do_form


def test_categoria_repetida_vira_uma_selecao_so():
    """🔴 O nome do arquivo."""
    form = MultiDict([
        ('complemento_categoria', '1'),
        ('complemento_categoria', '1'),
        ('complemento_qtd_1', '2'),
    ])

    selecoes = ler_selecoes_do_form(form)

    assert len(selecoes) == 1, f'{len(selecoes)} seleções para uma categoria'
    assert selecoes[0]['qtd'] == 2, 'a quantidade não é somada — era a mesma seleção'


def test_zero_a_esquerda_e_a_mesma_categoria():
    """'1' e '01' são o mesmo id depois do int().

    A primeira correção deduplicava as strings cruas e deixava este caso passar:
    duas strings diferentes, uma categoria só.
    """
    form = MultiDict([
        ('complemento_categoria', '1'),
        ('complemento_categoria', '01'),
        ('complemento_qtd_1', '3'),
    ])

    assert len(ler_selecoes_do_form(form)) == 1


def test_categorias_diferentes_continuam_separadas():
    """O contraponto: deduplicar demais apagaria o segundo complemento.

    Sem este teste, `selecoes = selecoes[:1]` passaria nos dois de cima.
    """
    form = MultiDict([
        ('complemento_categoria', '1'),
        ('complemento_categoria', '7'),
        ('complemento_qtd_1', '2'),
        ('complemento_item_7', '47'),
    ])

    selecoes = ler_selecoes_do_form(form)

    assert [s['categoria_id'] for s in selecoes] == [1, 7]
    assert selecoes[0]['qtd'] == 2
    assert selecoes[1]['item_id'] == 47


def test_a_primeira_ocorrencia_manda_e_a_ordem_se_mantem():
    """A ordem de chegada é a ordem das linhas gravadas — não embaralhar."""
    form = MultiDict([
        ('complemento_categoria', '7'),
        ('complemento_categoria', '1'),
        ('complemento_categoria', '7'),
        ('complemento_qtd_1', '1'),
        ('complemento_qtd_7', '1'),
    ])

    assert [s['categoria_id'] for s in ler_selecoes_do_form(form)] == [7, 1]


def test_lixo_no_lugar_do_id_e_ignorado():
    """Já era assim; fica preso para a dedup não ter mudado o comportamento."""
    form = MultiDict([
        ('complemento_categoria', 'abc'),
        ('complemento_categoria', '1'),
        ('complemento_qtd_1', '1'),
    ])

    assert [s['categoria_id'] for s in ler_selecoes_do_form(form)] == [1]
