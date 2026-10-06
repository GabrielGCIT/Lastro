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
import re
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


def verificar_acesso_pela_rede(porta=5001):
    """O firewall do Windows deixa as outras máquinas chegarem até aqui?

    🔴 Esta verificação existe por causa de um caso real: o sistema subiu,
    funcionou na tela de quem instalou, e não abria em mais nenhum aparelho. O
    Windows aceita a conexão local — que não atravessa o firewall de entrada —
    e recusa a de qualquer outra máquina. Nada quebra, nada aparece no log, e a
    conclusão de quem está do outro lado é "o sistema não funciona".

    O detalhe que tinha enganado: existia uma regra com o número certo, mas de
    UDP. HTTP é TCP, então ela não servia para nada. Por isso aqui o protocolo é
    conferido, e não só a porta.

    Fora do Windows, ou sem conseguir perguntar ao firewall, devolve None — e o
    item simplesmente não aparece. Chutar "está liberado" seria pior que calar.
    """
    import subprocess
    import sys

    if not sys.platform.startswith('win'):
        return None

    try:
        saida = subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'show', 'rule',
             'name=all', 'dir=in', 'verbose'],
            capture_output=True, text=True, timeout=20,
            encoding='latin-1', errors='replace')
    except (OSError, subprocess.SubprocessError):
        return None
    if saida.returncode != 0:
        return None

    liberada = _ha_regra_tcp(saida.stdout, porta)

    if liberada:
        return {'nome': 'Acesso pela rede', 'situacao': 'ok',
                'resumo': f'A porta {porta} está liberada no firewall.',
                'detalhe': ''}
    return {
        'nome': 'Acesso pela rede',
        'situacao': 'atencao',
        'resumo': f'A porta {porta} não está liberada no firewall do Windows.',
        'detalhe': 'O sistema funciona nesta máquina, mas pode não abrir nas '
                   'outras. Execute o instalar-servico.bat como administrador, '
                   'ou peça a quem cuida da rede para liberar a porta '
                   f'{porta} (TCP).',
    }


def _rotulo(linha):
    """Separa "Campo: valor" de uma linha do netsh, sem depender do idioma."""
    if ':' not in linha:
        return None, None
    campo, _, valor = linha.partition(':')
    return campo.strip().lower(), valor.strip()


def _ha_regra_tcp(saida_netsh, porta):
    """True se existe regra de ENTRADA permitindo TCP nesta porta.

    🔴 Linha a linha em vez de expressão regular. A primeira versão usava regex
    sobre o texto inteiro e eu não consegui fazê-la concordar com o resultado
    que a mesma expressão dava fora da função — gastei meia dúzia de tentativas
    nisso. Um parser explícito custa dez linhas, dá para ler em voz alta, e
    quando erra dá para apontar onde.

    As três condições precisam vir da MESMA regra: porta, protocolo TCP e ação
    de permitir. Procurar os três no texto todo aprovaria um servidor em que
    existem uma regra TCP de outra porta e uma regra UDP desta.
    """
    PORTA = ('localport', 'porta local')
    PROTO = ('protocol', 'protocolo')
    ACAO = ('action', 'ação', 'acao')
    NOME = ('rule name', 'nome da regra')

    atual = {}
    regras = [atual]
    for linha in saida_netsh.splitlines():
        campo, valor = _rotulo(linha)
        if campo is None:
            continue
        if campo in NOME:                 # começou outra regra
            atual = {}
            regras.append(atual)
        elif campo in PORTA:
            atual['porta'] = valor
        elif campo in PROTO:
            atual['proto'] = valor.upper()
        elif campo in ACAO:
            atual['acao'] = valor.lower()

    alvo = str(porta)
    for r in regras:
        # A porta pode vir como lista ("80,443") ou faixa; basta conter a nossa.
        portas = [x.strip() for x in r.get('porta', '').split(',')]
        if alvo not in portas:
            continue
        if r.get('proto') != 'TCP':
            continue
        if r.get('acao', '').startswith(('allow', 'permitir')):
            return True
    return False


def outros_firewalls():
    """Nomes dos firewalls de terceiros instalados nesta máquina.

    🔴 Existe por causa de um caso real que custou duas rodadas de diagnóstico:
    a porta estava liberada no firewall do Windows, o servidor no ar, o IP
    certo — e o celular não abria. O motivo era um antivírus com firewall
    próprio (Kaspersky), que roda por cima do Windows e barra antes.

    Isso importa muito para instalação em empresa: máquina corporativa quase
    sempre tem antivírus com firewall, e liberar só o do Windows não basta. Sem
    este aviso, a conclusão de quem instala é "o sistema não funciona na rede".

    Lista vazia quando não há outro firewall, ou quando não dá para perguntar.
    """
    import sys

    if not sys.platform.startswith('win'):
        return []
    try:
        import subprocess
        # O Security Center conhece os produtos registrados. Consultado por
        # linha de comando para não exigir biblioteca extra no pacote.
        saida = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-CimInstance -Namespace root/SecurityCenter2 "
             "-ClassName FirewallProduct | "
             "Select-Object -ExpandProperty displayName"],
            capture_output=True, text=True, timeout=25,
            encoding='latin-1', errors='replace')
    except (OSError, Exception):          # noqa: BLE001
        return []
    if saida.returncode != 0:
        return []

    nomes = []
    for linha in saida.stdout.splitlines():
        nome = linha.strip()
        # O Defender é o firewall do próprio Windows — não é "outro".
        if nome and 'defender' not in nome.lower() and nome not in nomes:
            nomes.append(nome)
    return nomes


def verificar_outro_firewall():
    """Avisa que há um segundo firewall, que precisa ser liberado à parte."""
    nomes = outros_firewalls()
    if not nomes:
        return None
    return {
        'nome': 'Outro firewall instalado',
        'situacao': 'atencao',
        'resumo': f"Esta máquina também tem {', '.join(nomes)}.",
        'detalhe': 'Liberar a porta no firewall do Windows pode não bastar: '
                   'um antivírus com firewall próprio barra antes. Se o sistema '
                   'não abrir em outros aparelhos, libere a porta 5001 (TCP) '
                   'também nele.',
    }


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
    rede = verificar_acesso_pela_rede()
    if rede:                       # None = não dá para perguntar; não inventa
        itens.append(rede)
    outro = verificar_outro_firewall()
    if outro:                      # só aparece quando há mesmo outro firewall
        itens.append(outro)
    situacoes = [i['situacao'] for i in itens]
    if 'erro' in situacoes:
        geral = 'erro'
    elif 'atencao' in situacoes:
        geral = 'atencao'
    else:
        geral = 'ok'
    return {'geral': geral, 'itens': itens,
            'versao': versao_instalada(root_dir)}
