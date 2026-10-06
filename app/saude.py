"""
O estado do sistema em português, para quem não lê log.

Esta é a tela que responde "está tudo bem?" sem obrigar ninguém a abrir um
arquivo de log nem a saber o que é WAL. Ela existe porque o produto vai rodar
numa máquina que ninguém monitora, operado por quem não o construiu.

Cada verificação devolve uma de três situações:
    ok      — não precisa fazer nada
    atencao — ainda funciona, mas vai parar de funcionar se ninguém olhar
    erro    — já está quebrado

A diferença entre `atencao` e `erro` é deliberada: disco com 2 GB livres não é
problema hoje e é problema certo em três meses. Pintar os dois de vermelho faz
quem olha parar de acreditar na tela; pintar os dois de verde faz a tela não
servir para nada.
"""
import os
import shutil
from datetime import datetime, timedelta

from app.backup import caminho_do_banco, copias_existentes

# Abaixo disto o disco vira assunto. 2 GB dá meses de folga para um banco de
# coletores, mas é pouco o bastante para alguém providenciar espaço sem correria.
DISCO_ATENCAO_GB = 2.0
DISCO_ERRO_GB = 0.5

# Backup de ontem é normal (o de hoje sai às 3h). Dois dias sem backup significa
# que alguma coisa parou — e é o tipo de coisa que passa meses despercebida.
BACKUP_ATENCAO_HORAS = 36
BACKUP_ERRO_HORAS = 72


def versao_instalada(root_dir):
    """A versão que está rodando, lida do arquivo VERSAO da raiz.

    🔴 Existe por causa da atualização: quando alguém substitui a pasta, a
    única forma de saber se o arquivo certo foi aplicado é o sistema DIZER qual
    versão carregou. Sem isso, "já atualizei" e "ainda não atualizei" são
    indistinguíveis — e o bug que você corrigiu continua aparecendo sem que
    ninguém entenda por quê.
    """
    try:
        with open(os.path.join(root_dir, 'VERSAO'), encoding='utf-8') as fh:
            return fh.read().strip() or 'desconhecida'
    except OSError:
        return 'desconhecida'


def _gb(bytes_):
    return bytes_ / (1024 ** 3)


def verificar_backup(root_dir, agora=None):
    """Quando foi a última cópia boa do banco."""
    agora = agora or datetime.now()
    pasta = os.path.join(root_dir, 'backups')
    copias = copias_existentes(pasta)

    if not copias:
        return {
            'nome': 'Backup do banco',
            'situacao': 'atencao',
            'resumo': 'Nenhuma cópia feita ainda.',
            'detalhe': 'A primeira cópia sai na próxima madrugada, às 3h. '
                       'Se amanhã ainda estiver assim, o sistema não está '
                       'conseguindo gravar na pasta de backups.',
        }

    ultima = copias[0]
    idade = agora - ultima['quando']
    quando = ultima['quando'].strftime('%d/%m/%Y às %H:%M')
    tamanho = f"{ultima['bytes'] / (1024 ** 2):.1f} MB"

    if idade > timedelta(hours=BACKUP_ERRO_HORAS):
        situacao, detalhe = 'erro', (
            'Faz mais de três dias. O backup automático parou — confira se o '
            'disco tem espaço e se a pasta de backups ainda existe.')
    elif idade > timedelta(hours=BACKUP_ATENCAO_HORAS):
        situacao, detalhe = 'atencao', (
            'Mais de um dia sem cópia nova. Pode ser que o servidor tenha '
            'ficado desligado; se repetir amanhã, algo está travando o backup.')
    else:
        situacao, detalhe = 'ok', f'{len(copias)} cópia(s) guardada(s) na pasta.'

    return {
        'nome': 'Backup do banco',
        'situacao': situacao,
        'resumo': f'Última cópia em {quando} ({tamanho}).',
        'detalhe': detalhe,
    }


