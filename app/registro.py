"""
O log em arquivo — o que alguém vai ler quando der problema.

🔴 Por que não basta imprimir no console:

Rodando como serviço do Windows, não há console. E mesmo quando há, o Python
segura a saída num buffer até ele encher: um `print` que você acabou de escrever
só aparece horas depois, ou nunca, se o processo for encerrado antes. O sintoma
é um arquivo de log VAZIO justamente no dia em que alguém foi procurar o motivo
de algo ter parado.

Aqui o log vai para `logs/lastro.log`, com rotação por tamanho. Rotação
importa porque este sistema deve rodar anos sem ninguém olhar: sem ela, o
arquivo cresce até ocupar o disco, e o primeiro sintoma seria o sistema parando
de gravar movimentação — um problema muito pior que o original.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

# 5 MB por arquivo, 5 arquivos guardados: ~25 MB no total, no pior caso. Cobre
# meses de operação e não é o que vai encher o disco de ninguém.
TAMANHO_MAXIMO = 5 * 1024 * 1024
ARQUIVOS_GUARDADOS = 5

FORMATO = '%(asctime)s %(levelname)-7s %(message)s'
DATA = '%d/%m/%Y %H:%M:%S'


def pasta_de_logs(root_dir):
    return os.path.join(root_dir, 'logs')


def configurar(app, root_dir):
    """Liga o log em arquivo. Idempotente — chamada mais de uma vez não duplica.

    Devolve o caminho do arquivo, ou None se não foi possível escrever (pasta
    sem permissão, disco cheio). Nesse caso o sistema SOBE assim mesmo: ficar
    sem log é ruim, não subir é pior, e a tela de saúde avisa sobre a pasta.
    """
    pasta = pasta_de_logs(root_dir)
    caminho = os.path.join(pasta, 'lastro.log')

    # Chamada repetida (reload do Werkzeug, teste) não pode empilhar handlers:
    # cada um escreveria a mesma linha de novo no arquivo.
    for handler in app.logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            return getattr(handler, 'baseFilename', caminho)

    try:
        os.makedirs(pasta, exist_ok=True)
        handler = RotatingFileHandler(
            caminho, maxBytes=TAMANHO_MAXIMO,
            backupCount=ARQUIVOS_GUARDADOS, encoding='utf-8')
    except OSError:
        return None

    handler.setFormatter(logging.Formatter(FORMATO, datefmt=DATA))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    return caminho


def anunciar(mensagem):
    """Imprime no console SEM esperar o buffer.

    Quem instala acompanha o primeiro boot pela janela do console, e é ali que
    sai o endereço de primeiro acesso. Sem `flush`, essa linha pode não aparecer
    nunca — e a pessoa fica olhando uma tela preta sem saber o que fazer.
    """
    print(mensagem, flush=True)
