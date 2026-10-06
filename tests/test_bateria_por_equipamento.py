"""
Testes da compatibilidade por EQUIPAMENTO — de que equipamento a peça é.

O sistema tinha uma dimensão só de compatibilidade: frio × seco. Marcar um tipo
como bateria significava, na prática, "ofereça isso quando alguém retirar um
COLETOR" — e não havia campo para dizer o contrário.

O teste que dá nome ao arquivo é `test_bateria_de_headset_nao_sai_com_coletor`:
o Gabriel cadastrou "BATERIA HEADSET" na véspera do treinamento e o balcão
passou a sugeri-la para o coletor de pista. Pior que aparecer na lista: ela
EMPATAVA em prioridade com a bateria certa e ganhava no desempate por nome, o
que a tornava a opção pré-selecionada.
"""
import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import baterias_compativeis
from app.models import (CategoriaComplemento, Coletor, Empresa,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_CAMARA_SECO,
                        COMPLEMENTO_CAMARA_CLIMATIZADO,
                        EQUIPAMENTO_COLETOR, EQUIPAMENTO_OUTRO)


@pytest.fixture()
def catalogo(app, loc):
    """As três baterias do cenário real: duas de coletor, uma de headset."""
    from types import SimpleNamespace
    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()

    def tipo(nome, camara, equipamento):
        c = CategoriaComplemento(empresa_id=mb.id, nome=nome,
                                 controle=COMPLEMENTO_QUANTIDADE,
                                 camara_atendida=camara, e_bateria=True,
                                 equipamento=equipamento)
        db.session.add(c)
        return c

    padrao = tipo('BATERIA PADRÃO', COMPLEMENTO_CAMARA_SECO, EQUIPAMENTO_COLETOR)
    camara = tipo('BATERIA CÂMARA', COMPLEMENTO_CAMARA_CLIMATIZADO, EQUIPAMENTO_COLETOR)
    headset = tipo('BATERIA HEADSET', COMPLEMENTO_CAMARA_SECO, EQUIPAMENTO_OUTRO)
    db.session.commit()
    return SimpleNamespace(mb=mb, padrao=padrao, camara=camara, headset=headset)


def _coletor(loc, camara, sufixo):
    c = Coletor(serial_number=f'SN-{sufixo}', numero_patrimonio=sufixo,
                localidade_id=loc.id, camara=camara, status='Disponível')
    db.session.add(c)
    db.session.commit()
    return c


def test_bateria_de_headset_nao_sai_com_coletor(app, catalogo, loc):
    """🔴 O nome do arquivo."""
    seco = _coletor(loc, 'SECO', 'S1')

    nomes = [b.nome for b in baterias_compativeis(catalogo.mb.id, seco)]

    assert 'BATERIA HEADSET' not in nomes, 'o balcão ofereceu bateria de headset'
    assert nomes[0] == 'BATERIA PADRÃO', \
        f'a sugerida deveria ser a do seco, veio {nomes}'


def test_a_bateria_de_coletor_continua_sendo_oferecida(app, catalogo, loc):
    """A trava não pode levar junto o que sempre funcionou."""
    congelado = _coletor(loc, 'CONGELADO', 'C1')

    nomes = [b.nome for b in baterias_compativeis(catalogo.mb.id, congelado)]

    assert nomes == ['BATERIA CÂMARA'], \
        'no congelado só a climatizada serve — e ela precisa aparecer'


def test_tipo_antigo_sem_a_coluna_continua_de_coletor(app, loc):
    """🔴 Compatibilidade: NULL significa COLETOR.

    Todo tipo cadastrado antes desta coluna tem `equipamento` nulo. Se NULL
    fosse tratado como "outro equipamento", o balcão pararia de oferecer bateria
    em toda base que já existe, inclusive as que estiverem em produção.
    """
    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    antiga = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA ANTIGA',
                                  controle=COMPLEMENTO_QUANTIDADE,
                                  e_bateria=True, equipamento=None)
    db.session.add(antiga)
    db.session.commit()

    assert antiga.serve_coletor is True
    seco = _coletor(loc, 'SECO', 'S2')
    assert 'BATERIA ANTIGA' in [b.nome for b in baterias_compativeis(mb.id, seco)]


