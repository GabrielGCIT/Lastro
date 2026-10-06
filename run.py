"""
Entrypoint do Lastro.
Uso: python run.py

Sobe sem nenhuma variável de ambiente: o banco nasce em instance/lastro.db e
a SECRET_KEY é gerada e guardada em instance/secret_key no primeiro boot.

Enquanto a instalação não tem usuário, o boot imprime o endereço de primeiro
acesso — é por ele que se cria o administrador.

LASTRO_DEBUG=1 liga o modo de desenvolvimento (recarga automática e o
debugger do Werkzeug). Nunca ligue isso numa máquina que a rede alcança: o
debugger executa código Python enviado pelo navegador.
"""
import os

from app import ROOT_DIR, create_app
from app.helpers import preparar_primeiro_acesso
from app.registro import anunciar, configurar as configurar_log
from app.startup import executar_startup

app = create_app()


def anunciar_primeiro_acesso(porta):
    token = preparar_primeiro_acesso()
    if not token:
        return
    # `anunciar` imprime com flush: sem isso estas linhas podem nunca aparecer,
    # e quem instala fica olhando uma tela preta sem saber o endereco de acesso.
    anunciar('=' * 72)
    anunciar('[PRIMEIRO ACESSO] Esta instalacao ainda nao tem administrador.')
    anunciar('[PRIMEIRO ACESSO] Abra no navegador, nesta maquina ou na rede:')
    anunciar(f'[PRIMEIRO ACESSO]   http://<endereco-do-servidor>:{porta}/primeiro-acesso/{token}')
    anunciar(f'[PRIMEIRO ACESSO]   (nesta maquina: http://127.0.0.1:{porta}/primeiro-acesso/{token})')
    anunciar('[PRIMEIRO ACESSO] O endereco deixa de valer assim que o administrador for criado.')
    anunciar('=' * 72)


if __name__ == '__main__':
    porta = int(os.environ.get('PORT', 5001))

    arquivo_log = configurar_log(app, ROOT_DIR)
    if arquivo_log:
        anunciar(f'[Lastro] Registro de eventos em {arquivo_log}')
    else:
        anunciar('[Lastro] AVISO: nao consegui gravar o arquivo de log. '
                 'O sistema sobe assim mesmo; veja a tela de Saude do sistema.')

    with app.app_context():
        executar_startup()
        anunciar_primeiro_acesso(porta)

    # O backup diário sobe junto com o serviço: nada a agendar por fora.
    from app.agendador import iniciar as iniciar_backup
    iniciar_backup(app)

    if os.environ.get('LASTRO_DEBUG') == '1':
        # Só para desenvolver: recarga automática e o debugger do Werkzeug.
        app.run(debug=True, host='0.0.0.0', port=porta)
    else:
        # 🔴 `app.run()` é o servidor de DESENVOLVIMENTO do Flask: atende um
        # pedido por vez e imprime um aviso em letras garrafais dizendo para não
        # usá-lo em produção. No balcão há vários postos bipando ao mesmo tempo,
        # e um deles esperando o outro terminar é fila na pista.
        from waitress import serve
        app.logger.info('Lastro iniciado na porta %s', porta)
        anunciar(f'[Lastro] No ar na porta {porta}. Esta janela pode ficar aberta.')
        serve(app, host='0.0.0.0', port=porta, threads=8,
              ident='Lastro')
