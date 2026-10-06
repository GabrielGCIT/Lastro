"""
L1 — acesso: quatro perfis fixos, primeiro acesso, senha provisória.

O que esta fatia promete, e cada bloco abaixo prende:
  - os perfis são QUATRO, fixos, e o seed impõe exatamente o declarado;
  - cada perfil abre o que deve e só isso (matriz tela × perfil);
  - a instalação nasce sem usuário; o administrador é criado pelo endereço de
    primeiro acesso, que só funciona com o token e só uma vez;
  - senha definida pela TI é provisória: até a troca, nada mais abre;
  - recuperação de senha: a TI redefine, ou o terminal do servidor para o
    último TI;
  - o que saiu (Owner, impersonação, reset por e-mail, /fix-admin) saiu mesmo.
"""
import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from app.models import Usuario, Grupo, Permissao, Empresa


def _usuario(perfil, re, provisoria=False, senha='segredo12', nivel='GLOBAL'):
    empresa = Empresa.query.first()
    grupo = Grupo.query.filter_by(nome=perfil, empresa_id=empresa.id).first()
    u = Usuario(nome=f'Pessoa {perfil}', re=re, email=f'{re}@mb.local',
                senha_hash=generate_password_hash(senha, method='scrypt'),
                grupo_id=grupo.id, empresa_id=empresa.id, nivel_acesso=nivel,
                senha_provisoria=provisoria)
    db.session.add(u)
    db.session.commit()
    return u


def _entrar(client, email, senha):
    return client.post('/login', data={'email': email, 'senha': senha})


# ---------------------------------------------------------------------------
# Os quatro perfis
# ---------------------------------------------------------------------------

def test_existem_exatamente_quatro_perfis(app):
    nomes = {g.nome for g in Grupo.query.all()}
    assert nomes == {'TI', 'SUPERVISOR', 'BALCAO', 'CONSULTA'}


# Cada tela protegida e quem a abre. Telas abertas a qualquer usuário logado
# (dashboard, histórico, painel, glossário, perfil) não entram: elas não têm o
# que vazar por perfil.
MATRIZ = {
    '/operacao':               {'TI', 'SUPERVISOR', 'BALCAO'},
    '/operacao/inventario':    {'TI', 'SUPERVISOR', 'BALCAO'},
    '/coletores':              {'TI', 'SUPERVISOR'},
    '/complementos':           {'TI', 'SUPERVISOR'},
    '/baterias':               {'TI', 'SUPERVISOR'},
    '/complementos/rastreio':  {'TI', 'SUPERVISOR'},
    '/admin/colaboradores/':   {'TI', 'SUPERVISOR'},
    '/exportar':               {'TI', 'SUPERVISOR', 'CONSULTA'},
    '/suspensos':              {'TI'},
    '/usuarios':               {'TI'},
    '/localidades':            {'TI'},
    '/grupos':                 {'TI'},
    '/auditoria':              {'TI'},
}


@pytest.mark.parametrize('perfil', ['TI', 'SUPERVISOR', 'BALCAO', 'CONSULTA'])
def test_cada_perfil_abre_o_que_deve_e_so_isso(app, cliente_logado, perfil):
    c = cliente_logado(_usuario(perfil, re=f'9{len(perfil)}00'))
    erros = []
    for rota, quem in MATRIZ.items():
        status = c.get(rota).status_code
        abriu = status == 200
        if abriu != (perfil in quem):
            erros.append(f'{rota}: {"abriu" if abriu else f"recusou ({status})"}')
    assert not erros, f'{perfil}: ' + '; '.join(erros)


def test_seed_impoe_exatamente_o_declarado(app):
    """🔴 Convergência EXATA: permissão a mais no banco volta ao declarado no boot.

    Antes o seed só ACRESCENTAVA — uma permissão dada à mão (ou por uma versão
    antiga) ficava para sempre. Com perfis fixos, isso é desvio, não ajuste.
    """
    from app.startup import seed_grupos_e_permissoes
    balcao = Grupo.query.filter_by(nome='BALCAO').first()
    usuarios = Permissao.query.filter_by(codigo='admin.usuarios').first()
    balcao.permissoes.append(usuarios)
    db.session.commit()

    seed_grupos_e_permissoes()

    db.session.refresh(balcao)
    assert 'admin.usuarios' not in {p.codigo for p in balcao.permissoes}


