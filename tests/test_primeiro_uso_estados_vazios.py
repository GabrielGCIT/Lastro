"""
Testes das telas VAZIAS — o estado que a instalação nova encontra no dia 1.

Todo teste até aqui parte de uma base já semeada, então a tela vazia nunca foi
olhada. E ela não tem uma causa, tem três:

    NADA CADASTRADO   — a base é nova. A frase certa manda cadastrar.
    FILTRO SEM MATCH  — tem dado, o recorte é que não bate. A frase certa
                        oferece limpar o filtro.
    SEM ALCANCE       — tem dado, o usuário é que não enxerga nenhuma
                        localidade. A frase certa manda falar com o admin.

O teste que dá nome ao arquivo é `test_sem_alcance_nao_manda_cadastrar`: o
usuário criado sem localidade vê a tabela vazia com a base CHEIA, e mandá-lo
"cadastrar o primeiro" produz patrimônio duplicado — dano que só aparece
semanas depois, na conferência. É o cenário de quem acabou de criar os usuários
da equipe, que é exatamente o que vem pela frente.
"""
import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import Coletor, Empresa, Grupo, Localidade, Usuario


@pytest.fixture()
def usuario_sem_localidade(app, loc):
    """Nível CD sem localidade vinculada — `_escopo_geografico` devolve set().

    Não é cenário exótico: o formulário de usuário deixa a localidade em branco,
    e o nível CD é o padrão de quem trabalha no balcão.
    """
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    ti = Grupo.query.filter_by(nome='TI').first()
    u = Usuario(nome='Recém-criado', re='9900', email='novo@mb.com',
                senha_hash=generate_password_hash('x', method='scrypt'),
                grupo_id=ti.id, empresa_id=mb.id, nivel_acesso='CD',
                localidade_id=None)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def base_com_coletor(app, loc):
    c = Coletor(serial_number='SN-VAZIO', numero_patrimonio='9001',
                localidade_id=loc.id, status='Disponível')
    db.session.add(c)
    db.session.commit()
    return c


# ---------------------------------------------------------------------------
# COLETORES
# ---------------------------------------------------------------------------

def test_base_vazia_manda_cadastrar_e_nao_fala_em_filtro(admin_client, loc):
    """🔴 O achado. A base nova dizia "com os filtros aplicados" sem filtro
    nenhum — mandando o operador caçar um controle que ele não mexeu."""
    html = admin_client.get('/coletores').get_data(as_text=True)

    assert 'Nenhum coletor cadastrado ainda' in html
    assert 'Novo Ativo' in html, 'a frase precisa apontar para onde cadastrar'
    assert 'filtros aplicados' not in html


def test_filtro_sem_resultado_continua_falando_de_filtro(admin_client, base_com_coletor):
    """A outra metade: com filtro, a frase antiga estava certa e some a dica de
    cadastro (o coletor existe, só não bate o recorte)."""
    html = admin_client.get('/coletores?status=Manutenção').get_data(as_text=True)

    assert 'Nenhum coletor com esses filtros' in html
    assert 'Mostrar todos' in html
    assert 'Nenhum coletor cadastrado ainda' not in html


def test_sem_alcance_nao_manda_cadastrar(app, cliente_logado, usuario_sem_localidade,
                                         base_com_coletor):
    """🔴 O nome do arquivo: base CHEIA, tela vazia, e o convite a cadastrar
    viraria duplicata de patrimônio."""
    c = cliente_logado(usuario_sem_localidade)
    html = c.get('/coletores').get_data(as_text=True)

    # Casa com o texto de `msg.sem_alcance` (translations/pt.json). Se aquele
    # texto mudar, este assert é o lugar certo para quebrar: o que o teste
    # defende é que a tela EXPLICA a ausência em vez de convidar a cadastrar.
    assert 'CD atribuído' in html
    assert 'Nenhum coletor cadastrado ainda' not in html, \
        'mandou cadastrar um coletor que já existe'


# ---------------------------------------------------------------------------
# O CADASTRO DO PRIMEIRO COLETOR
# ---------------------------------------------------------------------------

def _cadastrar_coletor(client, **extra):
    dados = {'serial_number': 'SN-NOVO', 'numero_identificacao': '',
             'modelo': 'MC9190', 'status': 'Disponível',
             'numero_patrimonio': '5555', 'localidade_id': ''}
    dados.update(extra)
    return client.post('/coletores', data=dados, follow_redirects=True)


def test_coletor_sem_cd_nao_e_cadastrado(admin_client, loc):
    """🔴 O combo vinha em "— Sem vínculo —" e o POST aceitava."""
    html = _cadastrar_coletor(admin_client).get_data(as_text=True)

    assert Coletor.query.filter_by(serial_number='SN-NOVO').first() is None
    assert 'não aparece para a equipe' in html, 'o erro precisa dizer a consequência'


