"""
Testes da U4 — o vocabulário do sistema.

A barra do topo dizia `Contexto: Todos os CDs (Global)` com um selo verde
`GLOBAL`. Isso é arquitetura multi-tenant na cara de quem só quer ver o CD dele,
em 100% das telas. E a palavra "contexto" repetia em 18 mensagens.

O teste que dá nome à fatia é `test_nenhuma_mensagem_usa_vocabulario_interno`:
inspeciona o CÓDIGO, não uma tela. A frase pode voltar em qualquer uma das ~440
mensagens do sistema, e checar telas específicas deixaria as outras escapando.
"""
import io
import pathlib
import re

import pytest

# Palavras que descrevem como o sistema É CONSTRUÍDO, não o que a pessoa faz.
# `escopo` e `contexto` entram porque são os nomes internos do recorte por
# empresa e por geografia — quem opera o CD chama isso de "empresa" e "CD".
JARGAO = ['contexto', 'escopo', 'tenant', 'payload', 'endpoint',
          'blueprint', 'commit', 'rollback', 'query']

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def _mensagens_de_tela():
    """(arquivo, linha, texto) de cada flash() do projeto."""
    for caminho in sorted((RAIZ / 'app').rglob('*.py')):
        for n, linha in enumerate(io.open(caminho, encoding='utf-8'), 1):
            if 'flash(' in linha:
                yield caminho.relative_to(RAIZ), n, linha.strip()


def test_nenhuma_mensagem_usa_vocabulario_interno():
    """🔴 O usuário lê a mensagem procurando o que fazer, não o nosso modelo.

    "Selecione o contexto de uma empresa" nomeava um conceito sem dizer onde se
    resolve. Virou "Escolha uma empresa no topo da tela" — que aponta o lugar.
    """
    ofensores = []
    for arquivo, n, texto in _mensagens_de_tela():
        for palavra in JARGAO:
            if re.search(rf'\b{palavra}', texto, re.I):
                ofensores.append(f'{arquivo}:{n} — {texto[:80]}')
                break
    assert not ofensores, 'mensagem com vocabulário interno:\n  ' + '\n  '.join(ofensores)


def test_o_guarda_realmente_pega():
    """Guarda que nunca acusa não é guarda.

    Sem isto, um erro no regex deixaria o teste acima verde para sempre — e ele
    é a única coisa que separa a varredura de hoje de o jargão voltar amanhã.
    """
    exemplo = "        flash('Selecione o contexto de uma empresa.', 'warning')"
    assert any(re.search(rf'\b{p}', exemplo, re.I) for p in JARGAO)


def test_a_barra_do_topo_nao_mostra_o_enum(admin_client, loc):
    """O selo imprimia `GLOBAL` cru — o valor da coluna, na tela."""
    html = admin_client.get('/').get_data(as_text=True)
    assert '>\n        GLOBAL\n      <' not in html
    assert 'Contexto:' not in html
    assert 'Vendo' in html
    assert 'Seu acesso' in html


def test_a_barra_mantem_a_distincao_que_importa(admin_client, loc):
    """Ela diz duas coisas diferentes: ATÉ ONDE a pessoa pode ver (o selo) e o
    que ela está vendo AGORA (o seletor). A U4 traduz sem fundir as duas."""
    html = admin_client.get('/').get_data(as_text=True)
    assert 'todos os CDs' in html                    # o alcance, em português
    assert 'Todos os CDs' in html                    # a opção do seletor


# ---------------------------------------------------------------------------
# o glossário
# ---------------------------------------------------------------------------

def test_glossario_abre_para_qualquer_um(cliente_logado, duas_empresas):
    """Sem permissão específica: um glossário que só alguns abrem falha
    justamente com quem mais precisa dele."""
    r = cliente_logado(duas_empresas.userB).get('/glossario')
    assert r.status_code == 200


def test_glossario_explica_o_que_nao_da_para_traduzir(admin_client):
    """"Coletor", "câmara" e "RE" são o vocabulário REAL do CD —
    traduzi-los deixaria a tela mais estranha. O glossário é a saída."""
    html = admin_client.get('/glossario').get_data(as_text=True)
    for termo in ('Coletor', 'Câmara', 'RE', 'Patrimônio', 'Complemento', 'Em falta', 'Fora do prazo'):
        assert termo in html, f'{termo} ficou de fora do glossário'


def test_glossario_esta_no_menu(admin_client, loc):
    html = admin_client.get('/').get_data(as_text=True)
    assert '/glossario' in html


@pytest.mark.parametrize('idioma', ['pt', 'en', 'es'])
def test_os_tres_idiomas_tem_as_chaves_novas(idioma):
    import json
    with io.open(RAIZ / 'translations' / f'{idioma}.json', encoding='utf-8') as f:
        textos = json.load(f)
    for chave in ('ctx.vendo', 'ctx.alcance', 'ctx.nivel.GLOBAL',
                  'glossario.titulo', 'nav.glossario'):
        assert chave in textos, f'{idioma} sem {chave}'
    assert 'ctx.contexto' not in textos, 'a chave antiga deveria ter saído'
