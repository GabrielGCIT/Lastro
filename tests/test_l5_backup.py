"""
Testes do backup do banco.

🔴 O teste que dá nome ao arquivo é `test_backup_pega_escrita_recente_do_wal`.

O banco roda em WAL: uma movimentação registrada agora vive no arquivo `-wal`,
não no `.db`, até o próximo checkpoint. Um backup feito com `shutil.copy` do
`.db` abre, passa em `integrity_check`, tem todas as tabelas — e está sem o que
aconteceu hoje. Esse backup não falha: ele mente, e só é descoberto no dia em
que alguém precisa restaurar.

Por isso o teste não se contenta em verificar que o arquivo existe: ele escreve,
copia, e vai LER o dado dentro da cópia.
"""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta

import pytest

from app.backup import (BackupFalhou, caminho_do_banco, copias_existentes,
                        fazer_backup, limpar_antigos)


@pytest.fixture()
def banco_wal(tmp_path):
    """Um banco SQLite em WAL, com uma tabela e uma linha — como o de verdade."""
    caminho = str(tmp_path / 'origem.db')
    con = sqlite3.connect(caminho)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE coletores (id INTEGER PRIMARY KEY, patrimonio TEXT)')
    con.execute("INSERT INTO coletores (patrimonio) VALUES ('001')")
    con.commit()
    yield caminho, con, str(tmp_path / 'backups')
    con.close()


def test_backup_pega_escrita_recente_do_wal(banco_wal):
    """🔴 O nome do arquivo: a cópia tem de conter o que acabou de ser gravado.

    A conexão fica ABERTA de propósito, sem checkpoint, que é o estado real de
    um servidor no ar: o app segurando o banco e o WAL cheio.
    """
    origem, con, destino = banco_wal
    con.execute("INSERT INTO coletores (patrimonio) VALUES ('RECEM-GRAVADO')")
    con.commit()

    copia = fazer_backup(f'sqlite:///{origem}', destino)

    lido = sqlite3.connect(copia)
    try:
        achados = [r[0] for r in lido.execute('SELECT patrimonio FROM coletores')]
    finally:
        lido.close()
    assert 'RECEM-GRAVADO' in achados, (
        'a cópia não tem a escrita recente — é o backup que mente')


def test_copiar_o_arquivo_na_mao_perderia_o_dado(banco_wal):
    """O contraponto que justifica o módulo existir.

    Escrever este teste mostrou que o estrago é MAIOR do que eu supunha: sem
    checkpoint, nem o `CREATE TABLE` chegou ao `.db` — a cópia ingênua abre
    como um banco vazio. Ou seja, o `shutil.copy` não perde só o movimento de
    hoje; dependendo do momento, ele copia um arquivo sem nada dentro.

    Por isso `_conferir` exige tabelas além do `integrity_check`: um banco vazio
    passa no integrity_check com louvor.
    """
    origem, con, destino = banco_wal
    con.execute("INSERT INTO coletores (patrimonio) VALUES ('SO-NO-WAL')")
    con.commit()

    os.makedirs(destino, exist_ok=True)
    ingenuo = os.path.join(destino, 'copia-ingenua.db')
    shutil.copy(origem, ingenuo)      # só o .db, sem o -wal

    lido = sqlite3.connect(ingenuo)
    try:
        integro = lido.execute('PRAGMA integrity_check').fetchone()[0]
        try:
            achados = [r[0] for r in lido.execute('SELECT patrimonio FROM coletores')]
        except sqlite3.OperationalError:
            achados = None          # a tabela nem chegou ao .db
    finally:
        lido.close()

    assert integro == 'ok', 'a cópia ingênua nem seria detectada como corrompida'
    assert achados is None or 'SO-NO-WAL' not in achados, (
        'copiar o .db sozinho trouxe o dado do WAL — o WAL ainda está ligado?')


def test_copia_ruim_nao_fica_no_disco(banco_wal, monkeypatch):
    """Backup que não passou na conferência é apagado, não guardado.

    Um arquivo ruim na pasta de backups é pior que pasta vazia: no dia do
    aperto, alguém o encontra e acredita nele.
    """
    origem, _con, destino = banco_wal

    def _reprovar(caminho):
        raise BackupFalhou('reprovado de propósito')

    monkeypatch.setattr('app.backup._conferir', _reprovar)

    with pytest.raises(BackupFalhou):
        fazer_backup(f'sqlite:///{origem}', destino)

    sobrou = [n for n in os.listdir(destino)] if os.path.isdir(destino) else []
    assert sobrou == [], f'a cópia reprovada ficou no disco: {sobrou}'


