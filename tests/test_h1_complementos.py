"""
Testes da H1 (pacote Handheld) — catálogo de complementos.

Esta fatia é FUNDAÇÃO: cadastro de tipos, peças identificadas e estoque por CD.
Ela não encosta na tela de movimentação — o que a torna segura de deployar antes
das fatias que mexem no fluxo de balcão. O teste
`test_h1_nao_altera_o_fluxo_de_retirada` é o guarda-corpo dessa promessa.

O teste que dá nome à fatia é `test_bateria_climatizada_serve_em_qualquer_camara`:
a regra do armazém é assimétrica (climatizada serve no seco, seca não serve no
climatizado) e, solta na cabeça do operador, vira bateria morrendo no congelado
às 3h da manhã.
"""
import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.models import (CategoriaComplemento, ItemComplementar, EstoqueComplemento,
                        MovimentacaoComplemento, Movimentacao, Coletor, Empresa,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE,
                        COMPLEMENTO_CAMARA_SECO, COMPLEMENTO_CAMARA_CLIMATIZADO)


@pytest.fixture()
def mb(app):
    return Empresa.query.filter_by(nome=NOME_EMPRESA).first()


@pytest.fixture()
def catalogo(app, mb):
    """O catálogo mínimo da MB: as duas baterias de coletor e o headset."""
    climatizada = CategoriaComplemento(
        empresa_id=mb.id, nome='BATERIA COLETOR CLIMATIZADO',
        controle=COMPLEMENTO_QUANTIDADE, camara_atendida=COMPLEMENTO_CAMARA_CLIMATIZADO, e_bateria=True)
    seca = CategoriaComplemento(
        empresa_id=mb.id, nome='BATERIA COLETOR SECO',
        controle=COMPLEMENTO_QUANTIDADE, camara_atendida=COMPLEMENTO_CAMARA_SECO, e_bateria=True)
    headset = CategoriaComplemento(
        empresa_id=mb.id, nome='HEADSET', controle=COMPLEMENTO_UNIDADE)
    db.session.add_all([climatizada, seca, headset])
    db.session.commit()
    return {'climatizada': climatizada, 'seca': seca, 'headset': headset}


# ---------------------------------------------------------------------------
# D5 — a compatibilidade ASSIMÉTRICA de câmara
# ---------------------------------------------------------------------------

def test_bateria_climatizada_serve_em_qualquer_camara(catalogo):
    """🔴 A regra que justifica a coluna `camara_atendida`.

    A bateria de climatizado tem cor diferente e custa mais, mas funciona em
    qualquer lugar. Recusá-la no seco seria inventar uma restrição que a operação
    não tem — e mandar o operador procurar a "certa" quando já tem uma na mão.
    """
    clim = catalogo['climatizada']
    for camara in ('SECO', 'RESFRIADO', 'CONGELADO', None):
        assert clim.atende_camara(camara) is True


def test_bateria_seca_nao_serve_no_climatizado(catalogo):
    """🔴 O outro lado da assimetria — e o motivo de a regra existir.

    Bateria seca no congelado morre no meio do turno. É o caso que o portão da H2
    vai bloquear (com dispensa auditada, D6).
    """
    seca = catalogo['seca']
    assert seca.atende_camara('SECO') is True
    assert seca.atende_camara('RESFRIADO') is False
    assert seca.atende_camara('CONGELADO') is False


def test_camara_ausente_conta_como_seco(catalogo):
    """Coletor sem câmara declarada não pode travar a retirada.

    Recusar por ausência de informação bloquearia todo coletor que ainda não teve
    o campo preenchido — punindo o operador por um cadastro incompleto que não é
    dele.
    """
    assert catalogo['seca'].atende_camara(None) is True


def test_complemento_sem_restricao_serve_sempre(catalogo):
    """Suporte e headset não se importam com temperatura."""
    for camara in ('SECO', 'RESFRIADO', 'CONGELADO', None):
        assert catalogo['headset'].atende_camara(camara) is True


# ---------------------------------------------------------------------------
# A pendência é DERIVADA, não uma flag
# ---------------------------------------------------------------------------

