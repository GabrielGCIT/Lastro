"""
Termos de uso: o aceite, o registro e a guarda.

🔴 Este arquivo é o único que monta clientes SEM aceite de propósito. As
fixtures do `conftest` marcam os termos como aceitos, porque representam gente
operando o sistema — e quando a guarda entrou, sem essa marcação, ela desviou a
suíte INTEIRA para a tela de aceite. Isso foi a melhor prova de que ela morde.

O teste que dá nome ao arquivo é `test_aceite_guarda_a_versao_do_texto`: um
aceite que não diz QUAL texto foi aceito é um registro decorativo. Se os termos
mudarem e o cadastro só tiver a data, ninguém consegue afirmar o que a pessoa
leu.
"""
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

from app import db, termos
from app.models import Grupo, Usuario


def _usuario_sem_aceite(app, re='8001', provisoria=False):
    """Alguém criado pela TI que ainda não entrou — o estado real do dia 1."""
    ti = Grupo.query.filter_by(nome='TI').first()
    u = Usuario(nome='Pessoa Nova', re=re, email=f'{re}@mb.com',
                senha_hash=generate_password_hash('x', method='scrypt'),
                grupo_id=ti.id, empresa_id=ti.empresa_id,
                nivel_acesso='GLOBAL', senha_provisoria=provisoria)
    db.session.add(u)
    db.session.commit()
    return u


def _cliente(app, usuario):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = usuario.id
        s['nome'] = usuario.nome
        s['re'] = usuario.re
        s['empresa_id'] = usuario.empresa_id
        s['nivel_acesso'] = usuario.nivel_acesso
        s['senha_provisoria'] = bool(usuario.senha_provisoria)
    return c


# ---------------------------------------------------------------------------
# A GUARDA
# ---------------------------------------------------------------------------

def test_sem_aceite_o_sistema_nao_abre(app):
    """Qualquer tela leva ao aceite, não só a inicial."""
    c = _cliente(app, _usuario_sem_aceite(app))

    for rota in ('/', '/coletores', '/operacao', '/baterias'):
        resposta = c.get(rota)
        assert resposta.status_code == 302, f'{rota} abriu sem aceite'
        assert resposta.headers['Location'].endswith('/termos')


def test_depois_de_aceitar_o_sistema_abre(app):
    c = _cliente(app, _usuario_sem_aceite(app))

    c.post('/termos', data={'aceito': 'sim'})

    assert c.get('/').status_code == 200


def test_a_api_responde_403_em_vez_de_redirecionar(app):
    """🔴 Redirect numa chamada de API vira HTML dentro de um `fetch`.

    O modal do balcão receberia a página de termos como se fosse resposta de
    dados, e o erro apareceria como comportamento estranho da tela — não como
    "aceite os termos".
    """
    c = _cliente(app, _usuario_sem_aceite(app))

    resposta = c.get('/api/coletor/1/complementos')

    assert resposta.status_code == 403
    assert 'termos' in resposta.get_data(as_text=True).lower()


def test_a_propria_tela_de_termos_nao_entra_em_loop(app):
    """Se o hook desviasse /termos para /termos, ninguém conseguiria aceitar."""
    c = _cliente(app, _usuario_sem_aceite(app))

    assert c.get('/termos').status_code == 200


def test_sair_funciona_sem_aceitar(app):
    """Quem não concorda precisa poder ir embora — e não ficar preso na tela."""
    c = _cliente(app, _usuario_sem_aceite(app))

    assert c.get('/logout').status_code in (302, 200)


def test_a_senha_provisoria_vem_ANTES_do_aceite(app):
    """🔴 A ordem importa: trocar a senha primeiro, aceitar depois.

    Um aceite feito com a senha que a TI definiu — e que a TI conhece — é um
    aceite que o próprio dono pode contestar depois. Com a senha já trocada,
    só ele poderia ter clicado.
    """
    u = _usuario_sem_aceite(app, re='8002', provisoria=True)
    c = _cliente(app, u)

    resposta = c.get('/coletores')

    assert resposta.headers['Location'].endswith('/trocar-senha'), \
        'o aceite foi pedido antes da troca da senha provisória'


# ---------------------------------------------------------------------------
# O REGISTRO
# ---------------------------------------------------------------------------

def test_aceite_guarda_a_versao_do_texto(app):
    """🔴 O nome do arquivo: aceite sem versão não diz o que foi aceito."""
    u = _usuario_sem_aceite(app)
    c = _cliente(app, u)

    c.post('/termos', data={'aceito': 'sim'})

    db.session.refresh(u)
    assert u.termos_aceitos_em is not None, 'não registrou QUANDO'
    assert u.termos_versao == termos.VERSAO, 'não registrou QUAL texto'


