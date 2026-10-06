"""
A tela de saúde avisa quando o firewall bloqueia o acesso pela rede.

🔴 Caso real, achado pelo Gabriel tentando abrir o sistema no celular: o
servidor estava no ar, escutando em 0.0.0.0, o IP estava certo — e não abria. O
Windows aceita a conexão local, que não atravessa o firewall de entrada, e
recusa a de qualquer outra máquina. Nada quebra, nada vai para o log, e quem
está do outro lado conclui que "o sistema não funciona".

O teste que dá nome ao arquivo é `test_regra_UDP_com_o_numero_certo_nao_conta`.
O que me enganou no diagnóstico foi exatamente isso: existia uma regra chamada
"5001", habilitada, permitindo — de UDP. HTTP é TCP. A regra parecia a
resposta e não servia para nada.
"""
import subprocess

import pytest

from app.saude import diagnostico, verificar_acesso_pela_rede


def _saida(texto, returncode=0):
    """Fabrica o retorno do netsh."""
    class _R:
        pass
    r = _R()
    r.stdout = texto
    r.returncode = returncode
    return r


def _netsh(monkeypatch, texto, returncode=0):
    monkeypatch.setattr('sys.platform', 'win32')
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: _saida(texto, returncode))


REGRA_TCP = """
Nome da Regra:                        Lastro (HTTP)
Habilitado:                           Sim
Direção:                              Entrada
Perfis:                               Domínio,Particular,Público
Protocolo:                            TCP
LocalPort:                            5001
Ação:                                 Permitir
"""

REGRA_UDP = """
Nome da Regra:                        5001
Habilitado:                           Sim
Direção:                              Entrada
Perfis:                               Domínio,Particular,Público
Protocolo:                            UDP
LocalPort:                            5001
Ação:                                 Permitir
"""

OUTRA_PORTA = """
Nome da Regra:                        Outro servico
Habilitado:                           Sim
Protocolo:                            TCP
LocalPort:                            8080
Ação:                                 Permitir
"""


def test_regra_UDP_com_o_numero_certo_nao_conta(monkeypatch):
    """🔴 O nome do arquivo: HTTP é TCP."""
    _netsh(monkeypatch, REGRA_UDP)

    item = verificar_acesso_pela_rede(5001)

    assert item['situacao'] == 'atencao', 'aceitou uma regra UDP como se servisse'


def test_com_regra_TCP_esta_tudo_certo(monkeypatch):
    _netsh(monkeypatch, REGRA_TCP)

    assert verificar_acesso_pela_rede(5001)['situacao'] == 'ok'


def test_sem_regra_nenhuma_avisa(monkeypatch):
    _netsh(monkeypatch, OUTRA_PORTA)

    item = verificar_acesso_pela_rede(5001)

    assert item['situacao'] == 'atencao'
    assert '5001' in item['detalhe'], 'o aviso tem de dizer qual porta liberar'


SEM_PROTOCOLO = """
Nome da Regra:                        Regra antiga
Habilitado:                           Sim
LocalPort:                            5001
Ação:                                 Permitir
"""


def test_a_porta_e_o_protocolo_tem_de_estar_na_MESMA_regra(monkeypatch):
    """🔴 Campos de regras DIFERENTES não se somam.

    Cenário: uma regra TCP da porta 8080, e outra regra desta porta que não
    declara protocolo. Lidos como um bloco só, o "TCP" da primeira encontra o
    "5001" da segunda e o sistema diria que está liberado — quando não está.

    Este caso substituiu um anterior (TCP de outra porta + UDP desta) que
    passava mesmo com a separação por regra sabotada: ali os campos se
    sobrescreviam numa ordem que dava o resultado certo por acidente. Descobri
    sabotando, e o teste que não distingue não defende.
    """
    _netsh(monkeypatch, OUTRA_PORTA + SEM_PROTOCOLO)

    assert verificar_acesso_pela_rede(5001)['situacao'] == 'atencao',         'juntou o protocolo de uma regra com a porta de outra'


def test_fora_do_windows_o_item_nao_aparece(monkeypatch):
    """Chutar "está liberado" seria pior que calar."""
    monkeypatch.setattr('sys.platform', 'linux')

    assert verificar_acesso_pela_rede(5001) is None


def test_netsh_indisponivel_nao_quebra_a_tela(monkeypatch):
    monkeypatch.setattr('sys.platform', 'win32')

    def _explode(*a, **k):
        raise OSError('netsh não encontrado')

    monkeypatch.setattr(subprocess, 'run', _explode)

    assert verificar_acesso_pela_rede(5001) is None


