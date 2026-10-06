"""
Testes da barra "Vendo:" depois do achatamento da geografia (L2).

🔴 Este arquivo existe porque o POST verde não prova que o usuário enxerga.
A L2 trocou a condição da barra de `nivel_acesso != 'CD'` para `== 'GLOBAL'` e
trocou a fonte dos dados: antes o context processor carregava Américas e Países
(com `selectinload` de duas camadas), agora carrega as Localidades da empresa.
Um erro em qualquer das duas pontas não quebra request nenhum — o Jinja itera
uma variável inexistente como lista vazia, e a barra aparece sem opção alguma,
ou não aparece.

O teste que dá nome ao arquivo é `test_global_ve_o_cd_no_seletor`: sem ele, a
barra renderizaria a casca (o rótulo, o selo, o `<select>`) com zero opções, e
um usuário que alcança vários CDs perderia a única forma de escolher um — sem
erro no log e sem nada de vermelho na suíte.
"""
from werkzeug.security import generate_password_hash

from app import db
from app.startup import NOME_EMPRESA
from app.models import Empresa, Grupo, Usuario


def _usuario(nome, re, nivel, empresa, grupo, localidade_id=None):
    u = Usuario(nome=nome, re=re, email=f'{re}@mb.local',
                senha_hash=generate_password_hash('x', method='scrypt'),
                grupo_id=grupo.id, empresa_id=empresa.id,
                nivel_acesso=nivel, localidade_id=localidade_id)
    db.session.add(u)
    db.session.commit()
    return u


def test_global_ve_o_cd_no_seletor(app, cliente_logado, loc):
    """🔴 O nome do arquivo: a barra tem de trazer o CD, não só a casca."""
    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    ti = Grupo.query.filter_by(nome='TI', empresa_id=mb.id).first()
    c = cliente_logado(_usuario('Gestor', '9001', 'GLOBAL', mb, ti))

    html = c.get('/usuarios').get_data(as_text=True)

    assert '/contexto/trocar' in html, 'a barra não apareceu para GLOBAL'
    assert f'value="CD:{loc.id}"' in html, \
        'a barra apareceu sem o CD — seletor com zero opções'


def test_cd_nao_ve_a_barra(app, cliente_logado, loc):
    """Quem é de um CD não tem o que escolher: a barra some, não fica vazia.

    Um seletor de uma opção só é pior que nenhum — convida ao clique e não faz
    nada.
    """
    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    ti = Grupo.query.filter_by(nome='TI', empresa_id=mb.id).first()
    c = cliente_logado(_usuario('Operador', '9002', 'CD', mb, ti, loc.id))

    html = c.get('/usuarios').get_data(as_text=True)

    assert '/contexto/trocar' not in html, 'a barra apareceu para nível CD'


def test_a_barra_nao_traz_cd_de_outra_empresa(app, cliente_logado, duas_empresas):
    """O recorte por empresa vale na barra também.

    GLOBAL é global DA empresa, não do banco. Sem o filtro no context processor,
    o seletor ofereceria o CD do outro tenant — e a recusa só apareceria depois,
    no POST, como "fora do que você pode ver".
    """
    du = duas_empresas
    c = cliente_logado(du.userA)
    with c.session_transaction() as s:
        s['nivel_acesso'] = 'GLOBAL'

    html = c.get('/usuarios').get_data(as_text=True)

    assert f'value="CD:{du.locB.id}"' not in html, \
        'a barra ofereceu o CD de outra empresa'
