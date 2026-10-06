"""
Testes da B4 (pacote Baterias) — o vínculo peça↔coletor mora na página do coletor.

A B3 tirou o pareamento do catálogo de peças e deixou a rota `item_parear` de pé,
esperando um ponto de entrada novo. Esta fatia é esse ponto: uma seção de
Complementos dentro do coletor, onde a pergunta é "o que sai junto com ESTE
aqui?" — que é a pergunta que alguém realmente faz.

Dois grupos, como na B3: o que a ROTA grava e o que a TELA mostra. E um terceiro
que a B4 acrescenta, porque a tela oferece uma lista: o que a lista NÃO pode
oferecer (peça de outro CD, de outra empresa, bateria, peça desativada).

O teste que dá nome à fatia é `test_vincular_rouba_a_peca_do_coletor_anterior`:
o vínculo é um só, e trocar de titular é o caso REAL — o coletor foi para
manutenção e outro assume.
"""
import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import pecas_do_coletor, pecas_para_vincular
from app.models import (CategoriaComplemento, Coletor, Empresa, ItemComplementar,
                        Localidade, COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE,
                        COMPLEMENTO_CAMARA_SECO)


@pytest.fixture()
def cena(app, loc):
    """Dois coletores no mesmo CD, um headset pareado com o primeiro."""
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    hs_cat = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                  controle=COMPLEMENTO_UNIDADE)
    db.session.add(hs_cat)
    db.session.flush()

    titular = Coletor(serial_number='SN-B4-1', numero_patrimonio='1001',
                      localidade_id=loc.id, camara='SECO')
    reserva = Coletor(serial_number='SN-B4-2', numero_patrimonio='1002',
                      localidade_id=loc.id, camara='CONGELADO')
    db.session.add_all([titular, reserva])
    db.session.flush()

    pareado = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                               identificador='HS-100', localidade_id=loc.id,
                               coletor_id=titular.id)
    livre = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                             identificador='HS-200', localidade_id=loc.id)
    orfa = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                            identificador='HS-300')             # sem CD
    db.session.add_all([pareado, livre, orfa])
    db.session.commit()
    return SimpleNamespace(mb=mb, loc=loc, hs_cat=hs_cat, titular=titular,
                           reserva=reserva, pareado=pareado, livre=livre, orfa=orfa)


def _pagina(cena):
    return '/coletor/%d/historico' % cena.titular.id


# ---------------------------------------------------------------------------
# A TELA — o vínculo passa a se decidir aqui
# ---------------------------------------------------------------------------

def test_pagina_do_coletor_mostra_o_que_acompanha(admin_client, cena):
    """🔴 Leitura de tela: a peça pareada precisa APARECER, com o tipo dela.

    A rota podia estar perfeita e a seção não renderizar — foi assim que os
    selos da B2 sumiram com a suíte verde.
    """
    html = admin_client.get(_pagina(cena)).get_data(as_text=True)

    assert 'Complementos deste coletor' in html
    assert 'HS-100' in html
    assert 'Desvincular' in html


def test_pagina_oferece_as_pecas_que_dao_para_vincular(admin_client, cena):
    """A lista branca precisa CHEGAR ao template, senão o select sai sem opções."""
    html = admin_client.get(_pagina(cena)).get_data(as_text=True)

    assert f'value="{cena.livre.id}"' in html, 'a peça livre não virou opção'
    assert f'value="{cena.orfa.id}"' in html, 'a peça sem CD não virou opção'
    assert 'Vincular' in html


def test_pagina_avisa_que_a_peca_esta_com_outro_coletor(admin_client, cena):
    """Roubar a peça é legítimo, mas não pode ser surpresa.

    Quem escolhe precisa ler, ANTES de confirmar, que aquele headset hoje
    acompanha outro coletor — o vínculo é um só.
    """
    html = admin_client.get('/coletor/%d/historico' % cena.reserva.id).get_data(as_text=True)

    assert 'HS-100' in html
    assert 'hoje acompanha' in html
    assert cena.titular.patrimonio_display in html


def test_seletor_separa_as_livres_das_que_tem_dono(admin_client, cena):
    """🔴 O agrupamento pedido depois da validação de 08/09.

    Roubar a peça de outro coletor é permitido, mas é EXCEÇÃO. Num CD com tudo
    pareado — o cenário de teste do homolog é assim — a lista inteira virava
    aviso e o excepcional passava por normal. As livres vêm num grupo antes.

    O teste também cobre a mecânica: `selectattr`/`rejectattr` sobre uma lista
    de DICTS depende de o Jinja cair do atributo para a chave. Se isso parar de
    valer, os dois grupos saem vazios e não há erro em lugar nenhum.
    """
    html = admin_client.get('/coletor/%d/historico' % cena.reserva.id).get_data(as_text=True)

    pos_livres = html.find('label="Livres"')
    pos_dono = html.find('label="Já acompanham outro coletor"')
    assert pos_livres != -1, 'o grupo das livres não renderizou'
    assert pos_dono != -1, 'o grupo das que têm dono não renderizou'
    assert pos_livres < pos_dono, 'o caso normal precisa vir antes da exceção'

    # HS-200 e HS-300 estão livres; HS-100 acompanha o outro coletor.
    assert pos_livres < html.find('HS-200') < pos_dono
    assert html.find('HS-100', pos_dono) > pos_dono