def test_a_tela_de_baterias_continua_mostrando_as_duas(admin_client, catalogo):
    """A peça some do BALCÃO, não do estoque: o CD tem essas baterias e precisa
    contá-las. A tela é o inventário; o popup é a retirada de um coletor."""
    html = admin_client.get('/baterias').get_data(as_text=True)

    assert 'BATERIA HEADSET' in html
    assert 'BATERIA PADRÃO' in html


def test_cadastro_pela_tela_grava_o_equipamento(admin_client, loc):
    """O caminho real: o formulário da tela de baterias."""
    admin_client.post('/baterias/tipo/criar', data={
        'nome': 'BATERIA DE HEADSET', 'camara_atendida': 'SECO',
        'equipamento': 'OUTRO',
    }, follow_redirects=True)

    criada = CategoriaComplemento.query.filter_by(nome='BATERIA DE HEADSET').first()
    assert criada is not None, 'o tipo não foi criado'
    assert criada.serve_coletor is False


def test_cadastro_sem_escolher_nasce_de_coletor(admin_client, loc):
    """Quem não responde está cadastrando bateria de coletor — o caso comum."""
    admin_client.post('/baterias/tipo/criar', data={
        'nome': 'BATERIA NOVA', 'camara_atendida': 'SECO',
    }, follow_redirects=True)

    criada = CategoriaComplemento.query.filter_by(nome='BATERIA NOVA').first()
    assert criada is not None and criada.serve_coletor is True


# ---------------------------------------------------------------------------
# CONSERTAR UM TIPO JÁ CADASTRADO
# ---------------------------------------------------------------------------

def test_da_para_corrigir_o_equipamento_de_um_tipo_existente(admin_client, catalogo):
    """🔴 A pergunta nasceu só no CADASTRO.

    Quem respondesse errado precisava desativar o tipo e criar outro — e o
    histórico de movimentação fica preso ao tipo antigo. Foi o Gabriel quem viu,
    na tela, tentando consertar a bateria de headset que ele mesmo cadastrou.
    """
    padrao = catalogo.padrao
    assert padrao.serve_coletor is True

    admin_client.post(f'/baterias/tipo/{padrao.id}/aparencia', data={
        'icone': 'fa-battery-full', 'cor': 'ambar',
        'camara_atendida': 'SECO', 'equipamento': 'OUTRO',
    }, follow_redirects=True)

    db.session.refresh(padrao)
    assert padrao.serve_coletor is False, 'a correção não pegou'


def test_e_da_para_voltar_atras(admin_client, catalogo):
    """Correção que só vai num sentido é meio caminho: quem erra ao consertar
    fica preso do outro lado."""
    headset = catalogo.headset
    assert headset.serve_coletor is False

    admin_client.post(f'/baterias/tipo/{headset.id}/aparencia', data={
        'icone': 'fa-headphones', 'cor': 'verde',
        'camara_atendida': 'SECO', 'equipamento': 'COLETOR',
    }, follow_redirects=True)

    db.session.refresh(headset)
    assert headset.serve_coletor is True


def test_editar_so_a_cor_nao_mexe_no_equipamento(admin_client, catalogo):
    """O formulário que não manda o campo não está pedindo para apagá-lo —
    mesma regra que a câmara já seguia."""
    headset = catalogo.headset

    admin_client.post(f'/baterias/tipo/{headset.id}/aparencia', data={
        'icone': 'fa-headphones', 'cor': 'azul',
    }, follow_redirects=True)

    db.session.refresh(headset)
    assert headset.cor == 'azul', 'a cor não foi salva'
    assert headset.serve_coletor is False, 'o equipamento foi apagado sem ninguém pedir'


def test_a_tela_mostra_o_seletor_em_cada_tipo(admin_client, catalogo):
    """Sem o seletor na linha, a rota existe e ninguém alcança."""
    html = admin_client.get('/baterias').get_data(as_text=True)

    assert html.count('name="equipamento"') >= 4, (
        'esperava o seletor no cadastro e em cada um dos três tipos')
