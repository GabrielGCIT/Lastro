"""
Testes da B6 — desativar e excluir coletor.

A única fatia que apaga dado. As três decisões que ela implementa:

    DESATIVAR   sempre disponível, reversível, motivo obrigatório.
    EXCLUIR     só para o que NUNCA foi usado. Item com movimentação não se
                apaga — a trilha de auditoria não pode ter buraco.
    SENHA       só na exclusão. Desativar já pede motivo e dá para desfazer.

O teste que dá nome à fatia é `test_item_com_historico_nao_se_exclui`: é a regra
escolhida contra a alternativa mais simples (apagar e pronto).
"""
import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.desativacao import (ExclusaoRecusada, desativar, excluir, historico_de,
                             pode_excluir, reativar, senha_confere)
from app.models import (Coletor, Empresa, ItemComplementar, LogAuditoria,
                        Movimentacao, Usuario, CategoriaComplemento,
                        COMPLEMENTO_UNIDADE)

SENHA = 'segredo-do-teste'


@pytest.fixture()
def cena(app, loc):
    """Um coletor virgem e um coletor com histórico."""
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    virgem = Coletor(serial_number='SN-VIRGEM', numero_patrimonio='9100',
                     localidade_id=loc.id)
    usado = Coletor(serial_number='SN-USADO', numero_patrimonio='9200',
                    localidade_id=loc.id)
    db.session.add_all([virgem, usado])
    db.session.flush()
    db.session.add(Movimentacao(coletor_id=usado.id, re_colaborador='7777'))
    db.session.commit()
    return SimpleNamespace(mb=mb, loc=loc, virgem=virgem, usado=usado)


@pytest.fixture()
def operador(app):
    """O usuário logado, com senha conhecida — a exclusão vai pedi-la."""
    ti = __import__('app.models', fromlist=['Grupo']).Grupo.query.filter_by(nome='TI').first()
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    u = Usuario(nome='Operadora B6', re='6600', email='b6@teste.local',
                senha_hash=generate_password_hash(SENHA, method='scrypt'),
                grupo_id=ti.id, empresa_id=mb.id, nivel_acesso='GLOBAL')
    db.session.add(u)
    db.session.commit()
    return u


# ---------------------------------------------------------------------------
# A REGRA CENTRAL
# ---------------------------------------------------------------------------

def test_item_com_historico_nao_se_exclui(app, cena, operador):
    """🔴 O nome da fatia. Com movimentação, o caminho é desativar.

    A alternativa simples — apagar e deixar o histórico órfão — foi recusada:
    uma trilha de auditoria com buraco não serve para provar nada.
    """
    assert historico_de(cena.usado), 'o cenário precisa ter histórico'

    with pytest.raises(ExclusaoRecusada) as erro:
        excluir(cena.usado, operador, SENHA)

    assert 'histórico' in str(erro.value)
    assert 'Desative' in str(erro.value), 'a recusa precisa dizer qual é a saída'
    assert db.session.get(Coletor, cena.usado.id) is not None


def test_item_virgem_se_exclui_e_deixa_lapide(app, cena, operador):
    """Cadastro errado se apaga — e o registro do apagamento é o que sobra.

    🔴 A lápide entra na MESMA transação do delete. Se fosse best-effort, como o
    `registrar_log` comum, um erro no log deixaria o item apagado sem rastro
    nenhum — exatamente onde a trilha mais importa.
    """
    ident = cena.virgem.id
    assert pode_excluir(cena.virgem)

    excluir(cena.virgem, operador, SENHA, cena.mb.id)

    assert db.session.get(Coletor, ident) is None
    lapide = (LogAuditoria.query.filter_by(acao='EXCLUSAO_DEFINITIVA')
              .order_by(LogAuditoria.id.desc()).first())
    assert lapide is not None, 'o apagamento não deixou registro'
    assert '9100' in lapide.detalhe, 'a lápide precisa dizer o que o item era'
    assert lapide.user_re == operador.re


