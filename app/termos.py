"""
Os termos de uso e o registro de quem aceitou.

🔴 Por que o texto tem VERSÃO:

Aceite sem versão não prova nada. Se os termos mudarem daqui a um ano e o
cadastro guardar apenas "aceitou em tal data", ninguém consegue dizer QUAL
texto a pessoa leu — e um aceite que não identifica o que foi aceito é um
registro decorativo. A versão muda toda vez que o conteúdo muda, e quem aceitou
a anterior volta a ver a tela.

🔴 Por que exigir rolar até o fim:

Um "li e aceito" que se clica sem rolar é uma mentira que o sistema registra
como verdade. Rolar até a última linha não garante leitura — nada garante —,
mas garante que o texto passou pela tela, e muda a conversa de "não me
mostraram" para "estava escrito". O botão só acende no fim, e isso é
deliberadamente incômodo.

O texto fica aqui, em dados, pelo mesmo motivo do manual: para ser testável, e
para que mudar um parágrafo não exija mexer em template.
"""
from app.sobre import AUTOR, NOME

# 🔴 Mude isto SEMPRE que mudar o texto abaixo. Quem aceitou uma versão
# anterior volta a ver a tela de aceite no próximo acesso — que é o
# comportamento certo: ninguém concordou com um texto que não leu.
VERSAO = '1.0'

TITULO = 'Termos de uso'

INTRODUCAO = (
    f'Antes de usar o {NOME}, leia e aceite as condições abaixo. Elas valem '
    'para qualquer pessoa que acesse o sistema, em qualquer perfil.'
)

CLAUSULAS = [
    (
        '1. O que é este sistema',
        f'O {NOME} registra a entrega e a devolução de equipamentos do centro '
        'de distribuição: quem levou, quando devolveu e o que voltou faltando. '
        'Os registros feitos aqui servem de base para cobrança de equipamento '
        'não devolvido, e por isso devem refletir o que realmente aconteceu.'
    ),
    (
        '2. Quem fez e de quem é',
        f'O {NOME} foi concebido, projetado e desenvolvido por {AUTOR}, que '
        'detém os direitos sobre o programa e sobre seu código-fonte. O sistema '
        'não foi encomendado, contratado nem custeado por terceiros, e não '
        'deriva de software de propriedade de outra parte.'
    ),
    (
        '3. O uso que está autorizado',
        'A empresa que recebeu este sistema pode instalá-lo e usá-lo em suas '
        'operações, sem limite de prazo, de número de usuários ou de unidades. '
        'O uso é gratuito.'
    ),
    (
        '4. O uso que não está autorizado',
        'Não está autorizado revender, sublicenciar, redistribuir a terceiros '
        'ou apresentar o sistema como obra própria. O código pode ser lido e '
        'ajustado para uso interno, mas a autoria permanece de quem o '
        'desenvolveu, e deve continuar declarada na tela "Sobre o sistema".'
    ),
    (
        '5. Outros produtos do mesmo autor',
        f'O {NOME} é um produto independente. Receber ou usar este sistema não '
        'cria direito algum sobre qualquer outro software do mesmo autor, '
        'ainda que haja semelhança de finalidade, de aparência ou de solução '
        'entre eles.'
    ),
    (
        '6. Garantia e suporte',
        'O sistema é entregue no estado em que se encontra. Não há garantia de '
        'funcionamento ininterrupto nem obrigação de manutenção, correção ou '
        'suporte. A responsabilidade por manter as cópias de segurança e por '
        'conferir os dados registrados é de quem opera o sistema.'
    ),
    (
        '7. Sua conta é sua',
        'A senha é pessoal e intransferível. Tudo o que for feito com o seu '
        'acesso fica registrado no seu nome, com data e hora, na trilha de '
        'auditoria. Emprestar o acesso a outra pessoa transfere para você a '
        'responsabilidade pelo que ela fizer.'
    ),
    (
        '8. Os dados que o sistema guarda',
        'O sistema registra nome, matrícula e as movimentações de equipamento '
        'de cada pessoa, para controle operacional. Esses dados ficam na '
        'máquina onde o sistema está instalado, dentro da rede da empresa. O '
        'sistema não envia informação para fora dessa rede e não depende de '
        'internet para funcionar.'
    ),
    (
        '9. Mudanças nestes termos',
        'Se este texto mudar, o sistema pede o aceite de novo no acesso '
        'seguinte. O aceite anterior continua registrado, com a versão e a '
        'data — ninguém concorda retroativamente com um texto que não leu.'
    ),
]

FECHO = (
    'Ao marcar o aceite, você declara que leu as condições acima e concorda '
    'com elas. A data, a hora e a versão do texto ficam registradas no seu '
    'cadastro.'
)

ROTULO_ACEITE = 'Li e aceito os termos de uso'
AVISO_ROLAR = 'Role o texto até o fim para habilitar o aceite'


def texto_completo():
    """Os termos em texto corrido — para o teste e para quem quiser exportar."""
    partes = [TITULO, INTRODUCAO]
    for titulo, corpo in CLAUSULAS:
        partes.append(f'{titulo}\n{corpo}')
    partes.append(FECHO)
    return '\n\n'.join(partes)


def precisa_aceitar(usuario):
    """True se esta pessoa ainda não aceitou a versão ATUAL dos termos.

    Fail-closed: usuário sem registro de aceite precisa aceitar. É o caso de
    quem foi criado antes de os termos existirem, e o certo é pedir — não
    presumir que concordou com algo que nunca viu.
    """
    if usuario is None:
        return False
    if not usuario.termos_aceitos_em:
        return True
    return (usuario.termos_versao or '') != VERSAO


def registrar_aceite(usuario, agora=None):
    """Grava o aceite no cadastro: quando, e de qual versão."""
    from datetime import datetime

    usuario.termos_aceitos_em = agora or datetime.now()
    usuario.termos_versao = VERSAO
    return usuario