def test_permissao_que_saiu_do_codigo_some_do_banco(app):
    from app.startup import seed_grupos_e_permissoes
    db.session.add(Permissao(codigo='lote.criar', descricao='antiga', modulo='manutencao'))
    db.session.commit()
    seed_grupos_e_permissoes()
    assert Permissao.query.filter_by(codigo='lote.criar').first() is None


def test_tela_de_perfis_e_so_consulta(admin_client):
    html = admin_client.get('/grupos').get_data(as_text=True)
    assert 'Supervisor' in html and 'Balcão' in html
    assert '<form' not in html.split('</nav>')[-1], 'a tela de perfis voltou a ter formulário'


# ---------------------------------------------------------------------------
# Primeiro acesso
# ---------------------------------------------------------------------------

@pytest.fixture()
def instalacao_vazia(app):
    """A instalação como sai do boot: perfis e empresa, nenhum usuário."""
    Usuario.query.delete()
    db.session.commit()
    from app.helpers import preparar_primeiro_acesso
    return preparar_primeiro_acesso()


def test_login_de_instalacao_vazia_aponta_o_caminho(client, instalacao_vazia):
    html = client.get('/login').get_data(as_text=True)
    assert 'log do servidor' in html
    assert 'name="senha"' not in html, 'não há ninguém para autenticar'


def test_primeiro_acesso_sem_o_token_nao_existe(client, instalacao_vazia):
    assert client.get('/primeiro-acesso/token-errado').status_code == 404
    client.post('/primeiro-acesso/token-errado',
                data={'nome': 'Intruso', 'email': 'x@x.com',
                      'senha': 'segredo12', 'senha_confirmacao': 'segredo12'})
    assert Usuario.query.count() == 0


def test_primeiro_acesso_cria_o_administrador(client, instalacao_vazia):
    resp = client.post(f'/primeiro-acesso/{instalacao_vazia}',
                       data={'nome': 'Joana TI', 'email': 'Joana@MB.com',
                             'senha': 'escolhida1', 'senha_confirmacao': 'escolhida1',
                             'aceito': 'sim'})      # os termos vêm no mesmo POST
    assert resp.status_code == 302
    admin = Usuario.query.one()
    assert admin.termos_aceitos_em is not None,         'o aceite tem de ser gravado na mesma transação que cria a conta'
    assert admin.grupo.nome == 'TI'
    assert admin.nivel_acesso == 'GLOBAL'
    assert admin.email == 'joana@mb.com'
    assert admin.senha_provisoria is False, 'a senha foi escolhida pelo dono'
    assert _entrar(client, 'joana@mb.com', 'escolhida1').status_code == 302


def test_token_vale_uma_vez(client, instalacao_vazia):
    dados = {'nome': 'Joana TI', 'email': 'joana@mb.com',
             'senha': 'escolhida1', 'senha_confirmacao': 'escolhida1',
             'aceito': 'sim'}
    client.post(f'/primeiro-acesso/{instalacao_vazia}', data=dados)
    segunda = client.post(f'/primeiro-acesso/{instalacao_vazia}',
                          data=dict(dados, email='outro@mb.com'))
    assert segunda.status_code == 404
    assert Usuario.query.count() == 1


def test_primeiro_acesso_recusa_senha_curta(client, instalacao_vazia):
    resp = client.post(f'/primeiro-acesso/{instalacao_vazia}',
                       data={'nome': 'Joana', 'email': 'joana@mb.com',
                             'senha': 'curta', 'senha_confirmacao': 'curta'})
    assert resp.status_code == 200
    assert 'pelo menos 8' in resp.get_data(as_text=True)
    assert Usuario.query.count() == 0


def test_reinicio_reaproveita_o_mesmo_token(app, instalacao_vazia):
    """O endereço anotado no primeiro boot continua valendo depois de reiniciar."""
    from app.helpers import preparar_primeiro_acesso
    assert preparar_primeiro_acesso() == instalacao_vazia


def test_com_usuario_nao_ha_primeiro_acesso(app, client):
    from app.helpers import preparar_primeiro_acesso
    assert preparar_primeiro_acesso() is None
    assert client.get('/primeiro-acesso/qualquer').status_code == 404


def test_o_boot_nao_cria_usuario(app):
    """O administrador nasce no primeiro acesso, nunca do boot com senha sorteada."""
    from app.startup import executar_startup
    Usuario.query.delete()
    db.session.commit()
    executar_startup()
    assert Usuario.query.count() == 0


# ---------------------------------------------------------------------------
# Senha provisória
# ---------------------------------------------------------------------------