def test_pendencia_e_derivada_dos_numeros(app, catalogo, loc):
    """🔴 Não existe coluna "pendente" — e é de propósito.

    Uma flag de estado dessincroniza: bastaria uma devolução parcial corrigida
    depois para ela mentir enquanto os números dizem a verdade. Aqui a falta é
    aritmética, então não tem como divergir.
    """
    coletor = Coletor(serial_number='SN-H1', numero_patrimonio='PAT-H1',
                      localidade_id=loc.id)
    db.session.add(coletor)
    db.session.flush()
    mov = Movimentacao(coletor_id=coletor.id, re_colaborador='9001')
    db.session.add(mov)
    db.session.flush()

    linha = MovimentacaoComplemento(movimentacao_id=mov.id,
                                    categoria_id=catalogo['climatizada'].id,
                                    qtd_saida=2, qtd_devolvida=0)
    db.session.add(linha)
    db.session.commit()

    assert linha.faltando == 2 and linha.pendente is True
    linha.qtd_devolvida = 1
    assert linha.faltando == 1 and linha.pendente is True
    linha.qtd_devolvida = 2
    assert linha.faltando == 0 and linha.pendente is False
    # Devolveram mais do que saiu (correção de lançamento): não vira falta negativa.
    linha.qtd_devolvida = 3
    assert linha.faltando == 0 and linha.pendente is False


# ---------------------------------------------------------------------------
# D2 — o pareamento headset ↔ coletor
# ---------------------------------------------------------------------------

def test_headset_pareado_com_coletor(app, mb, catalogo, loc):
    """O par é o que permite saber QUAL headset deveria voltar (voice picking)."""
    coletor = Coletor(serial_number='SN-VP', numero_patrimonio='PAT-VP',
                      localidade_id=loc.id, camara='CONGELADO')
    db.session.add(coletor)
    db.session.flush()

    item = ItemComplementar(empresa_id=mb.id, categoria_id=catalogo['headset'].id,
                            identificador='HS-047', coletor_id=coletor.id,
                            localidade_id=loc.id)
    db.session.add(item)
    db.session.commit()

    assert coletor.complementos_pareados == [item]
    assert item.coletor.patrimonio_display == coletor.patrimonio_display


# ---------------------------------------------------------------------------
# Isolamento por empresa (a classe de bug que o S1/S2/S3 caçou)
# ---------------------------------------------------------------------------

def test_catalogo_e_por_empresa(app, duas_empresas, catalogo):
    """Cada tenant vê o próprio catálogo — e o nome só é único DENTRO da empresa."""
    acme = duas_empresas.acme
    # Mesmo nome, outra empresa: tem de ser aceito.
    db.session.add(CategoriaComplemento(empresa_id=acme.id, nome='HEADSET',
                                        controle=COMPLEMENTO_UNIDADE))
    db.session.commit()

    da_mb = CategoriaComplemento.query.filter_by(empresa_id=duas_empresas.mb.id).all()
    da_acme = CategoriaComplemento.query.filter_by(empresa_id=acme.id).all()
    assert {c.nome for c in da_mb} == {'BATERIA COLETOR CLIMATIZADO',
                                       'BATERIA COLETOR SECO', 'HEADSET'}
    assert {c.nome for c in da_acme} == {'HEADSET'}


def test_estoque_e_por_localidade(app, mb, catalogo, loc):
    """D7 — o estoque responde 'quantas há NESTE CD', não um total solto."""
    db.session.add(EstoqueComplemento(empresa_id=mb.id,
                                      categoria_id=catalogo['climatizada'].id,
                                      localidade_id=loc.id, qtd_cadastrada=40))
    db.session.commit()

    linha = EstoqueComplemento.query.filter_by(categoria_id=catalogo['climatizada'].id,
                                               localidade_id=loc.id).first()
    assert linha.qtd_cadastrada == 40
    assert linha.localidade.sigla == loc.sigla


# ---------------------------------------------------------------------------
# As telas
# ---------------------------------------------------------------------------

def test_tela_lista_o_catalogo(admin_client, catalogo):
    """🔴 B1 — a tela de complementos deixou de listar BATERIA.

    Bateria é fungível e tem tela própria; headset e suporte têm patrimônio e
    ficam aqui. Misturar as duas foi o que obrigou a tela a perguntar "como você
    controla essas peças?" — uma decisão de modelagem empurrada para quem opera.
    """
    resposta = admin_client.get('/complementos')
    html = resposta.get_data(as_text=True)
    assert resposta.status_code == 200
    assert 'HEADSET' in html
    # Mira a LINHA da tabela, não o texto solto: o placeholder do campo de nome
    # também citava uma bateria, e a primeira versão deste teste acusou o
    # placeholder em vez da listagem. (O placeholder foi corrigido junto — exemplo
    # errado numa tela é pior do que exemplo nenhum.)
    for bateria in ('BATERIA COLETOR CLIMATIZADO', 'BATERIA COLETOR SECO'):
        assert f'>{bateria}<' not in html, f'{bateria} não sai desta tela'


