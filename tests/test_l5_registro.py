"""
O log em arquivo.

🔴 Rodando como tarefa agendada não há console, e o Python segura a saída num
buffer até ele encher. Sem arquivo de log, o dia em que alguém for procurar o
motivo de algo ter parado encontra nada — nem erro, nem pista.

O teste que dá nome ao arquivo é `test_chamar_duas_vezes_nao_duplica_a_linha`:
o boot pode passar por aqui mais de uma vez (recarga do desenvolvimento, uma
segunda chamada por engano), e cada handler empilhado escreve a MESMA linha de
novo. Um log que repete tudo três vezes é um log que ninguém lê.
"""
import logging
import os

from app.registro import ARQUIVOS_GUARDADOS, TAMANHO_MAXIMO, configurar


class _AppFalso:
    def __init__(self):
        self.logger = logging.getLogger(f'lastro-teste-{id(self)}')
        self.logger.handlers = []


def test_o_log_vai_para_arquivo(tmp_path):
    app = _AppFalso()

    caminho = configurar(app, str(tmp_path))
    app.logger.info('coletor GR 001 retirado')

    assert caminho and os.path.exists(caminho)
    with open(caminho, encoding='utf-8') as fh:
        assert 'coletor GR 001 retirado' in fh.read()


def test_chamar_duas_vezes_nao_duplica_a_linha(tmp_path):
    """🔴 O nome do arquivo."""
    app = _AppFalso()
    configurar(app, str(tmp_path))
    caminho = configurar(app, str(tmp_path))

    app.logger.info('mensagem unica')

    with open(caminho, encoding='utf-8') as fh:
        assert fh.read().count('mensagem unica') == 1


def test_o_arquivo_tem_rotacao(tmp_path):
    """Sem rotação, o log cresce até ocupar o disco — e o primeiro sintoma
    seria o sistema parando de gravar movimentação, muito pior que o original."""
    app = _AppFalso()
    configurar(app, str(tmp_path))

    handler = app.logger.handlers[0]
    assert handler.maxBytes == TAMANHO_MAXIMO
    assert handler.backupCount == ARQUIVOS_GUARDADOS


def test_sem_permissao_o_sistema_ainda_sobe(tmp_path, monkeypatch):
    """Ficar sem log é ruim; não subir é pior.

    A tela de Saúde avisa sobre a pasta, e o balcão continua atendendo.
    """
    app = _AppFalso()

    def _recusar(*_a, **_k):
        raise OSError(13, 'Permissão negada')

    monkeypatch.setattr('app.registro.os.makedirs', _recusar)

    assert configurar(app, str(tmp_path)) is None
