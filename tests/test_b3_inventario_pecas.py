"""
Testes da B3 (pacote Baterias) — a tela de complementos vira INVENTÁRIO.

Depois que a bateria ganhou tela própria (B1/B2), o que sobrou em
`/complementos` é o que ela deveria ter sido desde o começo: a lista das peças
com patrimônio, no mesmo desenho do inventário de coletores — filtro na URL e
seleção múltipla. As colunas ficam TODAS visíveis: o controle de colunas da U1
foi recusado na validação de 08/09, com a tela na frente.

Dois grupos de teste, e o segundo é o que a B2 ensinou a escrever:

    ESCRITA — a rota grava (ou recusa) o que deve.
    LEITURA DA TELA — a pessoa ENXERGA. Uma lista branca que não chega ao
    contexto do template faz o `{% for %}` não rodar: sobra o rótulo sem
    opções, e não há erro em lugar nenhum. Foi exatamente assim que os selos de
    aparência da B2 sumiram com a suíte verde.

O teste que dá nome à fatia é `test_peca_sem_cd_aparece_com_selo_proprio`: peça
sem CD é o buraco que a ação em lote existe para fechar, e ela some da tela se
alguém filtrar sem perceber.
"""
from datetime import datetime, timedelta

import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import rastreio_itens
from app.models import (CategoriaComplemento, Coletor, Colaborador, Empresa,
                        ItemComplementar, Movimentacao, MovimentacaoComplemento,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE)

# Base de tempo RELATIVA — data fixa faz o teste passar no dia em que foi
# escrito e quebrar sozinho dois dias depois (lição da H5).
AGORA = datetime.now()


@pytest.fixture()
def inventario(app, loc):
    """Um CD com dois headsets: um na prateleira, um ainda sem CD definido."""
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    hs_cat = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                  controle=COMPLEMENTO_UNIDADE)
    bat_cat = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                                   controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    db.session.add_all([hs_cat, bat_cat])
    db.session.flush()

    coletor = Coletor(serial_number='SN-B3', numero_patrimonio='PAT-B3',
                      localidade_id=loc.id, camara='SECO')
    colab = Colaborador(re='6100', nome='Bia Operadora', empresa_id=mb.id)
    db.session.add_all([coletor, colab])
    db.session.flush()

    no_cd = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                             identificador='HS-CD', localidade_id=loc.id)
    orfao = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                             identificador='HS-ORFAO')          # sem localidade
    db.session.add_all([no_cd, orfao])
    db.session.commit()
    return SimpleNamespace(mb=mb, loc=loc, hs_cat=hs_cat, bat_cat=bat_cat,
                           coletor=coletor, colab=colab, no_cd=no_cd, orfao=orfao)


def _emprestar(inv, item, horas_atras=1, devolvido=None, devolvida=0):
    """Manda a peça para a rua. `devolvido` fecha a movimentação sem a peça."""
    mov = Movimentacao(coletor_id=inv.coletor.id, re_colaborador=inv.colab.re,
                       colaborador_id=inv.colab.id,
                       data_saida=AGORA - timedelta(hours=horas_atras),
                       data_retorno=devolvido)
    db.session.add(mov)
    db.session.flush()
    db.session.add(MovimentacaoComplemento(
        movimentacao_id=mov.id, categoria_id=item.categoria_id, item_id=item.id,
        qtd_saida=1, qtd_devolvida=devolvida))
    db.session.commit()
    return mov


# ---------------------------------------------------------------------------
# A LISTA — o que a pessoa enxerga
# ---------------------------------------------------------------------------

def test_tela_lista_a_peca_com_a_situacao(admin_client, inventario):
    """O inventário diz onde a peça ESTÁ, não só que ela existe."""
    html = admin_client.get('/complementos').get_data(as_text=True)

    assert 'HS-CD' in html
    assert 'EM ESTOQUE' in html, 'a peça na prateleira precisa dizer que está lá'
    assert inventario.loc.sigla in html


def test_peca_fora_do_prazo_grita_na_linha_e_no_topo(admin_client, inventario):
    """Fora do prazo é dívida: marca a linha E aparece antes da lista.

    A marcação na linha só cobra quem rolar até ela — e o inventário pode ter
    centenas de peças. É o mesmo par (banner + linha) que a H4 entregou no
    inventário de coletores.
    """
    _emprestar(inventario, inventario.no_cd, horas_atras=48)     # prazo padrão: 24h
    html = admin_client.get('/complementos').get_data(as_text=True)

    assert 'FORA DO PRAZO' in html
    assert 'table-danger' in html, 'a linha da peça atrasada não está marcada'
    assert 'fora do prazo ou que não voltaram' in html, 'faltou o aviso no topo'


