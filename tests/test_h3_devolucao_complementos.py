"""
Testes da H3 — conferência na DEVOLUÇÃO e pendência no histórico.

É aqui que o controle fecha o ciclo: a H2 registra o que sai, a H3 cobra o que
não volta. O teste que dá nome à fatia é
`test_devolucao_nunca_e_bloqueada_por_falta`: o coletor volta de qualquer jeito e
a dívida fica registrada — travar a devolução porque o headset sumiu deixaria o
equipamento preso em campo, punindo o inventário para cobrar um acessório.
"""
import pytest

from app import db
from app.complementos import (conferir_devolucao, em_campo_da_movimentacao,
                              ler_conferencias_do_form, pendencias_do_coletor)
from app.models import (CategoriaComplemento, EstoqueComplemento, ItemComplementar,
                        Movimentacao, MovimentacaoComplemento, Coletor, Colaborador,
                        Empresa, Usuario, Grupo, COMPLEMENTO_QUANTIDADE,
                        COMPLEMENTO_UNIDADE, COMPLEMENTO_CAMARA_SECO)


@pytest.fixture()
def emprestimo(app, loc):
    """Um coletor JÁ em campo, com 2 baterias e 1 headset pendurados."""
    from types import SimpleNamespace
    from werkzeug.security import generate_password_hash

    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    bat = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                               controle=COMPLEMENTO_QUANTIDADE,
                               camara_atendida=COMPLEMENTO_CAMARA_SECO, e_bateria=True)
    hs_cat = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                  controle=COMPLEMENTO_UNIDADE)
    db.session.add_all([bat, hs_cat])
    db.session.flush()
    db.session.add(EstoqueComplemento(empresa_id=mb.id, categoria_id=bat.id,
                                      localidade_id=loc.id, qtd_cadastrada=10))

    coletor = Coletor(serial_number='SN-DEV', numero_patrimonio='PAT-DEV',
                      localidade_id=loc.id, status='Em Uso', camara='SECO',
                      re_colaborador='8100')
    db.session.add(coletor)
    db.session.flush()

    hs = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                          identificador='HS-900', coletor_id=coletor.id,
                          localidade_id=loc.id)
    colab = Colaborador(re='8100', nome='Joana Campo', empresa_id=mb.id)
    db.session.add_all([hs, colab])
    db.session.flush()

    mov = Movimentacao(coletor_id=coletor.id, re_colaborador='8100',
                       colaborador_id=colab.id)
    db.session.add(mov)
    db.session.flush()
    linha_bat = MovimentacaoComplemento(movimentacao_id=mov.id, categoria_id=bat.id,
                                        qtd_saida=2, qtd_devolvida=0)
    linha_hs = MovimentacaoComplemento(movimentacao_id=mov.id, categoria_id=hs_cat.id,
                                       item_id=hs.id, qtd_saida=1, qtd_devolvida=0)
    db.session.add_all([linha_bat, linha_hs])

    ti = Grupo.query.filter_by(nome='TI').first()
    analista = Usuario(nome='Analista Balcao', re='9200', email='balcao3@mb.test',
                       senha_hash=generate_password_hash('x', method='scrypt'),
                       grupo_id=ti.id, empresa_id=mb.id, nivel_acesso='GLOBAL',
                       localidade_id=loc.id)
    db.session.add(analista)
    db.session.commit()

    return SimpleNamespace(mb=mb, loc=loc, coletor=coletor, mov=mov, bat=bat,
                           hs_cat=hs_cat, hs=hs, linha_bat=linha_bat,
                           linha_hs=linha_hs, analista=analista, colab=colab)


# PNG de 1x1 — os uploads passam por checagem de magic bytes, então bytes
# aleatórios seriam recusados antes de chegar à regra que o teste quer exercitar.
_PNG_MINIMO = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c6300010000050001'
    '0d0a2db40000000049454e44ae426082')


def _devolver(client, coletor, extra=None):
    dados = {'busca_valor': coletor.numero_patrimonio, 'busca_modo': 'patrimonio',
             'status_identificacao': 'OK'}
    dados.update(extra or {})
    return client.post('/operacao/devolver', data=dados, follow_redirects=True)


# ---------------------------------------------------------------------------
# O ciclo fechado
# ---------------------------------------------------------------------------