def test_usuario_criado_pela_ti_nasce_com_senha_provisoria(admin_client):
    balcao = Grupo.query.filter_by(nome='BALCAO').first()
    admin_client.post('/usuarios', data={'nome': 'Novo', 're': '6001', 'email': 'novo@mb.com',
                                         'senha': 'provisoria1', 'grupo_id': balcao.id,
                                         'nivel_acesso': 'GLOBAL'})
    novo = Usuario.query.filter_by(re='6001').first()
    assert novo.senha_provisoria is True


def test_com_senha_provisoria_so_abre_a_troca(client):
    u = _usuario('BALCAO', re='6002', provisoria=True)
    resp = _entrar(client, u.email, 'segredo12')
    assert resp.headers['Location'].endswith('/trocar-senha')
    for rota in ('/', '/operacao', '/historico', '/perfil'):
        r = client.get(rota)
        assert r.status_code == 302 and r.headers['Location'].endswith('/trocar-senha'), rota
    api = client.get('/api/polling/snapshot')
    assert api.status_code == 403, 'a API não pode ser a porta dos fundos da senha provisória'


def test_troca_libera_a_navegacao(client):
    """🔴 A ordem é: trocar a senha, DEPOIS aceitar os termos.

    O aceite exigido antes da troca seria feito com a senha que a TI definiu —
    e que a TI conhece. Um aceite assim o próprio dono pode contestar. Com a
    senha já trocada, só ele poderia ter clicado.
    """
    u = _usuario('BALCAO', re='6003', provisoria=True)
    _entrar(client, u.email, 'segredo12')
    resp = client.post('/trocar-senha', data={'senha_nova': 'minhaSenha9',
                                              'senha_confirmacao': 'minhaSenha9'})
    assert resp.headers['Location'].endswith('/')
    db.session.refresh(u)
    assert u.senha_provisoria is False
    assert check_password_hash(u.senha_hash, 'minhaSenha9')

    # Senha trocada, termos ainda não: a navegação cai no aceite.
    desviado = client.get('/operacao')
    assert desviado.status_code == 302
    assert desviado.headers['Location'].endswith('/termos')

    client.post('/termos', data={'aceito': 'sim'})
    db.session.refresh(u)
    assert u.termos_aceitos_em is not None
    assert client.get('/operacao').status_code == 200


@pytest.mark.parametrize('nova,erro', [
    ('curta', 'pelo menos 8'),
    ('segredo12', 'diferente da provisória'),
])
def test_troca_recusa_senha_que_nao_serve(client, nova, erro):
    u = _usuario('BALCAO', re='6004', provisoria=True)
    _entrar(client, u.email, 'segredo12')
    resp = client.post('/trocar-senha', data={'senha_nova': nova, 'senha_confirmacao': nova})
    assert erro in resp.get_data(as_text=True)
    db.session.refresh(u)
    assert u.senha_provisoria is True


def test_ti_redefine_a_senha_de_outro_e_ela_sai_provisoria(admin_client):
    u = _usuario('BALCAO', re='6005')
    u.login_bloqueado_ate = __import__('datetime').datetime.now()
    db.session.commit()
    admin_client.post('/usuarios', data={'id': u.id, 'nome': u.nome, 're': u.re,
                                         'email': u.email, 'senha': 'redefinida1',
                                         'grupo_id': u.grupo_id, 'nivel_acesso': 'GLOBAL'})
    db.session.refresh(u)
    assert u.senha_provisoria is True
    assert u.login_bloqueado_ate is None, 'redefinir também desbloqueia'
    assert check_password_hash(u.senha_hash, 'redefinida1')


def test_a_propria_senha_trocada_na_tela_de_usuarios_nao_fica_provisoria(admin_client):
    admin = Usuario.query.filter_by(re='admin').first()
    admin_client.post('/usuarios', data={'id': admin.id, 'nome': admin.nome, 're': admin.re,
                                         'email': admin.email, 'senha': 'outraSenha9',
                                         'grupo_id': admin.grupo_id, 'nivel_acesso': 'GLOBAL'})
    db.session.refresh(admin)
    assert admin.senha_provisoria is False


