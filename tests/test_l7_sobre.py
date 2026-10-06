"""
A página Sobre: autoria, licença e componentes.

🔴 O teste que dá nome ao arquivo é `test_a_autoria_esta_na_tela`.

Esta página existe por um motivo que não é técnico. O MBAssets é obra de uma
pessoa, cedida para uso de uma empresa — e as duas coisas precisam estar
escritas DENTRO do sistema, não só num contrato que ninguém abre. Software sem
autoria declarada vira, com o tempo, software de quem estiver usando: é assim
que alguém acaba afirmando que o trabalho sempre foi da casa.

Um teste não impede isso. O que ele impede é o nome sumir da tela numa
refatoração distraída, sem ninguém perceber.
"""
import re

import pytest

from app import sobre


def test_a_autoria_esta_na_tela(admin_client):
    """🔴 O nome do arquivo: quem fez precisa estar escrito no sistema.

    🔴 A primeira versão deste teste fazia `assert sobre.AUTOR in html` — e
    passava com `AUTOR = ''`, porque string vazia está contida em qualquer
    string. Esvaziar o nome do autor é exatamente o estrago que este arquivo
    existe para impedir, e o teste aprovava. O nome vai literal aqui.
    """
    html = admin_client.get('/sobre').get_data(as_text=True)

    assert sobre.AUTOR.strip(), 'o nome do autor ficou em branco no módulo'
    assert 'Gabriel Garcia de Carvalho' in html, \
        'o nome do autor não aparece na página Sobre'
    assert 'detém os direitos' in html, 'a titularidade não está declarada'


def test_a_licenca_diz_o_que_NAO_esta_concedido(admin_client):
    """Licença que só diz o que pode é um convite a supor o resto.

    A parte que protege é a que delimita: revender, sublicenciar e apresentar
    como obra própria não estão incluídos.
    """
    html = admin_client.get('/sobre').get_data(as_text=True)

    assert 'não está concedido' in html.lower() or 'nao esta concedido' in html.lower()
    for palavra in ('Revender', 'sublicenciar', 'obra própria'):
        assert palavra in html, f'a licença não fala sobre {palavra!r}'


def test_a_licenca_separa_este_produto_dos_outros(admin_client):
    """🔴 A cláusula que existe por causa da história deste projeto.

    Receber este sistema não cria direito sobre nenhum outro software do mesmo
    autor, ainda que se pareçam — e eles se parecem, porque vieram da mesma
    cabeça e resolvem problemas vizinhos.
    """
    html = admin_client.get('/sobre').get_data(as_text=True)

    assert 'não cria direito algum sobre qualquer outro software' in html


def test_a_versao_bate_com_a_da_tela_de_saude(admin_client):
    """As duas leem o mesmo arquivo. Se divergirem, uma delas está mentindo —
    e quem for conferir se a atualização foi aplicada não vai saber em qual
    acreditar."""
    from app import ROOT_DIR
    from app.saude import versao_instalada

    html = admin_client.get('/sobre').get_data(as_text=True)

    assert versao_instalada(ROOT_DIR) in html


def test_os_componentes_estao_listados_com_licenca(admin_client):
    """🔴 A pergunta que o time de segurança sempre faz: "o que roda aí dentro?".

    Chegar com a resposta pronta é a diferença entre uma conversa de cinco
    minutos e um processo de aprovação.
    """
    html = admin_client.get('/sobre').get_data(as_text=True)

    for nome in ('Flask', 'SQLAlchemy', 'Bootstrap', 'Waitress', 'SQLite'):
        assert nome in html, f'{nome} não está na lista de componentes'
    for licenca in ('MIT', 'BSD-3-Clause'):
        assert licenca in html, f'a licença {licenca} não aparece'


def test_todo_componente_declara_licenca():
    """Componente sem licença declarada é o que trava a aprovação."""
    for nome, versao, licenca, para_que in sobre.COMPONENTES:
        assert licenca and licenca.strip(), f'{nome} sem licença'
        assert para_que and para_que.strip(), f'{nome} sem explicação de uso'


def test_a_lista_de_componentes_bate_com_o_que_esta_instalado():
    """🔴 Lista escrita à mão envelhece em silêncio.

    Se uma dependência for atualizada no requirements e ninguém lembrar daqui, a
    página passa a declarar uma versão que não é a que roda — e aí ela é pior
    que não existir, porque alguém vai confiar nela.
    """
    import importlib.metadata as md

    declarados = {nome: versao for nome, versao, _l, _p in sobre.COMPONENTES}
    divergentes = []
    for nome, versao in declarados.items():
        try:
            real = md.version(nome)
        except md.PackageNotFoundError:
            continue          # Bootstrap, Chart.js e SQLite não são pacotes pip
        if real != versao:
            divergentes.append(f'{nome}: página diz {versao}, instalado {real}')

    assert not divergentes, '; '.join(divergentes)


def test_a_pagina_abre_para_qualquer_um(app, cliente_logado, duas_empresas):
    """De quem é o trabalho é informação de todos, não só da TI."""
    from app import db
    from app.models import Grupo

    balcao = Grupo.query.filter_by(nome='BALCAO').first()
    duas_empresas.userA.grupo_id = balcao.id
    db.session.commit()

    assert cliente_logado(duas_empresas.userA).get('/sobre').status_code == 200


def test_a_pagina_diz_o_que_o_sistema_faz(admin_client):
    """Quem chega novo precisa entender o alcance sem abrir o manual."""
    html = admin_client.get('/sobre').get_data(as_text=True)

    for _icone, titulo, _desc in sobre.CONTROLES:
        assert titulo in html, f'o controle {titulo!r} não aparece'


def test_nao_promete_suporte_nem_garantia(admin_client):
    """🔴 A entrega é gratuita e sem obrigação de manutenção.

    Deixar isso implícito é como a expectativa de suporte vitalício nasce.
    """
    html = admin_client.get('/sobre').get_data(as_text=True)

    assert 'estado em que se encontra' in html
    assert re.search(r'não há garantia', html, re.I)