def test_peca_que_nao_voltou_continua_listada_como_fora(admin_client, inventario):
    """O coletor voltar não traz o headset de volta (regra da H5)."""
    _emprestar(inventario, inventario.no_cd, horas_atras=3, devolvido=AGORA)
    html = admin_client.get('/complementos').get_data(as_text=True)

    assert 'NÃO VOLTOU' in html
    assert 'HS-CD' in html


def test_peca_sem_cd_aparece_com_selo_proprio(admin_client, inventario):
    """🔴 O nome desta fatia: peça sem CD é problema, não campo em branco.

    Ela aparece na lista de TODOS os CDs enquanto não tiver lugar — e um traço
    cinza discreto não avisa ninguém. A B2 já tinha comprado esse defeito com a
    câmara nula.
    """
    html = admin_client.get('/complementos').get_data(as_text=True)

    assert 'HS-ORFAO' in html
    assert 'Sem CD' in html


def test_peca_inativa_aparece_no_inventario_mas_nao_no_rastreio(admin_client, inventario):
    """Peça desativada some do rastreio e FICA no inventário.

    🔴 Se sumisse dos dois, ninguém conseguiria reativá-la — e o caminho óbvio
    passaria a ser cadastrar uma peça nova com o mesmo patrimônio, perdendo o
    histórico da primeira.
    """
    inventario.no_cd.ativo = False
    db.session.commit()

    html = admin_client.get('/complementos').get_data(as_text=True)
    assert 'HS-CD' in html
    assert 'INATIVA' in html

    identificadores = [r['item'].identificador for r in rastreio_itens()]
    assert 'HS-CD' not in identificadores, 'peça aposentada não se cobra'


def test_bateria_nao_entra_no_inventario_de_pecas(admin_client, inventario):
    """A fronteira da B1 vale para a LISTAGEM, não só para o catálogo de tipos.

    Uma categoria marcada como bateria depois de já ter peças cadastradas
    deixaria itens órfãos nesta tela — e bateria não se lista uma a uma.
    """
    peca = ItemComplementar(empresa_id=inventario.mb.id,
                            categoria_id=inventario.bat_cat.id,
                            identificador='BAT-AVULSA', localidade_id=inventario.loc.id)
    db.session.add(peca)
    db.session.commit()

    html = admin_client.get('/complementos').get_data(as_text=True)
    assert 'BAT-AVULSA' not in html
    assert 'BATERIA SECO' not in html


# ---------------------------------------------------------------------------
# FILTROS — e o que eles NÃO podem fazer
# ---------------------------------------------------------------------------

def test_seletor_de_situacao_renderiza_todas_as_opcoes(admin_client, inventario):
    """🔴 Leitura de tela, não de rota: a lista branca precisa CHEGAR ao template.

    Se ela não chega, o `{% for %}` não roda, o `<select>` sai sem `<option>` e
    não há erro em lugar nenhum — nem no log, nem na tela, nem num teste que só
    olhasse o POST. Foi assim que os selos da B2 sumiram com a suíte verde.
    """
    html = admin_client.get('/complementos').get_data(as_text=True)
    for situacao in ('ESTOQUE', 'NO_PRAZO', 'ATRASADO', 'FALTOU', 'INATIVA'):
        assert f'value="{situacao}"' in html, f'a opção {situacao} não renderizou'

    # e os dois outros filtros, pelo mesmo motivo
    assert f'value="{inventario.loc.sigla}"' in html, 'o CD não virou opção'
    assert f'value="{inventario.hs_cat.id}"' in html, 'o tipo não virou opção'


def test_filtro_por_cd_estreita_a_lista(admin_client, inventario):
    """Com CD escolhido, entra o que está NAQUELE CD — e só."""
    html = admin_client.get(f'/complementos?localidade={inventario.loc.sigla}') \
        .get_data(as_text=True)

    assert 'HS-CD' in html
    assert 'HS-ORFAO' not in html, 'quem filtrou por um CD não pediu a peça sem CD'


def test_filtro_por_situacao_recorta(admin_client, inventario):
    _emprestar(inventario, inventario.no_cd, horas_atras=48)

    html = admin_client.get('/complementos?situacao=ATRASADO').get_data(as_text=True)
    assert 'HS-CD' in html
    assert 'HS-ORFAO' not in html


