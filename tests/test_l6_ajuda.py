"""
O manual dentro do sistema.

🔴 O teste que dá nome ao arquivo é `test_toda_tela_mapeada_existe_de_verdade`.

O "?" de cada tela é ligado por um mapa de endpoint → seção. Um endpoint
escrito errado nesse mapa não quebra nada: o botão simplesmente não aparece
naquela tela, para sempre, e ninguém nota — porque quem usa a tela não sabe que
deveria haver um "?" ali. É o tipo de erro que só aparece quando alguém procura
ajuda, trava, e desiste.

O mesmo vale para o contrário: uma seção referenciada que não existe levaria o
"?" para uma página sem o conteúdo prometido.
"""
import pytest

from app.ajuda import AJUDA_POR_TELA, SECOES, secao_da_tela, secao_por_id


def test_toda_tela_mapeada_existe_de_verdade(app):
    """🔴 O nome do arquivo: endpoint errado = botão que nunca aparece."""
    reais = {regra.endpoint for regra in app.url_map.iter_rules()}

    inventados = sorted(set(AJUDA_POR_TELA) - reais)

    assert not inventados, (
        f'{len(inventados)} endpoint(s) no mapa de ajuda não existem: '
        f'{inventados}')


def test_toda_secao_apontada_existe(app):
    """Um '?' que leva a uma âncora inexistente abre o manual no lugar errado."""
    ids = {s['id'] for s in SECOES}

    orfas = sorted({v for v in AJUDA_POR_TELA.values()} - ids)

    assert not orfas, f'seções apontadas e inexistentes: {orfas}'


def test_as_telas_que_as_pessoas_mais_usam_tem_ajuda(app):
    """O balcão é onde alguém trava com fila na frente.

    Sem esta lista, o mapa poderia ficar vazio e os dois testes acima
    continuariam verdes.
    """
    essenciais = [
        'movimentacao.operacao_index',      # retirada e devolução
        'dashboard.index',                  # a tela inicial
        'coletores.gerenciar_coletores',    # inventário
        'dashboard.gerenciar_usuarios',     # dar acesso a alguém
    ]
    for endpoint in essenciais:
        assert secao_da_tela(endpoint), f'{endpoint} ficou sem ajuda'


def test_cada_secao_tem_conteudo_de_verdade():
    """Seção com título e sem passo é promessa não cumprida."""
    for secao in SECOES:
        assert secao['itens'], f'seção {secao["id"]} está vazia'
        for item in secao['itens']:
            assert item['passos'], f'{secao["id"]}/{item["titulo"]} sem passos'
            assert item['situacao'], f'{secao["id"]}/{item["titulo"]} sem situação'


def test_o_manual_fala_do_imprevisto_e_nao_so_do_caminho_feliz():
    """🔴 O caminho feliz qualquer um descobre clicando.

    O que trava as pessoas é o imprevisto, e é por ele que elas procuram o
    manual. Um manual só com o passo a passo ideal não é consultado duas vezes.
    """
    com_problemas = sum(
        1 for s in SECOES for i in s['itens'] if i.get('problemas'))
    total = sum(len(s['itens']) for s in SECOES)

    assert com_problemas >= total * 0.6, (
        f'só {com_problemas} de {total} itens dizem o que fazer quando dá errado')


def test_a_pagina_abre_para_qualquer_um(app, cliente_logado, duas_empresas):
    """Ajuda que só alguns podem abrir falha com quem mais precisa dela.

    Mesma decisão do glossário.
    """
    from app.models import Grupo
    from app import db

    balcao = Grupo.query.filter_by(nome='BALCAO').first()
    duas_empresas.userA.grupo_id = balcao.id
    db.session.commit()

    resposta = cliente_logado(duas_empresas.userA).get('/ajuda')

    assert resposta.status_code == 200


def test_a_pagina_mostra_as_secoes(admin_client):
    html = admin_client.get('/ajuda').get_data(as_text=True)

    for secao in SECOES:
        assert secao['titulo'] in html, f'{secao["id"]} não apareceu na página'


def test_o_atalho_da_tela_leva_a_secao_certa(admin_client):
    """O '?' manda ?secao=<id>; a página tem de reconhecer e rolar até lá."""
    html = admin_client.get('/ajuda?secao=balcao').get_data(as_text=True)

    assert 'scrollIntoView' in html, 'a página não rola até a seção pedida'
    assert '"balcao"' in html, 'a seção pedida não chegou ao script'


def test_secao_inventada_na_url_nao_quebra_a_pagina(admin_client):
    """Link velho ou URL digitada à mão não pode dar erro."""
    resposta = admin_client.get('/ajuda?secao=nao-existe')

    assert resposta.status_code == 200
    assert 'scrollIntoView' not in resposta.get_data(as_text=True)


def test_o_botao_de_ajuda_aparece_na_tela_do_balcao(admin_client):
    """🔴 O POST verde não prova que o usuário enxerga o botão.

    O '?' vem do context processor; se ele parar de injetar a variável, o
    `{% if %}` do template simplesmente não renderiza nada — sem erro, sem
    aviso, e o atalho desaparece do sistema inteiro de uma vez.
    """
    html = admin_client.get('/operacao').get_data(as_text=True)

    assert '/ajuda?secao=balcao' in html, 'o botão de ajuda sumiu da tela'


def test_tela_sem_secao_nao_mostra_botao_quebrado(admin_client):
    """Tela fora do mapa não ganha um '?' que não leva a lugar nenhum."""
    assert secao_da_tela('dashboard.glossario') is None
    html = admin_client.get('/glossario').get_data(as_text=True)

    assert 'ajuda.sobre_esta_tela' not in html
    assert '/ajuda?secao=' not in html


def test_secao_por_id_desconhecida_devolve_nada():
    assert secao_por_id('inventada') is None
