"""
O backup diário, disparado pelo próprio sistema.

🔴 Por que não é uma tarefa do Agendador do Windows:

Tarefa agendada é uma segunda coisa para instalar, e uma segunda coisa para dar
errado sem avisar. Ela some quando alguém "limpa" o Agendador, e para de rodar
sozinha quando a senha da conta que a executa muda — que é rotina de política de
senha em servidor corporativo. O sintoma é o pior possível: nada acontece, nada
aparece, e só se descobre no dia em que o backup faz falta.

Aqui o backup é parte do sistema. Se o serviço está no ar, ele roda. Nada a
instalar além do próprio produto, e a tela de saúde mostra quando foi o último.

A thread é marcada como daemon: ela nunca segura o encerramento do serviço. Um
backup interrompido no meio não deixa lixo, porque `fazer_backup` só considera
pronta a cópia que passou na conferência, e apaga a que não passou.
"""
import os
import threading
from datetime import datetime, time, timedelta

from app.backup import BackupFalhou, fazer_backup, limpar_antigos

# Três da manhã: o balcão está fechado e a janela de escrita é mínima. Não é
# crítico — `sqlite3.backup()` convive com escrita —, mas um backup tirado com o
# sistema parado é mais fácil de explicar para quem auditar.
HORA_DO_BACKUP = time(3, 0)

# De quanto em quanto tempo a thread acorda para perguntar "já passou das 3?".
# Cinco minutos é folgado: erra a hora exata por minutos e não consome nada.
INTERVALO_DE_CHECAGEM = timedelta(minutes=5)


def pasta_de_backups(root_dir):
    return os.path.join(root_dir, 'backups')


def precisa_rodar(agora, ultimo_backup, hora_alvo=HORA_DO_BACKUP):
    """Decide se está na hora — separado da thread para poder ser testado.

    Regra: roda quando já passou da hora de hoje E ainda não rodou hoje.

    🔴 O "ainda não rodou hoje" é o que faz o backup ACONTECER num servidor que
    passou a madrugada desligado. Se a regra fosse "só às 3h em ponto", uma
    máquina ligada às 8h nunca mais faria backup — e ninguém perceberia, porque
    não há erro nenhum nesse caso, só ausência.
    """
    if agora.time() < hora_alvo:
        return False
    if ultimo_backup is None:
        return True
    return ultimo_backup.date() < agora.date()


def rodar_uma_vez(uri, root_dir, agora=None):
    """Faz a cópia do dia e limpa as vencidas. Devolve (caminho, apagados).

    Erros são devolvidos como exceção para quem chamou decidir — a thread
    registra e segue viva; um script de linha de comando prefere falhar alto.
    """
    pasta = pasta_de_backups(root_dir)
    caminho = fazer_backup(uri, pasta, agora=agora)
    apagados = limpar_antigos(pasta, agora=agora)
    return caminho, apagados


def iniciar(app):
    """Sobe a thread do backup. Chamada uma vez, no boot do serviço.

    🔴 Não sobe no modo de desenvolvimento com recarga: o Werkzeug reinicia o
    processo a cada arquivo salvo, e cada reinício criaria mais uma thread —
    várias copiando o mesmo banco ao mesmo tempo.
    """
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        return None
    if app.config.get('TESTING'):
        return None

    from app import ROOT_DIR          # tardio: evita ciclo com app/__init__

    uri = app.config['SQLALCHEMY_DATABASE_URI']
    root = ROOT_DIR
    parar = threading.Event()

    def laco():
        ultimo = None
        while not parar.wait(INTERVALO_DE_CHECAGEM.total_seconds()):
            agora = datetime.now()
            if not precisa_rodar(agora, ultimo):
                continue
            try:
                caminho, apagados = rodar_uma_vez(uri, root, agora=agora)
                ultimo = agora
                app.logger.info('Backup do dia: %s (%d antigo(s) removido(s))',
                                os.path.basename(caminho), len(apagados))
            except BackupFalhou as erro:
                # Não marca `ultimo`: na próxima volta ele tenta de novo. Um
                # disco cheio às 3h pode ter espaço às 3h05.
                app.logger.error('BACKUP FALHOU: %s', erro)
            except Exception:                      # noqa: BLE001
                # A thread não pode morrer: se morrer, o backup para para sempre
                # e o sistema continua funcionando normalmente — ninguém nota.
                app.logger.exception('Erro inesperado no backup automático')

    thread = threading.Thread(target=laco, name='backup-diario', daemon=True)
    thread.start()
    return parar