def test_peca_que_nao_serve_na_camara_e_avisada(admin_client, cena):
    """D5 — a peça de ambiente seco não serve no congelado. Aviso, não trava.

    Travar aqui criaria uma segunda versão da regra: quem barra de verdade é a
    retirada (H2), que já pede justificativa. Duas travas para a mesma regra
    divergem no dia em que uma for ajustada.
    """
    cena.hs_cat.camara_atendida = COMPLEMENTO_CAMARA_SECO
    db.session.commit()

    html = admin_client.get('/coletor/%d/historico' % cena.reserva.id).get_data(as_text=True)
    assert 'não serve na câmara deste coletor' in html


# ---------------------------------------------------------------------------
# VINCULAR / DESVINCULAR
# ---------------------------------------------------------------------------

def test_vincular_pela_pagina_do_coletor(admin_client, cena):
    admin_client.post('/complementos/item/vincular', data={
        'item_id': cena.livre.id, 'coletor_id': cena.titular.id,
        'voltar': _pagina(cena),
    }, follow_redirects=True)

    assert db.session.get(ItemComplementar, cena.livre.id).coletor_id == cena.titular.id


def test_vincular_rouba_a_peca_do_coletor_anterior(admin_client, cena):
    """🔴 O nome da fatia: o vínculo é UM só, e trocar de titular é o caso real.

    O coletor foi para manutenção e outro assume. A peça muda de dono e o par
    antigo se desfaz sozinho — sem isso, a saída seria desativar a peça e
    recadastrar, perdendo o histórico por um motivo operacional.
    """
    admin_client.post('/complementos/item/vincular', data={
        'item_id': cena.pareado.id, 'coletor_id': cena.reserva.id,
    }, follow_redirects=True)

    assert db.session.get(ItemComplementar, cena.pareado.id).coletor_id == cena.reserva.id
    assert pecas_do_coletor(cena.titular) == [], 'o par antigo sobreviveu'


def test_peca_sem_cd_herda_o_cd_do_coletor(admin_client, cena):
    """A peça órfã aparecia na lista de TODOS os CDs. O vínculo lhe dá lugar."""
    admin_client.post('/complementos/item/vincular', data={
        'item_id': cena.orfa.id, 'coletor_id': cena.titular.id,
    }, follow_redirects=True)

    peca = db.session.get(ItemComplementar, cena.orfa.id)
    assert peca.coletor_id == cena.titular.id
    assert peca.localidade_id == cena.loc.id


def test_desvincular_pela_pagina_do_coletor(admin_client, cena):
    admin_client.post('/complementos/item/%d/parear' % cena.pareado.id,
                      data={'coletor_id': '', 'voltar': _pagina(cena)},
                      follow_redirects=True)

    assert db.session.get(ItemComplementar, cena.pareado.id).coletor_id is None


def test_vincular_sem_escolher_peca_nao_quebra(admin_client, cena):
    resposta = admin_client.post('/complementos/item/vincular',
                                 data={'coletor_id': cena.titular.id},
                                 follow_redirects=True)
    assert resposta.status_code == 200


# ---------------------------------------------------------------------------
# O QUE A LISTA NÃO PODE OFERECER
# ---------------------------------------------------------------------------

def test_lista_nao_oferece_peca_de_outro_cd(app, cena):
    """A peça está fisicamente em outro CD — vinculá-la seria mentira de cadastro."""
    outro = Localidade(sigla='ZZZ', nome='CD Distante', empresa_id=cena.mb.id)
    db.session.add(outro)
    db.session.flush()
    distante = ItemComplementar(empresa_id=cena.mb.id, categoria_id=cena.hs_cat.id,
                                identificador='HS-ZZZ', localidade_id=outro.id)
    db.session.add(distante)
    db.session.commit()

    oferecidas = [c['item'].identificador for c in pecas_para_vincular(cena.titular)]
    assert 'HS-ZZZ' not in oferecidas
    assert 'HS-200' in oferecidas