def test_devolucao_completa_zera_a_pendencia(admin_client, emprestimo):
    """Tudo voltou: nada fica devendo, e o disponível volta ao total."""
    from app.complementos import disponivel_por_categoria

    _devolver(admin_client, emprestimo.coletor, {
        f'conferencia_{emprestimo.linha_bat.id}': '2',
        f'conferencia_{emprestimo.linha_hs.id}': '1',
    })

    mov = db.session.get(Movimentacao, emprestimo.mov.id)
    assert mov.data_retorno is not None
    assert all(not l.pendente for l in mov.complementos)
    assert disponivel_por_categoria(emprestimo.bat.id, emprestimo.loc.id) == 10


def test_devolucao_parcial_deixa_a_falta_explicita(admin_client, emprestimo):
    """🔴 O caso que motivou a fatia: levou 2 baterias, devolveu 1."""
    _devolver(admin_client, emprestimo.coletor, {
        f'conferencia_{emprestimo.linha_bat.id}': '1',
        f'conferencia_{emprestimo.linha_hs.id}': '1',
    })

    linha = db.session.get(MovimentacaoComplemento, emprestimo.linha_bat.id)
    assert linha.qtd_devolvida == 1
    assert linha.faltando == 1 and linha.pendente is True
    assert linha.devolvido_em is not None, 'a devolução parcial tem data'


def test_devolucao_nunca_e_bloqueada_por_falta(admin_client, emprestimo):
    """🔴 O coletor volta de qualquer jeito.

    Travar a devolução porque o headset sumiu deixaria o equipamento preso em
    campo — punindo o inventário para cobrar um acessório. A dívida fica
    registrada; o coletor volta para a operação.
    """
    _devolver(admin_client, emprestimo.coletor)      # não conferiu NADA

    coletor = db.session.get(Coletor, emprestimo.coletor.id)
    mov = db.session.get(Movimentacao, emprestimo.mov.id)
    assert coletor.status == 'Disponível', 'o coletor tem de voltar para a operação'
    assert mov.data_retorno is not None
    assert sum(l.faltando for l in mov.complementos) == 3, '2 baterias + 1 headset'


def test_nao_conferido_conta_como_nao_devolvido(app, emprestimo):
    """Ausência de conferência é falta, não 'assumo que voltou'.

    O contrário transformaria o controle em teatro: bastaria fechar o modal para
    quitar a dívida de todo mundo.
    """
    faltantes = conferir_devolucao(emprestimo.mov, {})
    assert len(faltantes) == 2
    assert all(l.qtd_devolvida == 0 for l in emprestimo.mov.complementos)


def test_conferencia_nao_aceita_mais_do_que_saiu(app, emprestimo):
    """Devolver 5 de 2 é erro de digitação — o excesso é aparado, não somado."""
    conferir_devolucao(emprestimo.mov, {emprestimo.linha_bat.id: 5})
    assert db.session.get(MovimentacaoComplemento,
                          emprestimo.linha_bat.id).qtd_devolvida == 2


def test_conferencia_negativa_vira_zero(app, emprestimo):
    conferir_devolucao(emprestimo.mov, {emprestimo.linha_bat.id: -3})
    assert db.session.get(MovimentacaoComplemento,
                          emprestimo.linha_bat.id).qtd_devolvida == 0


def test_pendencia_sobrevive_a_devolucao_com_defeito(admin_client, emprestimo):
    """A dívida não depende do estado em que o coletor chegou.

    Coletor que volta quebrado vai para Manutenção — e o headset que não voltou
    continua sendo dívida do colaborador. Por isso o aviso fica fora do
    if/elif/else dos desfechos.
    """
    # Foto é obrigatória ao reportar defeito (regra da Fase 4) — sem ela a
    # devolução é recusada, e o teste estaria medindo outra coisa.
    import io as _io
    _devolver(admin_client, emprestimo.coletor, {
        'voltou_danificado': 'on', 'categoria_defeito': 'FISICO',
        'detalhe_defeito': 'TELA_QUEBRADA',
        f'conferencia_{emprestimo.linha_bat.id}': '2',
        'foto_devolucao': (_io.BytesIO(_PNG_MINIMO), 'defeito.png'),
    })

    coletor = db.session.get(Coletor, emprestimo.coletor.id)
    linha = db.session.get(MovimentacaoComplemento, emprestimo.linha_hs.id)
    assert coletor.status == 'Manutenção'
    assert linha.pendente is True, 'o headset continua devendo mesmo com o coletor quebrado'


