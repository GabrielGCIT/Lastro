"""Vínculo Pista ↔ Colaborador: a retirada de coletor só aceita RE cadastrado e ativo.

O campo de RE segue sendo texto livre (bipe do crachá) — quem valida é o submit.
Estes testes batem direto no POST, que é também a superfície exposta a quem
ignora o JS da tela.
"""
from app import db
from app.models import Coletor, Colaborador, Movimentacao


CHECKLIST_OK = {
    'estado_visual':     'true',
    'bateria_ok':        'true',
    'leitura_ok':        'true',
    'identificacao_ok':  'true',
    'equipamento_limpo': 'true',
}


def _coletor(loc, serial='SN1', patrimonio='100', **kwargs):
    c = Coletor(serial_number=serial, numero_patrimonio=patrimonio,
                status='Disponível', localidade_id=loc.id, **kwargs)
    db.session.add(c)
    db.session.commit()
    return c


def _colaborador(re='9001', nome='Maria Souza', **kwargs):
    # Nasce no tenant MB (mesma empresa do admin/loc dos testes) — a unicidade de
    # RE e os lookups de movimentação passaram a ser POR EMPRESA (S1); um
    # colaborador órfão não seria visto pela sessão MB e falsearia o cenário.
    if 'empresa_id' not in kwargs:
        from app.models import Empresa
        mb = Empresa.query.filter_by(nome='Martin Brower').first()
        kwargs['empresa_id'] = mb.id if mb else None
    c = Colaborador(re=re, nome=nome, **kwargs)
    db.session.add(c)
    db.session.commit()
    return c


def _retirar(client, coletor, re):
    return client.post('/operacao/retirar', data={
        'busca_valor': coletor.numero_patrimonio,
        'busca_modo': 'patrimonio',
        're_colaborador': re,
        **CHECKLIST_OK,
    }, follow_redirects=True)


# --- validação de RE no submit -------------------------------------------

def test_retirada_grava_fk_do_colaborador(admin_client, loc):
    colab   = _colaborador()
    coletor = _coletor(loc)

    _retirar(admin_client, coletor, '9001')
    db.session.expire_all()

    assert coletor.status == 'Em Uso'
    mov = Movimentacao.query.filter_by(coletor_id=coletor.id).one()
    assert mov.colaborador_id == colab.id
    assert mov.re_colaborador == '9001'
    assert mov.colaborador.nome == 'Maria Souza'


def test_re_nao_cadastrado_bloqueia_retirada(admin_client, loc):
    coletor = _coletor(loc)

    r = _retirar(admin_client, coletor, '9999')
    db.session.expire_all()

    assert 'não está cadastrado como colaborador' in r.get_data(as_text=True)
    assert coletor.status == 'Disponível'          # não saiu da prateleira
    assert Movimentacao.query.count() == 0


def test_colaborador_inativo_bloqueia_retirada(admin_client, loc):
    _colaborador(re='9002', nome='João Desligado', ativo=False)
    coletor = _coletor(loc)

    r = _retirar(admin_client, coletor, '9002')
    db.session.expire_all()

    assert 'está inativo' in r.get_data(as_text=True)
    assert coletor.status == 'Disponível'
    assert Movimentacao.query.count() == 0


def test_re_invalido_bloqueia_antes_do_lookup(admin_client, loc):
    coletor = _coletor(loc)

    r = _retirar(admin_client, coletor, 'abc')
    db.session.expire_all()

    assert 'RE do colaborador inválido' in r.get_data(as_text=True)
    assert coletor.status == 'Disponível'


# --- trava de câmara agora lê Colaborador.camara --------------------------

def test_camara_divergente_bloqueia(admin_client, loc):
    _colaborador(re='9003', nome='Ana Seco', camara='SECO')
    coletor = _coletor(loc, camara='CONGELADO')

    r = _retirar(admin_client, coletor, '9003')
    db.session.expire_all()

    assert 'opera na câmara SECO' in r.get_data(as_text=True)
    assert coletor.status == 'Disponível'
    assert Movimentacao.query.count() == 0


def test_camara_nula_no_colaborador_nao_bloqueia(admin_client, loc):
    """Transição gradual: câmara só trava quando os DOIS lados a definem."""
    _colaborador(re='9004', nome='Sem Camara')
    coletor = _coletor(loc, camara='CONGELADO')

    _retirar(admin_client, coletor, '9004')
    db.session.expire_all()

    assert coletor.status == 'Em Uso'