def test_netsh_que_falha_nao_vira_alarme_falso(monkeypatch):
    """Sem permissão para perguntar, o certo é não responder."""
    _netsh(monkeypatch, '', returncode=1)

    assert verificar_acesso_pela_rede(5001) is None


def test_o_item_entra_no_diagnostico(monkeypatch, tmp_path):
    """E some quando não dá para verificar — a tela não pode ganhar um item
    vazio."""
    import sqlite3

    banco = tmp_path / 'lastro.db'
    con = sqlite3.connect(str(banco))
    con.execute('CREATE TABLE x (id INTEGER)')
    con.close()
    uri = f'sqlite:///{banco}'

    _netsh(monkeypatch, REGRA_UDP)
    nomes = [i['nome'] for i in diagnostico(uri, str(tmp_path))['itens']]
    assert 'Acesso pela rede' in nomes

    monkeypatch.setattr('sys.platform', 'linux')
    nomes = [i['nome'] for i in diagnostico(uri, str(tmp_path))['itens']]
    assert 'Acesso pela rede' not in nomes


# ---------------------------------------------------------------------------
# OUTRO FIREWALL NA MÁQUINA
# ---------------------------------------------------------------------------

def _powershell(monkeypatch, saida, returncode=0):
    monkeypatch.setattr('sys.platform', 'win32')
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: _saida(saida, returncode))


def test_antivirus_com_firewall_proprio_e_avisado(monkeypatch):
    """🔴 O caso que custou duas rodadas de diagnóstico.

    A porta estava liberada no firewall do Windows, o servidor no ar, o IP
    certo — e o celular não abria. O motivo era um antivírus com firewall
    próprio, que roda por cima e barra antes. Máquina de empresa quase sempre
    tem um, e liberar só o do Windows não basta.
    """
    from app.saude import verificar_outro_firewall

    _powershell(monkeypatch, 'Kaspersky\n')

    item = verificar_outro_firewall()

    assert item is not None, 'não avisou sobre o segundo firewall'
    assert 'Kaspersky' in item['resumo']
    assert '5001' in item['detalhe'], 'o aviso tem de dizer o que liberar'


def test_o_defender_nao_conta_como_outro_firewall(monkeypatch):
    """🔴 O Defender É o firewall do Windows.

    Sem esta exceção, toda instalação ganharia um aviso dizendo para liberar a
    porta "também" no firewall que já foi liberado — e aviso que aparece sempre
    é aviso que ninguém lê.
    """
    from app.saude import verificar_outro_firewall

    _powershell(monkeypatch, 'Windows Defender\n')

    assert verificar_outro_firewall() is None


def test_sem_outro_firewall_o_item_some(monkeypatch):
    from app.saude import verificar_outro_firewall

    _powershell(monkeypatch, '\n')

    assert verificar_outro_firewall() is None


def test_nome_repetido_aparece_uma_vez_so(monkeypatch):
    """O Security Center lista o mesmo produto duas vezes (foi o que aconteceu
    na máquina real). "Kaspersky, Kaspersky" só confunde."""
    from app.saude import outros_firewalls

    _powershell(monkeypatch, 'Kaspersky\nKaspersky\n')

    assert outros_firewalls() == ['Kaspersky']


def test_consulta_que_falha_nao_inventa_aviso(monkeypatch):
    from app.saude import outros_firewalls

    _powershell(monkeypatch, '', returncode=1)

    assert outros_firewalls() == []


def test_o_aviso_do_outro_firewall_chega_na_TELA(monkeypatch, tmp_path):
    """🔴 Função certa que não é chamada é função que não existe.

    Descobri sabotando: removi a chamada do `diagnostico` e todos os testes
    desta seção continuaram verdes, porque exercitavam a função isolada. O
    aviso tem de aparecer na tela, que é onde alguém vai lê-lo.
    """
    import sqlite3

    from app.saude import diagnostico

    banco = tmp_path / 'lastro.db'
    con = sqlite3.connect(str(banco))
    con.execute('CREATE TABLE x (id INTEGER)')
    con.close()

    _powershell(monkeypatch, 'Kaspersky\n')
    nomes = [i['nome'] for i in diagnostico(f'sqlite:///{banco}', str(tmp_path))['itens']]

    assert 'Outro firewall instalado' in nomes