# ---------------------------------------------------------------------------
# Leitura: o que o operador vê
# ---------------------------------------------------------------------------

def test_em_campo_lista_o_que_falta_voltar(app, emprestimo):
    pendentes = em_campo_da_movimentacao(emprestimo.mov)
    por_categoria = {p['categoria']: p for p in pendentes}

    assert por_categoria['BATERIA SECO']['faltando'] == 2
    assert por_categoria['HEADSET']['identificador'] == 'HS-900'


def test_api_em_campo_alimenta_a_conferencia(cliente_logado, emprestimo):
    balcao = cliente_logado(emprestimo.analista)
    dados = balcao.get(f'/api/coletor/{emprestimo.coletor.id}/em-campo').get_json()

    assert dados['encontrado'] is True
    assert len(dados['pendentes']) == 2
    assert {p['categoria'] for p in dados['pendentes']} == {'BATERIA SECO', 'HEADSET'}


def test_api_em_campo_respeita_escopo(cliente_logado, duas_empresas, emprestimo):
    cliente_b = cliente_logado(duas_empresas.userB)
    assert cliente_b.get(
        f'/api/coletor/{emprestimo.coletor.id}/em-campo').status_code == 404


def test_ler_conferencias_ignora_lixo():
    """Campo por id, não por posição — e nada que não seja número entra."""
    from werkzeug.datastructures import MultiDict
    form = MultiDict([('conferencia_12', '2'), ('conferencia_abc', '1'),
                      ('conferencia_9', 'x'), ('outro_campo', '5')])
    assert ler_conferencias_do_form(form) == {12: 2}


def test_historico_mostra_a_pendencia(admin_client, emprestimo):
    """🔴 'se estiver faltando, terá isso explícito no histórico' — o pedido literal."""
    _devolver(admin_client, emprestimo.coletor, {
        f'conferencia_{emprestimo.linha_bat.id}': '1',
    })

    html = admin_client.get(
        f'/coletor/{emprestimo.coletor.id}/historico').get_data(as_text=True)
    assert 'HS-900' in html or 'HEADSET' in html, \
        'o que não voltou precisa aparecer no histórico do coletor'


def test_pendencias_do_coletor_agrega_todas_as_movimentacoes(app, emprestimo):
    pendencias = pendencias_do_coletor(emprestimo.coletor.id)
    assert len(pendencias) == 2
    assert sum(l.faltando for l in pendencias) == 3


# ---------------------------------------------------------------------------
# A tela de devolução
# ---------------------------------------------------------------------------

def test_tela_traz_o_modal_de_conferencia(cliente_logado, emprestimo):
    """Simetria com a retirada: lá o checklist, aqui a conferência."""
    balcao = cliente_logado(emprestimo.analista)
    html = balcao.get('/operacao').get_data(as_text=True)

    assert 'modalConferencia' in html
    assert 'interceptarDevolucao' in html
    assert 'carregarEmCampo' in html
    # i18n: nenhuma chave crua na tela de balcão.
    assert 'confdev.' not in html


def test_devolucao_sem_pendencia_nao_abre_modal(cliente_logado, emprestimo):
    """🔴 Quem não tem o que conferir devolve como sempre.

    Sem esta saída, todo coletor sem complemento passaria por um modal vazio — e
    o balcão ficaria mais lento justamente no caso mais comum.
    """
    balcao = cliente_logado(emprestimo.analista)
    html = balcao.get('/operacao').get_data(as_text=True)

    assert 'if (!_emCampo.length || _conferenciaOk) {' in html
    assert 'ativarLoading();' in html


def test_modal_avisa_a_falta_mas_nao_impede(cliente_logado, emprestimo):
    """O aviso existe; o bloqueio não. A devolução nunca trava por falta."""
    balcao = cliente_logado(emprestimo.analista)
    html = balcao.get('/operacao').get_data(as_text=True)

    assert 'atualizarAvisoConferencia' in html
    # o botão de confirmar não é desabilitado por falta (ao contrário do
    # checklist da retirada, que exige os 5 itens)
    assert 'onclick="confirmarConferencia()"' in html
    assert 'id="btn-confirmar-conferencia" disabled' not in html