def test_filtro_de_cd_fora_do_escopo_nao_amplia(app, inventario):
    """🔴 O filtro só ESTREITA o que o T3 já permitiu (família C do S3).

    A sigla vem da query string. Resolvê-la contra o BANCO, em vez de contra as
    localidades já visíveis, deixaria quem opera um CD espiar o inventário de
    outro digitando a sigla na barra de endereços.

    O cenário precisa dos dois CDs na MESMA empresa e de um usuário preso a um
    deles: com empresas diferentes o filtro por tenant esconderia a peça sozinho,
    e o teste passaria sem provar nada sobre o escopo geográfico — foi o que
    aconteceu na primeira versão deste teste.
    """
    from werkzeug.security import generate_password_hash
    from app.models import Grupo, Localidade, Usuario

    distante = Localidade(sigla='ZZZ', nome='CD Distante', empresa_id=inventario.mb.id)
    db.session.add(distante)
    db.session.flush()
    db.session.add(ItemComplementar(empresa_id=inventario.mb.id,
                                    categoria_id=inventario.hs_cat.id,
                                    identificador='HS-ZZZ', localidade_id=distante.id))
    ti = Grupo.query.filter_by(nome='TI').first()
    preso = Usuario(nome='Preso ao CD', re='6199', email='preso@mb.com',
                    senha_hash=generate_password_hash('x', method='scrypt'),
                    grupo_id=ti.id, empresa_id=inventario.mb.id,
                    nivel_acesso='CD', localidade_id=inventario.loc.id)
    db.session.add(preso)
    db.session.commit()

    # Este usuário vai OPERAR o sistema, e no fluxo real ninguém opera sem ter
    # aceitado os termos. Quem exercita a guarda do aceite é
    # tests/test_l8_termos.py, com um usuário sem aceite de propósito.
    from app.termos import registrar_aceite
    registrar_aceite(preso)
    db.session.commit()

    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'], s['nome'], s['re'] = preso.id, preso.nome, preso.re
        s['nivel_acesso'], s['localidade_id'] = 'CD', inventario.loc.id

    resposta = c.get('/complementos?localidade=ZZZ')
    assert resposta.status_code == 200
    html = resposta.get_data(as_text=True)
    assert 'HS-ZZZ' not in html, 'a sigla na URL abriu um CD fora do escopo'
    assert 'HS-CD' in html or 'Nenhuma peça' in html


def test_todas_as_colunas_aparecem_sempre(admin_client, inventario):
    """🔴 Sem controle de colunas: as nove colunas estão lá desde o primeiro load.

    A U1 escondia as secundárias atrás de um botão. O Gabriel recusou o padrão
    na validação de 08/09, com a tela na frente: quem administra o parque quer
    ver tudo de uma vez. O teste trava a decisão — se alguém reintroduzir o
    controle, a coluna some do load inicial e isto quebra.
    """
    _emprestar(inventario, inventario.no_cd, horas_atras=2)

    html = admin_client.get('/complementos').get_data(as_text=True)
    assert 'Bia Operadora' in html, 'a coluna "com quem" não veio no load inicial'
    assert 'Acompanha' in html
    assert 'mais colunas' not in html, 'o controle de colunas voltou'


# ---------------------------------------------------------------------------
# AÇÃO EM LOTE — o caso real é em lote
# ---------------------------------------------------------------------------

def test_mover_em_lote_da_cd_as_pecas_marcadas(admin_client, inventario):
    """Chegou a caixa de headsets sem CD: todos vão para o mesmo lugar."""
    admin_client.post('/complementos/itens/mover', data={
        'item_ids': [inventario.orfao.id, inventario.no_cd.id],
        'localidade_destino': inventario.loc.id,
    }, follow_redirects=True)

    assert db.session.get(ItemComplementar, inventario.orfao.id).localidade_id \
        == inventario.loc.id


def test_mover_em_lote_recusa_destino_fora_do_escopo(cliente_logado, duas_empresas):
    """Destino inválido não move nada — nem 'quase tudo'."""
    du = duas_empresas
    cat = CategoriaComplemento(empresa_id=du.acme.id, nome='HEADSET B',
                               controle=COMPLEMENTO_UNIDADE)
    db.session.add(cat)
    db.session.flush()
    peca = ItemComplementar(empresa_id=du.acme.id, categoria_id=cat.id,
                            identificador='HS-B', localidade_id=du.locB.id)
    db.session.add(peca)
    db.session.commit()

    cliente_logado(du.userB).post('/complementos/itens/mover', data={
        'item_ids': [peca.id], 'localidade_destino': du.locA.id,
    }, follow_redirects=True)

    assert db.session.get(ItemComplementar, peca.id).localidade_id == du.locB.id, \
        'a peça foi parar num CD de outra empresa'


