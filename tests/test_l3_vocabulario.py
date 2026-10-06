"""
A unidade física tem UM nome na tela: CD (DC em inglês).

🔴 Antes da L3 o mesmo lugar tinha quatro nomes conviventes: a barra dizia "Todos
os CDs", o menu dizia "Localidades", as colunas diziam "Local", e o reset de
senha falava em "unidade". Em inglês eram "DC", "Location" e "site"; em espanhol,
"CD", "Localidad" e "ubicación". Ninguém escolheu isso — cada tela herdou o
vocabulário da tela de onde foi copiada, e a divergência passou despercebida
porque nenhum teste lê o que está escrito.

Este arquivo é a trava. Ele não cuida de código: `Localidade` continua sendo o
nome do model, do relationship e dos parâmetros, e deve continuar. O que ele
defende é o que a PESSOA lê.
"""
import io
import json
import os
import re

import pytest

from app import ROOT_DIR

# Termo proibido por idioma, e o motivo de cada exceção.
PROIBIDOS = {
    'pt': r'localidad|\blocal\b|\bunidade\b',
    'en': r'\blocation|\bsites?\b',
    'es': r'localidad|ubicaci|\blocal\b',
}

# Exceções com justificativa. Entrada nova aqui pede uma razão de verdade —
# "é mais fácil" não é razão.
EXCECOES = {
    # "On-Site" aqui quer dizer "em campo, na hora", não o CD. O pt é
    # "Cadastrar Colaborador em Campo".
    ('en', 'movimentacao.modal_cadastro_titulo'),
}


def _textos(lang):
    caminho = os.path.join(ROOT_DIR, 'translations', f'{lang}.json')
    return json.load(io.open(caminho, encoding='utf-8'))


@pytest.mark.parametrize('lang', ['pt', 'en', 'es'])
def test_a_unidade_fisica_se_chama_cd(lang):
    """🔴 O nome do arquivo: um nome só para a mesma coisa, em cada idioma."""
    padrao = re.compile(PROIBIDOS[lang], re.I)
    achados = {
        k: v for k, v in _textos(lang).items()
        if padrao.search(str(v)) and (lang, k) not in EXCECOES
    }

    assert not achados, (
        f'{lang}: termo antigo para CD em {len(achados)} chave(s). '
        f'Use CD (pt/es) ou DC (en), ou justifique em EXCECOES: '
        + '; '.join(f'{k}={v!r}' for k, v in sorted(achados.items())[:5])
    )


@pytest.mark.parametrize('lang,esperado', [('pt', 'CD'), ('en', 'DC'), ('es', 'CD')])
def test_o_termo_novo_esta_la(lang, esperado):
    """O contraponto: proibir o termo antigo sem ter o novo esvaziaria a tela.

    Sem este teste, apagar o rótulo satisfaria o teste acima.
    """
    textos = _textos(lang)
    assert textos['label.localidade'] == esperado
    assert esperado in textos['nav.localidades']


def test_as_tres_linguas_tem_as_mesmas_chaves():
    """Paridade — mexer em vocabulário é quando se perde uma chave de vista."""
    chaves = {lang: set(_textos(lang)) for lang in ('pt', 'en', 'es')}
    assert chaves['pt'] == chaves['en'], sorted(chaves['pt'] ^ chaves['en'])[:8]
    assert chaves['pt'] == chaves['es'], sorted(chaves['pt'] ^ chaves['es'])[:8]