def test_camara_igual_permite(admin_client, loc):
    _colaborador(re='9005', nome='Bia Frio', camara='CONGELADO')
    coletor = _coletor(loc, camara='CONGELADO')

    _retirar(admin_client, coletor, '9005')
    db.session.expire_all()

    assert coletor.status == 'Em Uso'


# --- API de lookup por RE exato -------------------------------------------

def test_api_re_encontra_colaborador_ativo(admin_client, app):
    _colaborador(re='9006', nome='Carlos Lima', cargo='Separador')

    r = admin_client.get('/admin/colaboradores/api/re/9006')

    assert r.status_code == 200
    assert r.get_json() == {
        'encontrado': True, 'id': Colaborador.query.filter_by(re='9006').one().id,
        're': '9006', 'nome': 'Carlos Lima', 'cargo': 'Separador',
    }


def test_api_re_inexistente_retorna_motivo(admin_client, app):
    r = admin_client.get('/admin/colaboradores/api/re/8888')

    assert r.status_code == 404
    assert r.get_json()['motivo'] == 'nao_cadastrado'


def test_api_re_inativo_retorna_motivo_e_nome(admin_client, app):
    _colaborador(re='9007', nome='Zé Inativo', ativo=False)

    r = admin_client.get('/admin/colaboradores/api/re/9007')

    assert r.status_code == 404
    body = r.get_json()
    assert body['encontrado'] is False
    assert body['motivo'] == 'inativo'
    assert body['nome'] == 'Zé Inativo'


# --- cadastro rápido em campo ---------------------------------------------

def test_criar_campo_cadastra_colaborador(admin_client, app):
    r = admin_client.post('/admin/colaboradores/api/criar-campo',
                          data={'nome': 'Nova Pessoa', 're': '9100'})

    assert r.get_json()['ok'] is True
    colab = Colaborador.query.filter_by(re='9100').one()
    assert colab.nome == 'Nova Pessoa'
    assert colab.ativo is True


def test_criar_campo_recusa_re_curto(admin_client, app):
    r = admin_client.post('/admin/colaboradores/api/criar-campo',
                          data={'nome': 'Zé', 're': '12'})

    assert r.get_json()['ok'] is False
    assert Colaborador.query.filter_by(re='12').first() is None


def test_criar_campo_recusa_re_nao_numerico(admin_client, app):
    r = admin_client.post('/admin/colaboradores/api/criar-campo',
                          data={'nome': 'Zé', 're': 'ABCD'})

    assert r.get_json()['ok'] is False
    assert Colaborador.query.filter_by(re='ABCD').first() is None


def test_criar_campo_recusa_re_duplicado(admin_client, app):
    _colaborador(re='9101', nome='Já Existe')

    r = admin_client.post('/admin/colaboradores/api/criar-campo',
                          data={'nome': 'Outro Nome', 're': '9101'})

    assert r.get_json()['ok'] is False
    assert 'já está cadastrado' in r.get_json()['erro']
    assert Colaborador.query.filter_by(re='9101').one().nome == 'Já Existe'


def test_criar_campo_aponta_inativo_para_a_ti(admin_client, app):
    _colaborador(re='9102', nome='Desligado', ativo=False)

    r = admin_client.post('/admin/colaboradores/api/criar-campo',
                          data={'nome': 'Desligado', 're': '9102'})

    assert r.get_json()['ok'] is False
    assert 'inativo' in r.get_json()['erro']


def test_criar_campo_depois_retira(admin_client, loc):
    """Fluxo completo do atalho: RE não existe → cadastra → retira."""
    coletor = _coletor(loc)

    bloqueio = _retirar(admin_client, coletor, '9200')
    assert 'não está cadastrado' in bloqueio.get_data(as_text=True)

    admin_client.post('/admin/colaboradores/api/criar-campo',
                      data={'nome': 'Recém Cadastrado', 're': '9200'})
    _retirar(admin_client, coletor, '9200')
    db.session.expire_all()

    assert coletor.status == 'Em Uso'
    mov = Movimentacao.query.filter_by(coletor_id=coletor.id).one()
    assert mov.colaborador.nome == 'Recém Cadastrado'