def test_criar_usuario_sem_senha_nao_quebra(admin_client):
    """Antes: generate_password_hash(None) → erro 500."""
    balcao = Grupo.query.filter_by(nome='BALCAO').first()
    resp = admin_client.post('/usuarios', data={'nome': 'Sem Senha', 're': '6006',
                                                'email': 'sem@mb.com', 'grupo_id': balcao.id,
                                                'nivel_acesso': 'GLOBAL'}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'pelo menos 8' in resp.get_data(as_text=True)
    assert Usuario.query.filter_by(re='6006').first() is None


def test_tela_de_usuarios_mostra_quem_nao_trocou_a_senha(admin_client):
    _usuario('BALCAO', re='6007', provisoria=True)
    html = admin_client.get('/usuarios').get_data(as_text=True)
    assert 'Aguardando troca de senha' in html


# ---------------------------------------------------------------------------
# Recuperação de senha
# ---------------------------------------------------------------------------

def test_esqueci_a_senha_manda_falar_com_a_ti(client):
    html = client.get('/esqueci-senha').get_data(as_text=True)
    assert 'TI' in html
    assert 'name="email"' not in html, 'não existe e-mail para mandar o link'


def test_terminal_redefine_a_senha_do_ultimo_ti(app, capsys):
    import manage
    admin = Usuario.query.filter_by(re='admin').first()
    admin.login_bloqueado_ate = __import__('datetime').datetime.now()
    db.session.commit()

    assert manage.redefinir_senha('ADMIN@mbassets.local') == 0

    provisoria = capsys.readouterr().out.split(': ', 1)[1].split()[0]
    db.session.refresh(admin)
    assert admin.senha_provisoria is True
    assert admin.login_bloqueado_ate is None
    assert check_password_hash(admin.senha_hash, provisoria)


def test_terminal_com_email_desconhecido_nao_mexe_em_nada(app):
    import manage
    assert manage.redefinir_senha('ninguem@mb.local') == 1


# ---------------------------------------------------------------------------
# O que saiu, saiu
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('rota', ['/fix-admin', '/redefinir-senha/qualquer', '/paises',
                                  '/usuarios/1/revisar', '/grupos/TI/restaurar'])
def test_rotas_removidas_nao_respondem(admin_client, rota):
    assert admin_client.get(rota).status_code in (404, 405)
    assert admin_client.post(rota).status_code == 404


def test_modelo_nao_tem_mais_owner_nem_suspensao(app):
    assert not hasattr(Usuario, 'is_owner')
    assert not hasattr(Usuario, 'criado_em_campo')
    assert not hasattr(Usuario, 'reset_token_hash')
    assert not hasattr(Empresa, 'ativa')


# ---------------------------------------------------------------------------
# A permissão protege a AÇÃO, não só o link do menu
# ---------------------------------------------------------------------------

def test_consulta_nao_registra_retirada_nem_devolucao_por_post(app, cliente_logado, loc):
    """🔴 Achado pela matriz: as rotas do balcão só exigiam login. A permissão
    escondia o link, e um POST direto de qualquer perfil registrava a retirada."""
    from app.models import Coletor, Colaborador, Movimentacao
    col = Coletor(serial_number='SN-L1', numero_patrimonio='L1', status='Disponível',
                  localidade_id=loc.id)
    db.session.add_all([col, Colaborador(re='8100', nome='Quem Leva',
                                         empresa_id=loc.empresa_id)])
    db.session.commit()
    c = cliente_logado(_usuario('CONSULTA', re='6100'))

    c.post('/operacao/retirar', data={
        'busca_valor': 'L1', 'busca_modo': 'patrimonio', 're_colaborador': '8100',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true'})
    c.post('/operacao/devolver', data={'busca_valor': 'L1', 'busca_modo': 'patrimonio'})
    c.post('/operacao/inventario', data={'id': col.id, 'status': 'Manutenção'})

    db.session.refresh(col)
    assert col.status == 'Disponível'
    assert Movimentacao.query.count() == 0


def test_balcao_continua_registrando_retirada(app, cliente_logado, loc):
    """A guarda não pode travar quem tem a permissão."""
    from app.models import Coletor, Colaborador, Movimentacao
    col = Coletor(serial_number='SN-L1B', numero_patrimonio='L1B', status='Disponível',
                  localidade_id=loc.id)
    db.session.add_all([col, Colaborador(re='8101', nome='Quem Leva',
                                         empresa_id=loc.empresa_id)])
    db.session.commit()
    c = cliente_logado(_usuario('BALCAO', re='6101'))
    c.post('/operacao/retirar', data={
        'busca_valor': 'L1B', 'busca_modo': 'patrimonio', 're_colaborador': '8101',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true'})
    db.session.refresh(col)
    assert col.status == 'Em Uso'
    assert Movimentacao.query.count() == 1
