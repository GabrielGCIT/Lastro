"""
Testes da U2 — dashboard por exceção.

O painel mostrava oito números e nenhum deles levava a lugar nenhum: "5
indisponíveis" e o usuário sem como ver quais são. E um dos cartões só continha
um botão, quebrando o padrão dos outros sete.

O teste que dá nome à fatia é `test_kpi_de_indisponiveis_bate_com_a_lista`: um
número que leva a uma lista com outra contagem é pior do que número sem link —
ele parece confiável e não é.
"""
import pytest

from app import db
from app.models import (Coletor, Colaborador, Movimentacao,
                        MovimentacaoComplemento, CategoriaComplemento,
                        STATUS_FORA_DE_OPERACAO, STATUS_INDISPONIVEIS,
                        COMPLEMENTO_QUANTIDADE)


@pytest.fixture()
def frota(app, loc):
    """Um coletor em cada situação relevante do painel."""
    from types import SimpleNamespace

    disponiveis, indisponiveis = [], []
    for i in range(2):
        c = Coletor(serial_number=f'SN-D{i}', numero_patrimonio=f'D{i}',
                    localidade_id=loc.id, status='Disponível')
        db.session.add(c)
        disponiveis.append(c)
    # um de cada status que o painel agrupa como "indisponível"
    for i, st in enumerate(STATUS_FORA_DE_OPERACAO):
        c = Coletor(serial_number=f'SN-I{i}', numero_patrimonio=f'I{i}',
                    localidade_id=loc.id, status=st)
        db.session.add(c)
        indisponiveis.append(c)
    em_uso = Coletor(serial_number='SN-U', numero_patrimonio='U0',
                     localidade_id=loc.id, status='Em Uso')
    db.session.add(em_uso)
    db.session.commit()
    return SimpleNamespace(loc=loc, disponiveis=disponiveis,
                           indisponiveis=indisponiveis, em_uso=em_uso)


# ---------------------------------------------------------------------------
# o número vira porta
# ---------------------------------------------------------------------------

def test_kpis_levam_para_a_lista_filtrada(admin_client, frota):
    html = admin_client.get('/').get_data(as_text=True)
    assert '/coletores?status=Dispon' in html
    assert f'/coletores?status={STATUS_INDISPONIVEIS}' in html
    assert '/coletores?pendencia=1' in html


def test_kpi_de_indisponiveis_bate_com_a_lista(admin_client, frota):
    """🔴 O número do painel e a lista de destino têm de contar o MESMO conjunto.

    "Indisponível" não é um status, é um grupo. Se o inventário filtrasse por um
    só, o painel diria 5 e a lista mostraria 1 — um número que parece confiável e
    não é, que é pior do que número sem link nenhum.
    """
    lista = admin_client.get(
        f'/coletores?status={STATUS_INDISPONIVEIS}').get_data(as_text=True)
    for c in frota.indisponiveis:
        assert c.numero_patrimonio in lista, f'{c.status} sumiu do recorte'
    # e nenhum dos disponíveis entra. A checagem inclui o `name=` de propósito:
    # `value="1"` sozinho casa com o <option> do dropdown de CDs, e o teste
    # passaria ou falharia por causa de um select que não tem nada a ver.
    for c in frota.disponiveis:
        assert f'name="coletor_ids" value="{c.id}"' not in lista


def test_status_do_grupo_nao_colide_com_status_real(admin_client, frota):
    """O valor reservado é minúsculo; os status reais são capitalizados."""
    assert STATUS_INDISPONIVEIS not in STATUS_FORA_DE_OPERACAO
    lista = admin_client.get('/coletores?status=Disponível').get_data(as_text=True)
    assert f'name="coletor_ids" value="{frota.disponiveis[0].id}"' in lista
    assert f'name="coletor_ids" value="{frota.em_uso.id}"' not in lista


# ---------------------------------------------------------------------------
# o cartão que não era cartão
# ---------------------------------------------------------------------------

def test_cartao_de_analise_virou_kpi_de_verdade(admin_client, frota):
    """Ele só continha um botão. Agora mostra um número, como os vizinhos."""
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Em falta' in html


# ---------------------------------------------------------------------------
# o painel de decisões
# ---------------------------------------------------------------------------

def test_sem_pendencia_o_painel_nao_aparece(admin_client, loc):
    """Alerta que acende todo dia deixa de ser lido.

    Sem a fixture `frota` de propósito: ela cria um coletor Suspenso, que É uma
    pendência — o teste passaria a afirmar o contrário do que quer provar.
    """
    db.session.add(Coletor(serial_number='SN-OK', numero_patrimonio='OK1',
                           localidade_id=loc.id, status='Disponível'))
    db.session.commit()
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Precisa de você hoje' not in html


def test_coletor_em_manutencao_nao_acende_o_painel(admin_client, loc):
    """Sem módulo de lotes, não há o que cobrar de um coletor em Manutenção: ele
    sai da operação e volta pelo cadastro. Acender o painel por ele seria pedir
    uma ação que o sistema não oferece."""
    db.session.add(Coletor(serial_number='SN-MAN', numero_patrimonio='MAN1',
                           localidade_id=loc.id, status='Manutenção'))
    db.session.commit()
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Precisa de você hoje' not in html
    assert '/manutencao' not in html


def test_complemento_em_falta_aparece_como_decisao(admin_client, loc, frota):
    from datetime import datetime

    mb_id = frota.disponiveis[0].localidade.empresa_id
    cat = CategoriaComplemento(empresa_id=mb_id, nome='BATERIA',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    colab = Colaborador(re='7700', nome='Quem Levou', empresa_id=mb_id)
    db.session.add_all([cat, colab])
    db.session.flush()
    mov = Movimentacao(coletor_id=frota.disponiveis[0].id, re_colaborador='7700',
                       colaborador_id=colab.id, data_retorno=datetime.now())
    db.session.add(mov)
    db.session.flush()
    db.session.add(MovimentacaoComplemento(movimentacao_id=mov.id,
                                           categoria_id=cat.id,
                                           qtd_saida=3, qtd_devolvida=1))
    db.session.commit()

    html = admin_client.get('/').get_data(as_text=True)
    assert 'Precisa de você hoje' in html
    assert 'não voltaram' in html


def test_suspenso_aparece_com_destino_proprio(admin_client, loc):
    """Suspensão é violação de integridade, com tela e ação próprias: a linha do
    painel leva para /suspensos, que é onde o caso se resolve."""
    susp = Coletor(serial_number='SN-SUS', numero_patrimonio='SUS1',
                   localidade_id=loc.id, status='Suspenso')
    db.session.add(susp)
    db.session.commit()

    html = admin_client.get('/').get_data(as_text=True)
    assert 'suspenso(s) aguardando análise' in html
    assert '/suspensos' in html
