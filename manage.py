"""
Tarefas de manutenção que exigem acesso à máquina do servidor.

Uso:
    python manage.py redefinir-senha <email>

redefinir-senha: para quando o único TI esqueceu a senha e não há outro TI
para redefini-la pela tela. Gera uma senha provisória, mostra uma única vez no
terminal e obriga a troca no próximo login. Exigir o terminal do servidor é a
proteção: quem chega até aqui já controla a máquina onde o banco mora.
"""
import secrets
import sys

from werkzeug.security import generate_password_hash

from app import create_app, db
from app.helpers import normalizar_email
from app.startup import executar_startup


def redefinir_senha(email):
    from app.models import Usuario
    from sqlalchemy import func
    email = normalizar_email(email)
    user = Usuario.query.filter(func.lower(Usuario.email) == email).first() if email else None
    if not user:
        print(f'Nenhum usuario com o e-mail {email!r}.')
        return 1
    provisoria = secrets.token_urlsafe(9)
    user.senha_hash          = generate_password_hash(provisoria, method='scrypt')
    user.senha_provisoria    = True
    user.login_tentativas    = 0
    user.login_bloqueado_ate = None
    db.session.commit()
    from app.models import LogAuditoria
    db.session.add(LogAuditoria(empresa_id=user.empresa_id, usuario='terminal do servidor',
                                acao='SENHA_REDEFINIDA_TERMINAL',
                                detalhe=f'Senha de {user.re} ({email}) redefinida pelo manage.py.'))
    db.session.commit()
    print(f'Senha provisoria de {user.nome}: {provisoria}')
    print('Ela sera trocada obrigatoriamente no proximo login.')
    return 0


COMANDOS = {'redefinir-senha': redefinir_senha}


def main(argv):
    if len(argv) != 2 or argv[0] not in COMANDOS:
        print(__doc__)
        return 2
    app = create_app()
    with app.app_context():
        executar_startup()
        return COMANDOS[argv[0]](argv[1])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