def test_texto_novo_pede_aceite_de_novo(app, monkeypatch):
    """Quem aceitou a versão 1.0 não concordou com a 2.0 — ninguém concorda
    retroativamente com um texto que não leu."""
    u = _usuario_sem_aceite(app)
    termos.registrar_aceite(u)
    db.session.commit()
    assert termos.precisa_aceitar(u) is False

    monkeypatch.setattr(termos, 'VERSAO', '2.0')

    assert termos.precisa_aceitar(u) is True


def test_usuario_antigo_sem_registro_precisa_aceitar(app):
    """Fail-closed: quem existe desde antes dos termos nunca os viu."""
    u = _usuario_sem_aceite(app)
    u.termos_aceitos_em = None
    db.session.commit()

    assert termos.precisa_aceitar(u) is True


def test_o_aceite_vai_para_a_auditoria(app):
    """Registro no cadastro é o dado; o log é a trilha de quando aconteceu."""
    from app.models import LogAuditoria

    c = _cliente(app, _usuario_sem_aceite(app))
    c.post('/termos', data={'aceito': 'sim'})

    acoes = [l.acao for l in LogAuditoria.query.all()]
    assert 'TERMOS_ACEITE' in acoes


def test_post_sem_a_confirmacao_nao_registra(app):
    """🔴 O servidor não acredita na tela.

    O botão só acende depois de rolar o texto, mas um POST montado à mão
    pularia a tela inteira. A confirmação é conferida no servidor.
    """
    u = _usuario_sem_aceite(app)
    c = _cliente(app, u)

    c.post('/termos', data={})

    db.session.refresh(u)
    assert u.termos_aceitos_em is None, 'registrou aceite sem confirmação'


# ---------------------------------------------------------------------------
# A TELA
# ---------------------------------------------------------------------------

def test_a_tela_mostra_o_texto_inteiro(app):
    c = _cliente(app, _usuario_sem_aceite(app))

    html = c.get('/termos').get_data(as_text=True)

    for titulo, _corpo in termos.CLAUSULAS:
        assert titulo in html, f'a cláusula {titulo!r} não aparece'


def test_o_botao_nasce_desabilitado(app):
    """🔴 Um "li e aceito" clicável sem rolar é uma mentira que o sistema
    registraria como verdade."""
    c = _cliente(app, _usuario_sem_aceite(app))

    html = c.get('/termos').get_data(as_text=True)

    assert 'id="btn-aceitar"' in html
    assert 'disabled' in html, 'o botão de aceite já nasce clicável'


def test_a_tela_fala_das_clausulas_que_protegem_a_autoria(app):
    """As cláusulas que motivaram este trabalho precisam estar no texto que a
    pessoa aceita — não só na página Sobre, que ninguém é obrigado a abrir."""
    c = _cliente(app, _usuario_sem_aceite(app))

    html = c.get('/termos').get_data(as_text=True)

    assert 'detém os direitos' in html
    assert 'obra própria' in html
    assert 'não cria direito algum sobre qualquer outro software' in html


# ---------------------------------------------------------------------------
# O PRIMEIRO ACESSO
# ---------------------------------------------------------------------------

def test_primeiro_acesso_sem_aceite_nao_cria_administrador(client, app):
    """🔴 Sem aceite não há administrador — e o sistema segue sem ninguém.

    Deixar criar e cobrar o aceite depois abriria a janela em que existe um
    administrador que nunca concordou com nada.
    """
    from app.helpers import preparar_primeiro_acesso

    Usuario.query.delete()
    db.session.commit()
    token = preparar_primeiro_acesso()

    client.post(f'/primeiro-acesso/{token}',
                data={'nome': 'Joana', 'email': 'joana@mb.com',
                      'senha': 'escolhida1', 'senha_confirmacao': 'escolhida1'})

    assert Usuario.query.count() == 0, 'criou administrador sem aceite'


def test_a_tela_de_primeiro_acesso_traz_os_termos(client, app):
    from app.helpers import preparar_primeiro_acesso

    Usuario.query.delete()
    db.session.commit()
    token = preparar_primeiro_acesso()

    html = client.get(f'/primeiro-acesso/{token}').get_data(as_text=True)

    assert 'modalTermos' in html, 'o modal de termos não está na tela'
    assert termos.CLAUSULAS[0][0] in html, 'o texto dos termos não veio'
    assert 'btn-aceitar-modal' in html