def test_criar_categoria_pela_tela(admin_client, mb):
    admin_client.post('/complementos/categoria/criar', data={
        'nome': 'suporte veicular', 'controle': COMPLEMENTO_UNIDADE,
        'camara_atendida': '',
    }, follow_redirects=True)

    cat = CategoriaComplemento.query.filter_by(nome='SUPORTE VEICULAR').first()
    assert cat is not None, 'o nome é normalizado para maiúsculas, como no Discovery'
    assert cat.empresa_id == mb.id
    assert cat.por_unidade is True
    assert cat.camara_atendida is None


def test_camara_invalida_e_recusada(admin_client, mb):
    """O formulário é sugestão, não garantia: POST forjado não vira linha."""
    antes = CategoriaComplemento.query.count()
    admin_client.post('/complementos/categoria/criar',
                      data={'nome': 'Y', 'camara_atendida': 'TROPICAL'},
                      follow_redirects=True)
    assert CategoriaComplemento.query.count() == antes


def test_tipo_criado_nesta_tela_e_sempre_por_unidade(admin_client, mb):
    """🔴 B3 — a pergunta "como você controla?" saiu, e o POST não a ressuscita.

    A tela virou inventário de peças com patrimônio; o único tipo CONTADO é a
    bateria, e ela se cadastra na tela dela. Aceitar `controle` de volta pelo
    form seria criar por aqui um tipo sem lugar nenhum para informar quantidade.
    """
    admin_client.post('/complementos/categoria/criar',
                      data={'nome': 'CABO', 'controle': COMPLEMENTO_QUANTIDADE},
                      follow_redirects=True)

    cat = CategoriaComplemento.query.filter_by(nome='CABO').first()
    assert cat is not None
    assert cat.por_unidade is True, 'o controle vindo do form foi obedecido'
    assert cat.e_bateria is False


def test_peca_identificada_so_para_categoria_de_unidade(admin_client, catalogo):
    """Bateria é contada, não identificada — criar peça dela é incoerente."""
    admin_client.post('/complementos/item/criar', data={
        'categoria_id': catalogo['climatizada'].id, 'identificador': 'BAT-1',
    }, follow_redirects=True)

    assert ItemComplementar.query.filter_by(identificador='BAT-1').first() is None


def test_desativar_categoria_nao_apaga_historico(admin_client, catalogo, loc):
    """Toggle é reversível e não mexe no que já saiu — a cobrança sobrevive."""
    coletor = Coletor(serial_number='SN-HIST', numero_patrimonio='PAT-HIST',
                      localidade_id=loc.id)
    db.session.add(coletor)
    db.session.flush()
    mov = Movimentacao(coletor_id=coletor.id, re_colaborador='9002')
    db.session.add(mov)
    db.session.flush()
    db.session.add(MovimentacaoComplemento(movimentacao_id=mov.id,
                                           categoria_id=catalogo['seca'].id,
                                           qtd_saida=1, qtd_devolvida=0))
    db.session.commit()

    admin_client.post(f'/complementos/categoria/{catalogo["seca"].id}/toggle',
                      follow_redirects=True)

    assert db.session.get(CategoriaComplemento, catalogo['seca'].id).ativa is False
    pendencia = MovimentacaoComplemento.query.filter_by(movimentacao_id=mov.id).first()
    assert pendencia is not None and pendencia.pendente is True


def test_estoque_recusa_quantidade_negativa(admin_client, catalogo, loc):
    admin_client.post('/complementos/estoque', data={
        'categoria_id': catalogo['climatizada'].id, 'localidade_id': loc.id,
        'qtd_cadastrada': '-5',
    }, follow_redirects=True)
    assert EstoqueComplemento.query.count() == 0


def test_estoque_salva_e_converge(admin_client, catalogo, loc):
    """Gravar duas vezes atualiza a mesma linha, não cria uma segunda."""
    for qtd in ('40', '35'):
        admin_client.post('/complementos/estoque', data={
            'categoria_id': catalogo['climatizada'].id, 'localidade_id': loc.id,
            'qtd_cadastrada': qtd,
        }, follow_redirects=True)

    linhas = EstoqueComplemento.query.filter_by(
        categoria_id=catalogo['climatizada'].id, localidade_id=loc.id).all()
    assert len(linhas) == 1 and linhas[0].qtd_cadastrada == 35


# ---------------------------------------------------------------------------
# A promessa da fatia
# ---------------------------------------------------------------------------