def test_exclusao_sem_a_senha_certa_nao_acontece(app, cena, operador):
    """A senha responde "é você mesmo?" — a estação destravada é o cenário real."""
    with pytest.raises(ExclusaoRecusada) as erro:
        excluir(cena.virgem, operador, 'senha-errada')

    assert 'Senha' in str(erro.value)
    assert db.session.get(Coletor, cena.virgem.id) is not None
    assert LogAuditoria.query.filter_by(acao='EXCLUSAO_DEFINITIVA').count() == 0, \
        'recusou a exclusão mas gravou a lápide'


def test_senha_confere_e_o_hash_de_verdade(app, operador):
    assert senha_confere(operador, SENHA) is True
    assert senha_confere(operador, SENHA.upper()) is False
    assert senha_confere(operador, '') is False
    assert senha_confere(None, SENHA) is False


# ---------------------------------------------------------------------------
# DESATIVAR
# ---------------------------------------------------------------------------

def test_desativar_exige_motivo(app, cena, operador):
    """Sem motivo, o item vira algo que sumiu e ninguém sabe por quê."""
    for vazio in (None, '', '   '):
        with pytest.raises(ExclusaoRecusada):
            desativar(cena.usado, vazio, operador)
    assert cena.usado.esta_ativo


def test_desativar_guarda_quem_quando_e_por_que(app, cena, operador):
    desativar(cena.usado, 'Quebrado sem conserto', operador)

    assert cena.usado.esta_ativo is False
    assert cena.usado.desativado_motivo == 'Quebrado sem conserto'
    assert cena.usado.desativado_por_id == operador.id
    assert cena.usado.desativado_em is not None


def test_desativar_nao_encosta_no_historico(app, cena, operador):
    """O que já saiu continua cobrável — desativar é sobre o FUTURO do item."""
    antes = Movimentacao.query.filter_by(coletor_id=cena.usado.id).count()
    desativar(cena.usado, 'Aposentado', operador)
    assert Movimentacao.query.filter_by(coletor_id=cena.usado.id).count() == antes


def test_voltar_a_operacao_limpa_os_tres_campos(app, cena, operador):
    """🔴 Os três voltam a nulo juntos.

    Um `desativado_motivo` sobrevivente descreveria uma desativação que não
    existe mais — e a próxima leitura acreditaria nele.
    """
    desativar(cena.usado, 'Engano', operador)
    reativar(cena.usado)

    assert cena.usado.esta_ativo
    assert cena.usado.desativado_motivo is None
    assert cena.usado.desativado_por_id is None
    assert cena.usado.desativado_em is None


# ---------------------------------------------------------------------------
# O BALCÃO — onde a desativação precisa morder
# ---------------------------------------------------------------------------

def test_coletor_desativado_nao_sai_no_balcao(app, cena, operador, admin_client):
    """🔴 Emprestar um coletor desativado desfaria a decisão por acidente.

    E do lugar mais movimentado do sistema: o balcão tem fila na frente e um
    leitor de código de barras. A recusa diz o MOTIVO, porque quem está ali
    precisa saber se é definitivo ou engano.
    """
    from app.models import Colaborador
    db.session.add(Colaborador(re='7001', nome='Quem Retira', empresa_id=cena.mb.id))
    desativar(cena.virgem, 'Devolvido ao fornecedor', operador)

    resposta = admin_client.post('/operacao/retirar', data={
        're_colaborador': '7001', 'busca_modo': 'patrimonio',
        'busca_valor': cena.virgem.numero_patrimonio,
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
    }, follow_redirects=True)

    html = resposta.get_data(as_text=True)
    assert 'desativado' in html.lower()
    assert 'Devolvido ao fornecedor' in html, 'a recusa não disse o motivo'
    assert db.session.get(Coletor, cena.virgem.id).status == 'Disponível', \
        'o coletor desativado foi emprestado'


# ---------------------------------------------------------------------------
# AS TELAS
# ---------------------------------------------------------------------------

def test_inventario_mostra_o_desativado_com_selo(admin_client, cena, operador):
    """Desativado FICA na lista, apagado e com selo.

    Se sumisse, ninguém conseguiria trazê-lo de volta — e o caminho óbvio
    passaria a ser recadastrar o mesmo equipamento (a lição da B3).
    """
    desativar(cena.usado, 'Quebrado sem conserto', operador)

    html = admin_client.get('/coletores').get_data(as_text=True)
    assert cena.usado.numero_patrimonio in html
    assert 'FORA DE OPERAÇÃO' in html
    assert 'Voltar à operação' in html
    assert 'Quebrado sem conserto' in html, 'o motivo não chegou à tela'