def test_mover_em_lote_recusa_peca_de_outra_empresa(cliente_logado, duas_empresas):
    """🔴 Origem também é validada — só o destino deixaria puxar peça alheia.

    Num POST em massa a tentação é maior: os ids chegam em lista, e conferir só
    o destino é a família B do S2 servida em bandeja.

    A peça alheia é de propósito uma peça SEM CD: com localidade preenchida, a
    guarda geográfica barraria sozinha e o teste passaria mesmo sem a checagem
    de empresa. Sem CD, só o tenant protege — e é justo a peça órfã que fica
    visível para todo mundo.
    """
    du = duas_empresas
    cat = CategoriaComplemento(empresa_id=du.mb.id, nome='HEADSET A',
                               controle=COMPLEMENTO_UNIDADE)
    db.session.add(cat)
    db.session.flush()
    alheia = ItemComplementar(empresa_id=du.mb.id, categoria_id=cat.id,
                              identificador='HS-A', localidade_id=None)
    db.session.add(alheia)
    db.session.commit()

    cliente_logado(du.userB).post('/complementos/itens/mover', data={
        'item_ids': [alheia.id], 'localidade_destino': du.locB.id,
    }, follow_redirects=True)

    assert db.session.get(ItemComplementar, alheia.id).localidade_id is None, \
        'peça de outra empresa foi movida para dentro do tenant'


# ---------------------------------------------------------------------------
# O PAREAMENTO SAIU DAQUI (e vai para o detalhe do coletor, na B4)
# ---------------------------------------------------------------------------

def test_pareamento_nao_e_mais_decidido_nesta_tela(admin_client, inventario):
    """Vincular peça a coletor é decisão sobre AQUELE coletor, não sobre o catálogo.

    A coluna vira leitura — e leitura secundária, atrás do controle de colunas,
    porque só o headset de voice picking é pareado e a coluna vinha vazia na
    maioria das linhas. O `<select name="coletor_id">` não pode sobrar em lugar
    nenhum, nas duas visões.
    """
    inventario.no_cd.coletor_id = inventario.coletor.id
    db.session.commit()

    for url in ('/complementos', '/complementos?colunas=tudo'):
        html = admin_client.get(url).get_data(as_text=True)
        assert 'name="coletor_id"' not in html, \
            f'o seletor de pareamento continua na tela ({url})'

    completo = admin_client.get('/complementos?colunas=tudo').get_data(as_text=True)
    assert inventario.coletor.patrimonio_display in completo, \
        'a tela deixou de mostrar com qual coletor a peça anda'


def test_criar_peca_ignora_pareamento_enviado_pelo_form(admin_client, inventario):
    """O campo saiu do formulário; um POST forjado não o traz de volta."""
    admin_client.post('/complementos/item/criar', data={
        'categoria_id': inventario.hs_cat.id, 'identificador': 'HS-NOVO',
        'coletor_id': inventario.coletor.id,
    }, follow_redirects=True)

    nova = ItemComplementar.query.filter_by(identificador='HS-NOVO').first()
    assert nova is not None, 'a peça precisa ser criada mesmo assim'
    assert nova.coletor_id is None


def test_rota_de_parear_continua_de_pe(admin_client, inventario):
    """A B4 vai chamar esta rota do detalhe do coletor — ela não pode ter morrido."""
    admin_client.post(f'/complementos/item/{inventario.no_cd.id}/parear',
                      data={'coletor_id': inventario.coletor.id}, follow_redirects=True)

    assert db.session.get(ItemComplementar, inventario.no_cd.id).coletor_id \
        == inventario.coletor.id


# ---------------------------------------------------------------------------
# Acabamento
# ---------------------------------------------------------------------------

def test_uma_consulta_serve_a_listagem_inteira(app, inventario):
    """Anti-N+1: o template não pode voltar ao banco por linha.

    `patrimonio_display` toca `coletor.localidade` — foi assim que a H4 escondeu
    um N+1 atrás de uma property, e só o teste pegou.
    """
    from sqlalchemy import event

    _emprestar(inventario, inventario.no_cd, horas_atras=2)
    inventario.orfao.coletor_id = inventario.coletor.id
    db.session.commit()

    linhas = rastreio_itens(incluir_inativos=True)
    consultas = []
    engine = db.session.get_bind()

    def ouvir(*args, **kwargs):
        consultas.append(1)

    event.listen(engine, 'before_cursor_execute', ouvir)
    try:
        for r in linhas:                          # o que o template faz por linha
            item = r['item']
            _ = (item.identificador, item.ativo, r['categoria'], r['estado'])
            _ = item.localidade.sigla if item.localidade else None
            _ = item.categoria.camara_atendida if item.categoria else None
    finally:
        event.remove(engine, 'before_cursor_execute', ouvir)

    assert consultas == [], f'{len(consultas)} consulta(s) extra(s) — o joinedload caiu'


def test_tela_traduzida_nao_tem_texto_cru(admin_client, inventario):
    """Chave não encontrada volta como a própria chave — o sintoma é visível."""
    html = admin_client.get('/complementos?colunas=tudo').get_data(as_text=True)
    assert 'complementos.' not in html
    assert 'rastreio.' not in html
    assert 'label.' not in html