def test_banco_inexistente_falha_alto(tmp_path):
    """Sem o banco, o backup não pode "dar certo" em silêncio."""
    with pytest.raises(BackupFalhou):
        fazer_backup(f'sqlite:///{tmp_path}/nao-existe.db', str(tmp_path / 'b'))


def test_so_sabe_fazer_de_sqlite(tmp_path):
    """Config apontando para outro banco é erro de instalação, não um aviso.

    O exemplo usa postgres de propósito: `tests/test_fronteira.py` barra os
    termos do produto de onde este código veio, e a primeira versão deste teste
    citava o banco de lá — a guarda da fronteira mordeu, que é o trabalho dela.
    """
    with pytest.raises(BackupFalhou):
        caminho_do_banco('postgresql://servidor/base')


def test_retencao_usa_a_DATA_DO_NOME_e_nao_o_mtime(tmp_path):
    """🔴 Copiar a pasta de backups para outro disco reescreve o mtime.

    Com a idade vindo do mtime, uma pasta restaurada de fita pareceria toda
    nova (nada seria limpo) e uma pasta recém-copiada pareceria toda velha —
    apagando os backups bons no primeiro dia. O nome do arquivo não muda ao ser
    copiado, então é dele que a data sai.
    """
    pasta = tmp_path / 'backups'
    pasta.mkdir()
    antigo = pasta / 'lastro-20260101-0300.db'
    novo = pasta / 'lastro-20261005-0300.db'
    for f in (antigo, novo):
        f.write_bytes(b'x')
        # mtime de AGORA nos dois: pelo mtime, nenhum seria apagado.
        os.utime(f, None)

    apagados = limpar_antigos(str(pasta), dias=30,
                              agora=datetime(2026, 10, 5, 12, 0))

    assert apagados == ['lastro-20260101-0300.db']
    assert novo.exists(), 'apagou o backup recente'


def test_arquivo_estranho_na_pasta_e_poupado(tmp_path):
    """A pasta pode ter outras coisas; a limpeza não é dona dela."""
    pasta = tmp_path / 'backups'
    pasta.mkdir()
    (pasta / 'anotacoes-do-analista.txt').write_text('nao me apague')
    (pasta / 'lastro-20260101-0300.db').write_bytes(b'x')

    limpar_antigos(str(pasta), dias=1, agora=datetime(2026, 10, 5))

    assert (pasta / 'anotacoes-do-analista.txt').exists()


def test_copias_existentes_vem_da_mais_nova(banco_wal):
    """Alimenta a tela de saúde: "o último backup foi às 03:00"."""
    origem, _con, destino = banco_wal
    base = datetime(2026, 10, 5, 3, 0)
    fazer_backup(f'sqlite:///{origem}', destino, agora=base - timedelta(days=1))
    fazer_backup(f'sqlite:///{origem}', destino, agora=base)

    copias = copias_existentes(destino)

    assert len(copias) == 2
    assert copias[0]['quando'] == base, 'a primeira não é a mais nova'
    assert copias[0]['bytes'] > 0


def test_pasta_sem_backup_nenhum_nao_quebra(tmp_path):
    """Primeiro dia da instalação: a tela de saúde ainda precisa abrir."""
    assert copias_existentes(str(tmp_path / 'nunca-criada')) == []
    assert limpar_antigos(str(tmp_path / 'nunca-criada')) == []


def test_banco_vazio_e_integro_nao_passa_como_backup(tmp_path):
    """🔴 `integrity_check` aprova um banco VAZIO — ele está íntegro, afinal.

    E banco vazio é exatamente o que uma cópia interrompida no começo produz,
    ou o que o `shutil.copy` devolve quando nada passou por checkpoint ainda.
    Sem a contagem de tabelas, essa cópia entraria na pasta de backups como boa.

    Este teste nasceu de uma sabotagem que PASSOU: eu tinha escrito a guarda e
    explicado o porquê no comentário, mas nenhum teste a exercitava.
    """
    from app.backup import _conferir

    vazio = str(tmp_path / 'vazio.db')
    con = sqlite3.connect(vazio)
    con.close()                      # banco válido, íntegro, sem tabela nenhuma

    assert sqlite3.connect(vazio).execute(
        'PRAGMA integrity_check').fetchone()[0] == 'ok', 'o cenário exige um banco íntegro'

    with pytest.raises(BackupFalhou):
        _conferir(vazio)
