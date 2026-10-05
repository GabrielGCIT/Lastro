"""
Envio de e-mail transacional com backend plugável (T2).

O sistema ainda não tem provedor de e-mail em produção — o backend padrão
('console') apenas imprime a mensagem no stdout, o que basta para dev e para
o fluxo de "esqueci minha senha" não depender de infraestrutura externa.
Quando o P26 (notificações) entrar, basta registrar o backend real (Brevo/
Resend/SMTP) em _BACKENDS e apontar MAIL_BACKEND no .env — nenhum chamador
precisa mudar.

Contrato: enviar_email() NUNCA propaga exceção. Falha de envio é logada e
devolve False — fluxos de negócio (reset de senha, convites futuros) não
podem quebrar porque o provedor de e-mail está fora do ar.
"""
import os


def _backend_console(destinatario: str, assunto: str, corpo: str) -> bool:
    """Backend de desenvolvimento: imprime o e-mail no stdout."""
    print('=' * 60)
    print(f'[MAIL console] Para:    {destinatario}')
    print(f'[MAIL console] Assunto: {assunto}')
    print('-' * 60)
    print(corpo)
    print('=' * 60)
    return True


# Registro de backends disponíveis. O P26 adiciona aqui o provedor real
# (ex: 'brevo', 'smtp') sem tocar em nenhum chamador.
_BACKENDS = {
    'console': _backend_console,
}


def enviar_email(destinatario: str, assunto: str, corpo: str) -> bool:
    """Envia um e-mail pelo backend configurado em MAIL_BACKEND (default: console).

    Retorna True se o backend reportou sucesso, False em qualquer falha —
    inclusive backend desconhecido ou exceção interna. Nunca levanta exceção:
    o chamador decide se loga/avisa, mas o fluxo dele segue.
    """
    nome = os.environ.get('MAIL_BACKEND', 'console').strip().lower()
    backend = _BACKENDS.get(nome)
    if backend is None:
        print(f"[MAIL ERROR] Backend '{nome}' desconhecido — e-mail não enviado.")
        return False
    try:
        return bool(backend(destinatario, assunto, corpo))
    except Exception as exc:
        print(f'[MAIL ERROR] Falha no envio via {nome}: {exc}')
        return False