def verificar_disco(root_dir):
    """Espaço livre no disco onde o sistema grava."""
    try:
        uso = shutil.disk_usage(root_dir)
    except OSError as erro:
        return {
            'nome': 'Espaço em disco',
            'situacao': 'erro',
            'resumo': 'Não consegui ler o disco.',
            'detalhe': str(erro),
        }

    livre = _gb(uso.free)
    resumo = f'{livre:.1f} GB livres de {_gb(uso.total):.0f} GB.'

    if livre < DISCO_ERRO_GB:
        return {'nome': 'Espaço em disco', 'situacao': 'erro', 'resumo': resumo,
                'detalhe': 'Praticamente sem espaço. O sistema pode parar de '
                           'gravar movimentações a qualquer momento.'}
    if livre < DISCO_ATENCAO_GB:
        return {'nome': 'Espaço em disco', 'situacao': 'atencao', 'resumo': resumo,
                'detalhe': 'Ainda funciona, mas convém liberar espaço antes que '
                           'aperte. As cópias de backup são o que mais ocupa.'}
    return {'nome': 'Espaço em disco', 'situacao': 'ok', 'resumo': resumo,
            'detalhe': ''}


def verificar_banco(uri):
    """O arquivo do banco existe e dá para abrir."""
    import sqlite3

    try:
        caminho = caminho_do_banco(uri)
    except Exception as erro:                      # noqa: BLE001
        return {'nome': 'Banco de dados', 'situacao': 'erro',
                'resumo': 'Configuração do banco não reconhecida.',
                'detalhe': str(erro)}

    if not os.path.exists(caminho):
        return {'nome': 'Banco de dados', 'situacao': 'erro',
                'resumo': 'O arquivo do banco não está no lugar.',
                'detalhe': f'Esperado em {caminho}.'}

    tamanho = os.path.getsize(caminho) / (1024 ** 2)
    try:
        con = sqlite3.connect(f'file:{caminho}?mode=ro', uri=True)
        try:
            tabelas = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as erro:
        return {'nome': 'Banco de dados', 'situacao': 'erro',
                'resumo': 'O arquivo existe mas não abre.',
                'detalhe': str(erro)}

    return {'nome': 'Banco de dados', 'situacao': 'ok',
            'resumo': f'{tamanho:.1f} MB, {tabelas} tabelas.',
            'detalhe': ''}


def verificar_pastas(root_dir):
    """As pastas de escrita existem e aceitam gravação.

    🔴 Testa gravando de verdade, não com `os.access`: em Windows, permissão de
    pasta de rede e herança de ACL fazem `os.access` responder que dá para
    escrever em lugar onde a escrita falha. O custo é um arquivo temporário.
    """
    problemas = []
    for nome in ('instance', 'uploads', 'backups'):
        caminho = os.path.join(root_dir, nome)
        try:
            os.makedirs(caminho, exist_ok=True)
            teste = os.path.join(caminho, '.escrita-teste')
            with open(teste, 'w', encoding='utf-8') as fh:
                fh.write('ok')
            os.remove(teste)
        except OSError as erro:
            problemas.append(f'{nome}: {erro.strerror or erro}')

    if problemas:
        return {'nome': 'Pastas do sistema', 'situacao': 'erro',
                'resumo': f'{len(problemas)} pasta(s) sem permissão de escrita.',
                'detalhe': ' · '.join(problemas)}
    return {'nome': 'Pastas do sistema', 'situacao': 'ok',
            'resumo': 'Todas as pastas existem e aceitam gravação.',
            'detalhe': ''}


def diagnostico(uri, root_dir, agora=None):
    """Todas as verificações, mais o veredito geral.

    O geral é o PIOR dos itens: um sistema com backup parado não está "quase
    bem". Quem abre esta tela precisa de uma resposta só.
    """
    itens = [
        verificar_banco(uri),
        verificar_backup(root_dir, agora=agora),
        verificar_disco(root_dir),
        verificar_pastas(root_dir),
    ]
    situacoes = [i['situacao'] for i in itens]
    if 'erro' in situacoes:
        geral = 'erro'
    elif 'atencao' in situacoes:
        geral = 'atencao'
    else:
        geral = 'ok'
    return {'geral': geral, 'itens': itens,
            'versao': versao_instalada(root_dir)}