def test_rotas_recusam_item_fora_do_escopo(cliente_logado, duas_empresas):
    """Item de outro tenant é inexistente (T3/S2) — nunca 403."""
    du = duas_empresas
    alheio = Coletor(serial_number='SN-ALHEIO', numero_patrimonio='8000',
                     localidade_id=du.locA.id)
    db.session.add(alheio)
    db.session.commit()

    cliente_logado(du.userB).post(f'/coletores/{alheio.id}/desativar',
                                  data={'motivo': 'invadindo'}, follow_redirects=True)

    assert db.session.get(Coletor, alheio.id).esta_ativo, \
        'coletor de outra empresa foi desativado'


# ---------------------------------------------------------------------------
# As travas operacionais
# ---------------------------------------------------------------------------

def test_coletor_em_uso_nao_se_desativa(admin_client, cena, operador):
    """Com alguém segurando, desativar deixaria a movimentação aberta órfã."""
    cena.virgem.status = 'Em Uso'
    db.session.commit()

    admin_client.post(f'/coletores/{cena.virgem.id}/desativar',
                      data={'motivo': 'tentando'}, follow_redirects=True)

    assert db.session.get(Coletor, cena.virgem.id).esta_ativo


def test_peca_pareada_nao_impede_exclusao_e_fica_livre(app, cena, operador):
    """🔴 Pareamento é vínculo ATUAL, não histórico.

    Tratá-lo como histórico impediria de apagar um cadastro errado só porque
    alguém encostou um headset nele. A peça sobrevive ao coletor, solta.
    """
    cat = CategoriaComplemento(empresa_id=cena.mb.id, nome='HEADSET',
                               controle=COMPLEMENTO_UNIDADE)
    db.session.add(cat)
    db.session.flush()
    peca = ItemComplementar(empresa_id=cena.mb.id, categoria_id=cat.id,
                            identificador='HS-B6', coletor_id=cena.virgem.id,
                            localidade_id=cena.loc.id)
    db.session.add(peca)
    db.session.commit()
    peca_id = peca.id

    assert pode_excluir(cena.virgem), 'o pareamento virou impedimento'
    excluir(cena.virgem, operador, SENHA, cena.mb.id)

    sobrevivente = db.session.get(ItemComplementar, peca_id)
    assert sobrevivente is not None, 'a peça foi apagada junto com o coletor'
    assert sobrevivente.coletor_id is None


def test_inventario_oferece_excluir_so_no_que_nunca_foi_usado(admin_client, cena):
    """O botão de excluir aparece no coletor virgem e some no que tem histórico."""
    html = admin_client.get('/coletores').get_data(as_text=True)

    assert f'abrirExcluir({cena.virgem.id}' in html, 'virgem devia oferecer exclusão'
    assert f'abrirExcluir({cena.usado.id}' not in html,         'coletor com movimentação não pode oferecer exclusão'


def test_listagem_nao_consulta_historico_por_linha(app, cena):
    """🔴 Anti-N+1: `historico_de` por linha seriam 3 consultas por coletor.

    O inventário tem centenas de linhas, e esta tela já pagou uma reescrita por
    N+1 escondido atrás de property (H4). `ids_com_historico` resolve a lista
    inteira em uma consulta por RELAÇÃO.
    """
    from sqlalchemy import event
    from app.desativacao import ids_com_historico

    ids = [c.id for c in Coletor.query.all()]
    consultas = []
    engine = db.session.get_bind()

    def ouvir(*args, **kwargs):
        consultas.append(1)

    event.listen(engine, 'before_cursor_execute', ouvir)
    try:
        usados = ids_com_historico(Coletor, ids)
    finally:
        event.remove(engine, 'before_cursor_execute', ouvir)

    assert cena.usado.id in usados and cena.virgem.id not in usados
    # 3 relações de histórico do coletor — e não 3 × número de coletores.
    assert len(consultas) <= 3, f'{len(consultas)} consultas para {len(ids)} coletores'
