"""
Backup do banco — uma cópia consistente, verificada, sem parar o sistema.

🔴 Por que NÃO é um copiar-e-colar do arquivo:

O banco roda em modo WAL (write-ahead log). As transações recentes vivem no
`lastro.db-wal`, não no `.db`, até o próximo checkpoint. Copiar só o `.db` com
o sistema no ar devolve um arquivo que ABRE, que parece certo, e que está sem as
últimas movimentações — ou pior, pego no meio de uma escrita. Um backup que
mente é mais perigoso que backup nenhum: ninguém confere o que acredita ter.

A API `sqlite3.Connection.backup()` resolve isso. Ela copia página a página pela
própria engine, enxerga o WAL, e lida com escrita concorrente — o balcão pode
estar registrando uma retirada enquanto o backup roda.

E toda cópia é CONFERIDA antes de contar como sucesso: o arquivo é reaberto e
submetido a `PRAGMA integrity_check`, mais uma contagem de tabelas. Sem isso o
backup só seria descoberto ruim no dia em que alguém precisasse dele.
"""
import os
import re
import sqlite3
from datetime import datetime, timedelta

# Quantos dias de cópias ficam no disco. Trinta cobre o "só percebi semana
# passada" sem encher o disco de um servidor que ninguém monitora.
RETENCAO_DIAS = 30

# lastro-20261005-2230.db — data e hora no nome, ordenável por nome.
PADRAO_NOME = re.compile(r'^lastro-(\d{8})-(\d{4})\.db$')


class BackupFalhou(Exception):
    """A cópia não foi feita, ou foi feita e não passou na conferência."""


def caminho_do_banco(uri):
    """Extrai o caminho do arquivo de uma URI do SQLAlchemy.

    Só sabe ler SQLite — é o único banco que este produto usa, e tentar
    adivinhar outros aqui só esconderia o erro mais para frente.
    """
    if not uri.startswith('sqlite:///'):
        raise BackupFalhou(
            f'Só sei fazer backup de SQLite, e a configuração aponta para {uri[:30]!r}.')
    return uri[len('sqlite:///'):]


def _conferir(caminho):
    """Abre a cópia e exige que ela esteja íntegra e com conteúdo.

    `integrity_check` sozinho aprova um banco VAZIO e íntegro — que é
    exatamente o que um backup interrompido no início produz. Por isso a
    contagem de tabelas vem junto.
    """
    con = sqlite3.connect(caminho)
    try:
        resultado = con.execute('PRAGMA integrity_check').fetchone()[0]
        if resultado != 'ok':
            raise BackupFalhou(f'A cópia não passou na verificação: {resultado}')
        tabelas = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if tabelas == 0:
            raise BackupFalhou('A cópia abriu, mas está sem tabela nenhuma.')
        return tabelas
    finally:
        con.close()


def fazer_backup(uri, pasta_destino, agora=None):
    """Copia o banco para `pasta_destino` e devolve o caminho do arquivo.

    Levanta BackupFalhou se a cópia não pôde ser feita ou não passou na
    conferência — nesse caso o arquivo ruim é apagado, para que ninguém o
    encontre depois achando que é um backup bom.
    """
    origem = caminho_do_banco(uri)
    if not os.path.exists(origem):
        raise BackupFalhou(f'O banco não existe em {origem}.')

    agora = agora or datetime.now()
    os.makedirs(pasta_destino, exist_ok=True)
    destino = os.path.join(pasta_destino,
                           f'lastro-{agora:%Y%m%d}-{agora:%H%M}.db')

    try:
        com_origem = sqlite3.connect(f'file:{origem}?mode=ro', uri=True)
        try:
            com_destino = sqlite3.connect(destino)
            try:
                com_origem.backup(com_destino)
            finally:
                com_destino.close()
        finally:
            com_origem.close()
    except sqlite3.Error as erro:
        _apagar(destino)
        raise BackupFalhou(f'Não consegui copiar o banco: {erro}') from erro

    try:
        _conferir(destino)
    except BackupFalhou:
        # Cópia ruim não fica no disco se passando por boa.
        _apagar(destino)
        raise

    return destino


def _apagar(caminho):
    try:
        if os.path.exists(caminho):
            os.remove(caminho)
    except OSError:
        pass


def limpar_antigos(pasta_destino, dias=RETENCAO_DIAS, agora=None):
    """Apaga cópias mais velhas que `dias`. Devolve o que apagou.

    A idade vem do NOME do arquivo, não da data de modificação: copiar a pasta
    de backup para outro disco (ou restaurar de uma fita) reescreve o mtime e
    faria a limpeza apagar o que acabou de chegar, ou poupar o que já venceu.
    Arquivo com nome fora do padrão é ignorado — pode ser de outra pessoa.
    """
    agora = agora or datetime.now()
    corte = agora - timedelta(days=dias)
    apagados = []
    if not os.path.isdir(pasta_destino):
        return apagados

    for nome in sorted(os.listdir(pasta_destino)):
        casou = PADRAO_NOME.match(nome)
        if not casou:
            continue
        try:
            quando = datetime.strptime(casou.group(1) + casou.group(2), '%Y%m%d%H%M')
        except ValueError:
            continue
        if quando < corte:
            _apagar(os.path.join(pasta_destino, nome))
            apagados.append(nome)
    return apagados


def copias_existentes(pasta_destino):
    """As cópias que estão no disco, da mais nova para a mais velha.

    Alimenta a tela de saúde: "o último backup foi hoje às 03:00" é o que diz a
    quem administra se o sistema está se cuidando sozinho.
    """
    if not os.path.isdir(pasta_destino):
        return []
    achados = []
    for nome in os.listdir(pasta_destino):
        casou = PADRAO_NOME.match(nome)
        if not casou:
            continue
        try:
            quando = datetime.strptime(casou.group(1) + casou.group(2), '%Y%m%d%H%M')
        except ValueError:
            continue
        caminho = os.path.join(pasta_destino, nome)
        achados.append({
            'nome': nome,
            'caminho': caminho,
            'quando': quando,
            'bytes': os.path.getsize(caminho),
        })
    return sorted(achados, key=lambda c: c['quando'], reverse=True)