def test_h1_nao_altera_o_fluxo_de_retirada(admin_client, catalogo, loc):
    """🔴 O guarda-corpo da promessa: a H1 é INVISÍVEL no balcão.

    A tela de movimentação é operada com leitor de código de barras e fila na
    frente. Esta fatia cadastra o catálogo e nada mais; qualquer campo novo no
    fluxo de retirada é da H2, e aparecer aqui significa que a fatia vazou.
    """
    from app.models import Colaborador
    db.session.add(Colaborador(re='7777', nome='Operador Teste',
                               empresa_id=loc.empresa_id))
    coletor = Coletor(serial_number='SN-BALCAO', numero_patrimonio='PAT-BALCAO',
                      localidade_id=loc.id, status='Disponível')
    db.session.add(coletor)
    db.session.commit()

    resposta = admin_client.post('/operacao/retirar', data={
        'busca_valor': 'PAT-BALCAO', 'busca_modo': 'patrimonio',
        're_colaborador': '7777',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
    }, follow_redirects=True)

    assert resposta.status_code == 200
    mov = Movimentacao.query.filter_by(coletor_id=coletor.id).first()
    assert mov is not None, 'a retirada existente deixou de funcionar'
    assert mov.complementos == [], 'a H1 não pode gravar complemento na retirada'


# ---------------------------------------------------------------------------
# Acabamento (feedback do Gabriel na primeira versão da tela)
# ---------------------------------------------------------------------------

def test_tela_sem_tipo_diz_o_que_fazer(admin_client, mb):
    """🔴 O que confundia na primeira versão: um select vazio, sem motivo.

    Com só bateria cadastrada, não há tipo de peça com patrimônio — e a tela
    precisa dizer isso e apontar o caminho, em vez de mostrar uma lista vazia
    e um botão que não abre nada.
    """
    db.session.add(CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                                        controle=COMPLEMENTO_QUANTIDADE,
                                        camara_atendida=COMPLEMENTO_CAMARA_SECO, e_bateria=True))
    db.session.commit()

    html = admin_client.get('/complementos').get_data(as_text=True)
    # Checa o QUE a tela comunica, não a frase exata: a copy foi reescrita duas
    # vezes, e um teste preso ao texto literal quebra a cada ajuste de linguagem
    # sem apontar defeito nenhum.
    assert 'Tipos de peça' in html, \
        'a tela precisa apontar onde se cadastra o tipo que falta'
    assert 'disabled' in html, \
        'sem tipo cadastrado, "Nova peça" não pode abrir um formulário vazio'

    # Com uma categoria de UNIDADE, o botão destrava.
    db.session.add(CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                        controle=COMPLEMENTO_UNIDADE))
    db.session.commit()
    html = admin_client.get('/complementos').get_data(as_text=True)
    assert 'HEADSET' in html


def test_tela_esta_no_menu(admin_client, catalogo):
    """Uma tela fora do menu é uma tela que ninguém encontra."""
    html = admin_client.get('/coletores').get_data(as_text=True)
    assert '/complementos' in html, 'o link do menu sumiu'


def test_pareamento_pode_ser_trocado_e_desfeito(admin_client, mb, catalogo, loc):
    """O par muda na operação real: o titular vai para manutenção e outro assume.

    Sem esta rota, trocar exigiria desativar a peça e recadastrar — perdendo o
    histórico dela por um motivo puramente operacional.
    """
    c1 = Coletor(serial_number='SN-P1', numero_patrimonio='PAT-P1', localidade_id=loc.id)
    c2 = Coletor(serial_number='SN-P2', numero_patrimonio='PAT-P2', localidade_id=loc.id)
    db.session.add_all([c1, c2])
    db.session.flush()
    item = ItemComplementar(empresa_id=mb.id, categoria_id=catalogo['headset'].id,
                            identificador='HS-100', coletor_id=c1.id, localidade_id=loc.id)
    db.session.add(item)
    db.session.commit()

    admin_client.post(f'/complementos/item/{item.id}/parear',
                      data={'coletor_id': c2.id}, follow_redirects=True)
    assert db.session.get(ItemComplementar, item.id).coletor_id == c2.id

    admin_client.post(f'/complementos/item/{item.id}/parear',
                      data={'coletor_id': ''}, follow_redirects=True)
    assert db.session.get(ItemComplementar, item.id).coletor_id is None


def test_tela_traduzida_nao_tem_texto_cru(admin_client, catalogo):
    """Paridade de i18n: a tela usa t(), então nenhuma chave pode vazar crua.

    Chave não encontrada faz o helper devolver a própria chave — o sintoma seria
    'complementos.titulo' aparecendo na tela em vez do rótulo.
    """
    html = admin_client.get('/complementos').get_data(as_text=True)
    assert 'complementos.' not in html, 'há chave de tradução não resolvida na tela'
    assert 'nav.complementos' not in html