def test_coletor_com_cd_entra_normalmente(admin_client, loc):
    """A trava não pode atrapalhar o caminho certo."""
    _cadastrar_coletor(admin_client, localidade_id=str(loc.id))

    c = Coletor.query.filter_by(serial_number='SN-NOVO').first()
    assert c is not None and c.localidade_id == loc.id


def test_o_motivo_da_trava_coletor_orfao_e_invisivel(app, admin_client, loc,
                                                     usuario_sem_localidade,
                                                     cliente_logado):
    """Documenta o dano que a trava evita — e vale por si.

    Um coletor sem CD não pertence a empresa nenhuma, então não aparece para
    NINGUÉM — nem para o administrador que o cadastrou. Sem a trava, ele some no
    instante em que é salvo, e o caminho óbvio vira cadastrar de novo.
    """
    orfao = Coletor(serial_number='SN-FANTASMA', numero_patrimonio='4444',
                    localidade_id=None, status='Disponível')
    db.session.add(orfao)
    db.session.commit()

    do_admin = admin_client.get('/coletores').get_data(as_text=True)
    assert 'SN-FANTASMA' not in do_admin, 'coletor sem CD apareceu para o admin'

    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    global_mb = Usuario.query.filter_by(re='9900').first()
    global_mb.nivel_acesso = 'GLOBAL'
    global_mb.empresa_id = mb.id
    db.session.commit()

    da_equipe = cliente_logado(global_mb).get('/coletores').get_data(as_text=True)
    assert 'SN-FANTASMA' not in da_equipe, \
        'se passar a aparecer, a obrigatoriedade pode ser revista'


# ---------------------------------------------------------------------------
# O CADASTRO DOS USUÁRIOS DA EQUIPE
# ---------------------------------------------------------------------------

def _cadastrar_usuario(client, **extra):
    grupo = Grupo.query.filter_by(nome='BALCAO').first()
    dados = {'nome': 'Operador Novo', 're': '7777', 'email': 'op@mb.com',
             'senha': 'trocar123', 'grupo_id': str(grupo.id),
             'nivel_acesso': 'CD', 'localidade_id': ''}
    dados.update(extra)
    return client.post('/usuarios', data=dados, follow_redirects=True)


def test_usuario_cd_sem_localidade_nao_e_criado(admin_client, loc):
    """🔴 O formulário nasce em "CD" com "— Sem localidade definida —", então
    criar um usuário sem tocar em nada produzia um usuário CEGO.

    `_escopo_geografico` devolve set() para CD sem localidade, e set() fecha
    TODOS os módulos: ele entra, navega, e toda tela vem sem uma linha.
    """
    html = _cadastrar_usuario(admin_client).get_data(as_text=True)

    assert Usuario.query.filter_by(re='7777').first() is None
    assert 'não enxerga nenhum equipamento' in html


def test_nivel_fora_da_lista_e_recusado(admin_client, loc):
    """🔴 Antes da L2 existiam quatro níveis; agora são dois (NIVEIS_ACESSO).

    Este teste nasceu substituindo um que mandava nivel_acesso='PAIS' e checava
    que o usuário não era criado. Ele continuaria VERDE depois da poda — mas
    pelo motivo errado: a recusa passaria a vir da validação de nível, não do
    escopo vazio que o nome prometia. Então o alvo mudou junto: o que importa
    provar agora é que um nível herdado não grava.

    Sem esta guarda o estrago é mudo: a coluna é String e aceita qualquer texto,
    e `_escopo_geografico` é fail-closed — gravaria 'PAIS' sem erro nenhum e
    produziria um usuário que entra no sistema e não enxerga uma linha.
    """
    html = _cadastrar_usuario(admin_client, nivel_acesso='PAIS').get_data(as_text=True)

    assert Usuario.query.filter_by(re='7777').first() is None
    assert 'inválido' in html, 'a recusa tem de dizer por que, não só não gravar'


def test_nivel_global_grava(admin_client, loc):
    """O contraponto do teste acima: o nível que ESTÁ na lista passa."""
    _cadastrar_usuario(admin_client, nivel_acesso='GLOBAL')

    assert Usuario.query.filter_by(re='7777').first() is not None


def test_usuario_global_nao_precisa_de_escopo(admin_client, loc):
    """GLOBAL enxerga a empresa inteira — exigir escopo dele seria invenção."""
    _cadastrar_usuario(admin_client, nivel_acesso='GLOBAL')

    u = Usuario.query.filter_by(re='7777').first()
    assert u is not None and u.localidade_id is None


def test_usuario_cd_com_localidade_entra(admin_client, loc):
    _cadastrar_usuario(admin_client, localidade_id=str(loc.id))

    u = Usuario.query.filter_by(re='7777').first()
    assert u is not None and u.localidade_id == loc.id
