"""
Testes da B1 — bateria vira coisa própria.

Bateria não é um complemento como os outros: é FUNGÍVEL, some o tempo todo e
ninguém quer cadastrá-la uma a uma. Tratá-la junto com headset e suporte foi o
que obrigou a tela a perguntar "como você controla essas peças?" — uma decisão de
modelagem empurrada para quem opera o CD.

O teste que dá nome à fatia é `test_backfill_marca_a_bateria_ja_cadastrada`: sem
ele, toda categoria que já existe nasce desmarcada, some da tela nova E do popup
do balcão. A coluna é trivial; o backfill é que carrega o risco.
"""
import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import baterias_compativeis, rastreio_quantidades
from app.models import (CategoriaComplemento, Coletor, Empresa,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE,
                        COMPLEMENTO_CAMARA_SECO)


@pytest.fixture()
def catalogo(app, loc):
    """Bateria, um contado que NÃO é bateria, e um por unidade."""
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    bateria = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                                   controle=COMPLEMENTO_QUANTIDADE,
                                   camara_atendida=COMPLEMENTO_CAMARA_SECO,
                                   e_bateria=True)
    # 🔴 O caso que a heurística antiga errava: contado, mas não é bateria.
    cabo = CategoriaComplemento(empresa_id=mb.id, nome='CABO USB',
                                controle=COMPLEMENTO_QUANTIDADE)
    headset = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                   controle=COMPLEMENTO_UNIDADE)
    db.session.add_all([bateria, cabo, headset])
    db.session.flush()
    coletor = Coletor(serial_number='SN-B1', numero_patrimonio='B1',
                      localidade_id=loc.id, camara='SECO')
    db.session.add(coletor)
    db.session.commit()
    return SimpleNamespace(mb=mb, loc=loc, bateria=bateria, cabo=cabo,
                           headset=headset, coletor=coletor)


# ---------------------------------------------------------------------------
# a marca substitui a adivinhação
# ---------------------------------------------------------------------------

def test_contado_que_nao_e_bateria_nao_vira_bateria(app, catalogo):
    """🔴 Antes da B1, "é bateria" era ADIVINHADO por `controle=QUANTIDADE`.

    Funcionava enquanto bateria era a única coisa contada. No dia em que alguém
    cadastrasse CABO USB por quantidade, o popup de bateria ofereceria cabo — e
    o portão de câmara passaria a julgar a compatibilidade térmica de um cabo.
    """
    nomes = [c.nome for c in baterias_compativeis(catalogo.mb.id, catalogo.coletor)]
    assert 'BATERIA SECO' in nomes
    assert 'CABO USB' not in nomes


def test_marca_nao_mexe_no_calculo_da_h5(app, catalogo):
    """O domínio de saldo e atraso continua o mesmo — a B1 só separa telas.

    Trocar o modelo por causa de apresentação jogaria fora código testado e em
    produção. O parâmetro apenas RECORTA o que já era calculado.
    """
    todos = rastreio_quantidades(None, catalogo.mb.id)
    so_bat = rastreio_quantidades(None, catalogo.mb.id, so_bateria=True)
    sem_bat = rastreio_quantidades(None, catalogo.mb.id, so_bateria=False)
    # sem estoque nem movimento não há linha; o que importa é o recorte não
    # inventar nem perder categoria
    nomes = lambda ls: {s['categoria'].nome for s in ls}          # noqa: E731
    assert nomes(so_bat) | nomes(sem_bat) == nomes(todos)
    assert not (nomes(so_bat) & nomes(sem_bat))


# ---------------------------------------------------------------------------
# a separação das telas
# ---------------------------------------------------------------------------

def test_catalogo_de_complementos_nao_lista_bateria(admin_client, catalogo):
    html = admin_client.get('/complementos').get_data(as_text=True)
    assert '<td class="fw-bold">HEADSET</td>' in html
    assert '<td class="fw-bold">BATERIA SECO</td>' not in html


def test_o_balcao_continua_enxergando_a_bateria(app, catalogo):
    """🔴 A separação é de TELA, não de operação.

    Se a bateria sumisse da retirada junto com a tela, a fatia teria quebrado o
    balcão — o ponto de maior volume e menor tolerância a erro do sistema.
    """
    from app.complementos import sugerir_para_coletor
    sugestao = sugerir_para_coletor(catalogo.mb.id, catalogo.coletor)
    assert [b['nome'] for b in sugestao['baterias']] == ['BATERIA SECO']


def test_placeholder_nao_sugere_bateria_onde_ela_nao_mora(admin_client, catalogo):
    """O campo de nome exemplificava com "BATERIA COLETOR CLIMATIZADO" numa tela
    que não aceita mais bateria. Exemplo errado é pior do que exemplo nenhum."""
    html = admin_client.get('/complementos').get_data(as_text=True)
    assert 'placeholder="BATERIA' not in html
