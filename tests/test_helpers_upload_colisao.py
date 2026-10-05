"""
Colisão de nome em uploads/fotos.

O nome do arquivo era gerado só com prefixo+timestamp, então duas chamadas com
o MESMO prefixo dentro do MESMO segundo (duplo clique, duas abas, corrida entre
requisições) colidiam no nome. Não há lógica de "apagar o anterior" — a colisão
vira sobrescrita silenciosa: o segundo save() apaga o conteúdo do primeiro, e
os dois registros de banco que referenciam o filename ficam apontando pro mesmo
arquivo, que só tem o conteúdo de um deles.

datetime.now() é congelado (mesma resolução de segundo) para os dois saves —
sem o sufixo aleatório, os dois produziriam o MESMO filename.
"""
import io
import os
from datetime import datetime

import pytest
from werkzeug.datastructures import FileStorage

from app.helpers import salvar_foto

PNG_A = b'\x89PNG\r\n\x1a\n' + b'\x00' * 50
PNG_B = b'\x89PNG\r\n\x1a\n' + b'\x11' * 50


class _RelogioParado(datetime):
    """now() sempre devolve o mesmo instante — simula duas chamadas no mesmo segundo."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture()
def _pastas_tmp(tmp_path, monkeypatch):
    """Isola a pasta de fotos num diretório temporário."""
    import app.helpers as helpers
    fotos = tmp_path / 'fotos'
    fotos.mkdir()
    monkeypatch.setattr(helpers, 'FOTO_FOLDER', str(fotos))
    return {'fotos': fotos}


@pytest.fixture()
def _relogio_parado(monkeypatch):
    import app.helpers as helpers
    monkeypatch.setattr(helpers, 'datetime', _RelogioParado)


def _arquivo(conteudo, nome):
    return FileStorage(stream=io.BytesIO(conteudo), filename=nome)


def test_salvar_foto_colisao_mesmo_segundo_nao_sobrescreve(app, _pastas_tmp, _relogio_parado):
    nome1 = salvar_foto(_arquivo(PNG_A, 'a.png'), 'RET_COL123')
    nome2 = salvar_foto(_arquivo(PNG_B, 'b.png'), 'RET_COL123')

    assert nome1 and nome2
    assert nome1 != nome2                              # sufixo aleatório evita a colisão

    caminho1 = os.path.join(str(_pastas_tmp['fotos']), nome1)
    caminho2 = os.path.join(str(_pastas_tmp['fotos']), nome2)
    assert os.path.exists(caminho1) and os.path.exists(caminho2)
    with open(caminho1, 'rb') as f:
        assert f.read() == PNG_A                       # o primeiro upload sobrevive intacto
    with open(caminho2, 'rb') as f:
        assert f.read() == PNG_B
