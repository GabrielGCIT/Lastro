"""
As opções que quem administra pode mudar depois da instalação.

🔴 O arquivo vive em `instance/`, e isso não é detalhe: é a única pasta que a
atualização não toca. Configuração na raiz do sistema seria sobrescrita na
primeira correção enviada, e a porta escolhida pelo cliente voltaria ao padrão
sem ninguém entender por quê — num reinício qualquer, semanas depois.

O formato é `chave = valor`, uma por linha, com `#` para comentário. Nada de
JSON ou INI com seções: quem vai editar isso abre o Bloco de Notas no servidor,
às vezes com o sistema fora do ar e alguém esperando. Uma vírgula fora do lugar
não pode derrubar a instalação.

Por isso toda leitura é tolerante: linha estragada, valor fora da faixa ou
arquivo ilegível caem no padrão e seguem. O sistema SOBE — e a tela de Saúde
mostra qual porta está valendo de verdade.
"""
import os

NOME_ARQUIVO = 'configuracao.txt'

PORTA_PADRAO = 5001
# Abaixo de 1024 são portas reservadas, que no Windows exigem privilégio e
# costumam estar ocupadas. Acima de 65535 não existe.
PORTA_MINIMA = 1024
PORTA_MAXIMA = 65535

MODELO = """# Configuração do Lastro
# ----------------------------------------------------------------------
# Edite este arquivo e reinicie o sistema para valer.
# Linhas começando com # são comentário e não fazem nada.
#
# Este arquivo NÃO é substituído quando o sistema é atualizado.


# Porta de acesso. O endereço do sistema fica http://<servidor>:<porta>
# Troque se a {padrao} estiver ocupada por outro programa nesta máquina.
#
# ATENÇÃO: ao trocar a porta, ela também precisa ser liberada no firewall.
# A tela "Saúde do sistema" avisa se faltar.
porta = {padrao}
"""


def caminho(instance_folder):
    return os.path.join(instance_folder, NOME_ARQUIVO)


def garantir_arquivo(instance_folder):
    """Cria o arquivo comentado no primeiro boot. Devolve o caminho.

    Nunca sobrescreve: o que o cliente escreveu lá é dele.
    """
    destino = caminho(instance_folder)
    if os.path.exists(destino):
        return destino
    try:
        os.makedirs(instance_folder, exist_ok=True)
        with open(destino, 'w', encoding='utf-8', newline='\r\n') as fh:
            fh.write(MODELO.format(padrao=PORTA_PADRAO))
    except OSError:
        return None            # sem permissão: o sistema usa os padrões
    return destino


def _ler_pares(instance_folder):
    """Lê o arquivo como {chave: valor}. Arquivo ausente ou ilegível = {}."""
    try:
        with open(caminho(instance_folder), encoding='utf-8') as fh:
            linhas = fh.readlines()
    except OSError:
        return {}

    pares = {}
    for linha in linhas:
        linha = linha.split('#', 1)[0].strip()
        if not linha or '=' not in linha:
            continue
        chave, _, valor = linha.partition('=')
        pares[chave.strip().lower()] = valor.strip()
    return pares


def porta(instance_folder):
    """A porta configurada, ou o padrão.

    A variável de ambiente PORT ainda vence, porque é o que o desenvolvimento
    usa para subir duas instâncias ao mesmo tempo — e porque quem define
    variável de ambiente sabe o que está fazendo.

    Valor inválido não derruba o sistema: cai no padrão. Um erro de digitação às
    três da manhã não pode deixar o balcão sem sistema de manhã.
    """
    do_ambiente = os.environ.get('PORT')
    if do_ambiente and do_ambiente.strip().isdigit():
        bruto = do_ambiente.strip()
    else:
        bruto = _ler_pares(instance_folder).get('porta', '')

    if not bruto.isdigit():
        return PORTA_PADRAO
    valor = int(bruto)
    if not (PORTA_MINIMA <= valor <= PORTA_MAXIMA):
        return PORTA_PADRAO
    return valor
