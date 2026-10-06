"""
Entrypoint do MBAssets.
Uso: python run.py

Sobe sem nenhuma variável de ambiente: o banco nasce em instance/mbassets.db e
a SECRET_KEY é gerada e guardada em instance/secret_key no primeiro boot.

Enquanto a instalação não tem usuário, o boot imprime o endereço de primeiro
acesso — é por ele que se cria o administrador.

MBASSETS_DEBUG=1 liga o modo de desenvolvimento (recarga automática e o
debugger do Werkzeug). Nunca ligue isso numa máquina que a rede alcança: o
debugger executa código Python enviado pelo navegador.
"""
import os

from app import create_app
from app.helpers import preparar_primeiro_acesso
from app.startup import executar_startup

app = create_app()


def anunciar_primeiro_acesso(porta):
    token = preparar_primeiro_acesso()
    if not token:
        return
    print('=' * 72)
    print('[PRIMEIRO ACESSO] Esta instalacao ainda nao tem administrador.')
    print('[PRIMEIRO ACESSO] Abra no navegador, nesta maquina ou na rede:')
    print(f'[PRIMEIRO ACESSO]   http://<endereco-do-servidor>:{porta}/primeiro-acesso/{token}')
    print(f'[PRIMEIRO ACESSO]   (nesta maquina: http://127.0.0.1:{porta}/primeiro-acesso/{token})')
    print('[PRIMEIRO ACESSO] O endereco deixa de valer assim que o administrador for criado.')
    print('=' * 72)


if __name__ == '__main__':
    porta = int(os.environ.get('PORT', 5001))
    with app.app_context():
        executar_startup()
        anunciar_primeiro_acesso(porta)

    # O backup diário sobe junto com o serviço: nada a agendar por fora.
    from app.agendador import iniciar as iniciar_backup
    iniciar_backup(app)

    debug = os.environ.get('MBASSETS_DEBUG') == '1'
    app.run(debug=debug, host='0.0.0.0', port=porta)