def test_lista_nao_oferece_bateria_nem_peca_desativada(app, cena):
    """Bateria é contada (B1) e peça aposentada não volta a operar por um select."""
    bat = CategoriaComplemento(empresa_id=cena.mb.id, nome='BATERIA',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    db.session.add(bat)
    db.session.flush()
    db.session.add(ItemComplementar(empresa_id=cena.mb.id, categoria_id=bat.id,
                                    identificador='BAT-1', localidade_id=cena.loc.id))
    cena.livre.ativo = False
    db.session.commit()

    oferecidas = [c['item'].identificador for c in pecas_para_vincular(cena.titular)]
    assert 'BAT-1' not in oferecidas
    assert 'HS-200' not in oferecidas


def test_vincular_em_coletor_de_outra_empresa_e_recusado(admin_client, duas_empresas):
    """🔴 Coletor não tem `empresa_id`: o tenant dele é o da LOCALIDADE.

    Sem checar isso, o Owner (que passa pelo escopo geográfico com None) prende a
    peça de um tenant num coletor de outro — e o complemento nunca apareceria na
    retirada daquele coletor. É a família E do S1, entrando por uma porta nova.
    """
    du = duas_empresas
    cat = CategoriaComplemento(empresa_id=du.mb.id, nome='HEADSET A',
                               controle=COMPLEMENTO_UNIDADE)
    db.session.add(cat)
    db.session.flush()
    peca = ItemComplementar(empresa_id=du.mb.id, categoria_id=cat.id,
                            identificador='HS-A', localidade_id=du.locA.id)
    alheio = Coletor(serial_number='SN-ACME', numero_patrimonio='9001',
                     localidade_id=du.locB.id)
    db.session.add_all([peca, alheio])
    db.session.commit()

    admin_client.post('/complementos/item/vincular',
                      data={'item_id': peca.id, 'coletor_id': alheio.id},
                      follow_redirects=True)

    assert db.session.get(ItemComplementar, peca.id).coletor_id is None


def test_vincular_em_coletor_fora_do_escopo_e_recusado(cliente_logado, duas_empresas):
    """Coletor de outro tenant é inexistente para quem tenta (T3/S2)."""
    du = duas_empresas
    cat = CategoriaComplemento(empresa_id=du.acme.id, nome='HEADSET B',
                               controle=COMPLEMENTO_UNIDADE)
    db.session.add(cat)
    db.session.flush()
    peca = ItemComplementar(empresa_id=du.acme.id, categoria_id=cat.id,
                            identificador='HS-B', localidade_id=du.locB.id)
    alheio = Coletor(serial_number='SN-MB', numero_patrimonio='9002',
                     localidade_id=du.locA.id)
    db.session.add_all([peca, alheio])
    db.session.commit()

    cliente_logado(du.userB).post('/complementos/item/vincular',
                                  data={'item_id': peca.id, 'coletor_id': alheio.id},
                                  follow_redirects=True)

    assert db.session.get(ItemComplementar, peca.id).coletor_id is None


# ---------------------------------------------------------------------------
# Acabamento
# ---------------------------------------------------------------------------

def test_voltar_nao_manda_para_fora_do_sistema(admin_client, cena):
    """🔴 `voltar` vem do formulário, e `redirect()` obedece a qualquer URL.

    Um destino absoluto transformaria a ação numa ponte para outro site. Só
    caminho relativo passa; o resto cai no padrão.
    """
    resposta = admin_client.post('/complementos/item/vincular', data={
        'item_id': cena.livre.id, 'coletor_id': cena.titular.id,
        'voltar': 'https://exemplo-malicioso.test/colhe',
    })

    assert resposta.status_code in (301, 302)
    assert 'exemplo-malicioso' not in resposta.headers.get('Location', '')

    # e o caminho relativo legítimo continua funcionando
    ok = admin_client.post('/complementos/item/%d/parear' % cena.livre.id,
                           data={'coletor_id': '', 'voltar': _pagina(cena)})
    assert _pagina(cena) in ok.headers.get('Location', '')


def test_uma_consulta_serve_a_secao_inteira(app, cena):
    """Anti-N+1: esta tela já pagou uma reescrita por causa disso (custo acumulado)."""
    from sqlalchemy import event

    pecas = pecas_do_coletor(cena.titular)
    candidatas = pecas_para_vincular(cena.titular)
    consultas = []
    engine = db.session.get_bind()

    def ouvir(*args, **kwargs):
        consultas.append(1)

    event.listen(engine, 'before_cursor_execute', ouvir)
    try:
        for peca in pecas:                       # o que o template faz por linha
            _ = (peca.identificador, peca.categoria.nome,
                 peca.categoria.atende_camara(cena.titular.camara))
        for c in candidatas:
            _ = (c['item'].identificador, c['item'].categoria.nome, c['serve'])
            if c['com_outro']:
                _ = c['com_outro'].patrimonio_display
    finally:
        event.remove(engine, 'before_cursor_execute', ouvir)

    assert consultas == [], f'{len(consultas)} consulta(s) extra(s) — o joinedload caiu'


def test_secao_traduzida_nao_tem_texto_cru(admin_client, cena):
    html = admin_client.get(_pagina(cena)).get_data(as_text=True)
    assert 'coletorcomp.' not in html
