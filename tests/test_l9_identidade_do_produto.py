"""
O produto não carrega o nome de nenhum cliente.

🔴 Este arquivo existe por causa de um achado do Gabriel: o sistema se chamava
"MBAssets", e "MB" é a marca do cliente. Um produto que leva o nome de quem o
recebe é um produto que parece ter sido feito para ele, com recursos dele — e
isso vira argumento contra quem o escreveu.

Procurando o nome antigo, apareceu coisa pior: `app/startup.py` cravava
`NOME_EMPRESA = 'Martin Brower'`. Isso estava no CÓDIGO-FONTE, que é entregue
junto. O nome real da empresa passou a ser informado na instalação.

O teste não impede a decisão de voltar atrás. Impede o retorno por descuido —
um exemplo copiado de um teste antigo, um seed de demonstração.
"""
import os
import re

import pytest

from app import ROOT_DIR
from app.sobre import NOME
from app.startup import NOME_EMPRESA

# Nomes de cliente que já estiveram no código e não podem voltar.
CLIENTES = [r'Martin\s*Brower', r'\bMBAssets\b', r'\bmbassets\b']

_IGNORAR = {'venv', '.git', '__pycache__', 'dist', '.pytest_cache',
            'instance', 'uploads', 'backups', 'logs', '.claude'}
_EXTENSOES = ('.py', '.html', '.json', '.txt', '.bat', '.sh', '.md')


def _arquivos():
    for pasta, dirs, arquivos in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in _IGNORAR]
        for nome in arquivos:
            if nome.endswith(_EXTENSOES):
                yield os.path.join(pasta, nome)


def test_nenhum_nome_de_cliente_no_codigo():
    """🔴 O nome do arquivo."""
    padroes = [re.compile(p, re.IGNORECASE) for p in CLIENTES]
    achados = []
    for caminho in _arquivos():
        try:
            with open(caminho, encoding='utf-8') as fh:
                texto = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        # Este próprio arquivo cita os nomes para poder proibi-los.
        if os.path.basename(caminho) == os.path.basename(__file__):
            continue
        for padrao in padroes:
            for achado in padrao.finditer(texto):
                achados.append(f'{os.path.relpath(caminho, ROOT_DIR)}: {achado.group()!r}')

    assert not achados, (
        f'{len(achados)} menção(ões) a nome de cliente no código:\n  '
        + '\n  '.join(achados[:15]))


def test_a_empresa_padrao_e_generica():
    """O produto nasce sem dizer de quem é — o nome real entra na instalação."""
    assert NOME_EMPRESA == 'Minha Empresa'


def test_o_produto_se_chama_lastro():
    """O contraponto: proibir os nomes antigos sem ter um novo deixaria o
    sistema sem nome nenhum, e os testes acima passariam."""
    assert NOME == 'Lastro'


def test_o_primeiro_acesso_pergunta_o_nome_da_empresa(client, app):
    """Sem perguntar, a instalação fica "Minha Empresa" para sempre."""
    from app import db
    from app.helpers import preparar_primeiro_acesso
    from app.models import Usuario

    Usuario.query.delete()
    db.session.commit()
    token = preparar_primeiro_acesso()

    html = client.get(f'/primeiro-acesso/{token}').get_data(as_text=True)

    assert 'name="empresa"' in html, 'a tela não pergunta o nome da empresa'


def test_o_nome_informado_vira_o_nome_da_empresa(client, app):
    """🔴 Perguntar e ignorar é pior que não perguntar."""
    from app import db
    from app.helpers import preparar_primeiro_acesso
    from app.models import Empresa, Usuario

    Usuario.query.delete()
    db.session.commit()
    token = preparar_primeiro_acesso()

    client.post(f'/primeiro-acesso/{token}',
                data={'empresa': 'Logística Central Ltda', 'nome': 'Joana',
                      'email': 'joana@empresa.com', 'senha': 'escolhida1',
                      'senha_confirmacao': 'escolhida1', 'aceito': 'sim'})

    assert Empresa.query.order_by(Empresa.id).first().nome == 'Logística Central Ltda'


def test_empresa_em_branco_nao_apaga_o_nome(app):
    """Chamada sem o campo (ou com espaços) mantém o que estava."""
    from app import db
    from app.helpers import criar_administrador
    from app.models import Empresa, Usuario

    Usuario.query.delete()
    db.session.commit()
    antes = Empresa.query.order_by(Empresa.id).first().nome

    criar_administrador('Joana', 'j@x.com', 'escolhida1', nome_empresa='   ')

    assert Empresa.query.order_by(Empresa.id).first().nome == antes


# ---------------------------------------------------------------------------
# O NOME DA EMPRESA NA TELA
# ---------------------------------------------------------------------------

def test_o_nome_da_empresa_aparece_no_cabecalho(app, cliente_logado, duas_empresas):
    """🔴 A dica do campo PROMETE que aparece. Até aqui, não aparecia.

    Perguntar o nome da empresa no primeiro acesso, guardar no banco e nunca
    mostrar é pior que não perguntar: quem digitou fica procurando onde foi
    parar, e a próxima coisa que faz é desconfiar do resto.
    """
    from app import db

    du = duas_empresas
    du.mb.nome = 'Logística Central Ltda'
    db.session.commit()

    html = cliente_logado(du.userA).get('/').get_data(as_text=True)

    assert 'Logística Central Ltda' in html, 'o nome da empresa não chegou à tela'


def test_a_pagina_sobre_identifica_a_instalacao(app, cliente_logado, duas_empresas):
    """"De quem é esta instalação" é parte dos créditos, não enfeite.

    🔴 Procura o RÓTULO, não só o nome: o nome da empresa aparece no cabeçalho
    de toda página, inclusive desta. A primeira versão deste teste passava com o
    bloco do Sobre removido — descobri sabotando. Um teste que o cabeçalho
    satisfaz não está testando o Sobre.
    """
    import io as _io
    import json as _json

    from app import ROOT_DIR, db

    du = duas_empresas
    du.mb.nome = 'Logística Central Ltda'
    db.session.commit()

    with _io.open(f'{ROOT_DIR}/translations/pt.json', encoding='utf-8') as fh:
        rotulo = _json.load(fh)['sobre.instalado_em']

    html = cliente_logado(du.userA).get('/sobre').get_data(as_text=True)

    assert rotulo in html, 'a página Sobre não diz de quem é a instalação'
    # o rótulo e o nome, juntos, no mesmo trecho
    pedaco = html[html.index(rotulo):html.index(rotulo) + 200]
    assert 'Logística Central Ltda' in pedaco


def test_a_dica_do_campo_nao_promete_o_que_nao_existe(app):
    """🔴 A dica dizia que o nome aparece "nos documentos". O sistema não gera
    documento nenhum — essa parte era promessa vazia, e promessa vazia na tela
    de instalação é o primeiro motivo para alguém parar de ler as outras."""
    import io
    import json

    from app import ROOT_DIR

    with io.open(f'{ROOT_DIR}/translations/pt.json', encoding='utf-8') as fh:
        dica = json.load(fh)['primeiro.empresa_dica']

    assert 'documento' not in dica.lower(), f'promete o que não existe: {dica!r}'
    assert 'cabeçalho' in dica.lower()
