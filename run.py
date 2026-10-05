"""
Entrypoint do MBAssets.
Uso: python run.py

Sobe sem nenhuma variável de ambiente: o banco nasce em instance/mbassets.db e
a SECRET_KEY é gerada e guardada em instance/secret_key no primeiro boot.

MBASSETS_DEBUG=1 liga o modo de desenvolvimento (recarga automática e o
debugger do Werkzeug). Nunca ligue isso numa máquina que a rede alcança: o
debugger executa código Python enviado pelo navegador.
"""
import os

from app import create_app
from app.startup import executar_startup

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        executar_startup()

    debug = os.environ.get('MBASSETS_DEBUG') == '1'
    app.run(debug=debug, host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
