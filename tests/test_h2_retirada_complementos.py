"""
Testes da H2 — complementos na RETIRADA (tela de balcão).

Esta é a fatia que toca um fluxo em produção, operado com leitor de código de
barras e fila na frente. Por isso o primeiro teste do arquivo é o de regressão:
a retirada SEM complemento nenhum tem de continuar funcionando exatamente como
antes. Tudo o mais é acréscimo.

Os dois testes que dão nome à fatia:
  `test_bateria_seca_e_bloqueada_em_coletor_de_congelado` — o portão do D6;
  `test_dispensa_registrada_libera_a_excecao`             — a válvula que impede
  o bloqueio de virar contorno por fora do sistema.
"""
import pytest

from app import db
from app.complementos import (BATERIAS_PADRAO, ComplementoRecusado,
                              disponivel_por_categoria, em_campo_por_categoria,
                              baterias_compativeis, sugerir_para_coletor)
from app.models import (CategoriaComplemento, EstoqueComplemento, ItemComplementar,
                        Movimentacao, MovimentacaoComplemento, Coletor, Colaborador,
                        Empresa, COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE,
                        COMPLEMENTO_CAMARA_SECO, COMPLEMENTO_CAMARA_CLIMATIZADO)


@pytest.fixture()
def cenario(app, loc):
    """Balcão completo: catálogo, estoque, dois coletores e um colaborador."""
    from types import SimpleNamespace
    mb = Empresa.query.filter_by(nome='Martin Brower').first()

    clim = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA CLIMATIZADO',
                                controle=COMPLEMENTO_QUANTIDADE,
                                camara_atendida=COMPLEMENTO_CAMARA_CLIMATIZADO, e_bateria=True)
    seca = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                                controle=COMPLEMENTO_QUANTIDADE,
                                camara_atendida=COMPLEMENTO_CAMARA_SECO, e_bateria=True)
    headset = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                   controle=COMPLEMENTO_UNIDADE)
    db.session.add_all([clim, seca, headset])
    db.session.flush()

    db.session.add_all([
        EstoqueComplemento(empresa_id=mb.id, categoria_id=clim.id,
                           localidade_id=loc.id, qtd_cadastrada=10),
        EstoqueComplemento(empresa_id=mb.id, categoria_id=seca.id,
                           localidade_id=loc.id, qtd_cadastrada=10),
    ])

    frio = Coletor(serial_number='SN-FRIO', numero_patrimonio='PAT-FRIO',
                   localidade_id=loc.id, status='Disponível', camara='CONGELADO')
    seco = Coletor(serial_number='SN-SECO', numero_patrimonio='PAT-SECO',
                   localidade_id=loc.id, status='Disponível', camara='SECO')
    db.session.add_all([frio, seco])
    db.session.flush()

    hs = ItemComplementar(empresa_id=mb.id, categoria_id=headset.id,
                          identificador='HS-047', coletor_id=frio.id,
                          localidade_id=loc.id)
    db.session.add(hs)
    db.session.add_all([
        Colaborador(re='8001', nome='Operador Frio', empresa_id=mb.id),
        Colaborador(re='8002', nome='Operador Seco', empresa_id=mb.id),
    ])

    # Analista de balcão REAL: usuário da empresa, não Owner. GLOBAL porque as
    # rotas /api/ são ignoradas pelo `_sync_permissoes` e a fixture
    # `cliente_logado` não injeta `localidade_id` na sessão — um nível CD ficaria
    # sem escopo ali. O admin de bootstrap é
    # Owner sem localidade e não passa no escopo das APIs de coletor — que é o
    # comportamento existente (a rota irmã /api/coletor/status/ faz igual), não
    # uma regra nova desta fatia.
    from werkzeug.security import generate_password_hash
    from app.models import Usuario, Grupo
    ti = Grupo.query.filter_by(nome='TI').first()
    analista = Usuario(nome='Analista Balcao', re='9100', email='balcao@mb.test',
                       senha_hash=generate_password_hash('x', method='scrypt'),
                       grupo_id=ti.id, empresa_id=mb.id, nivel_acesso='GLOBAL',
                       localidade_id=loc.id)
    db.session.add(analista)
    db.session.commit()

    return SimpleNamespace(mb=mb, loc=loc, clim=clim, seca=seca, headset=headset,
                           frio=frio, seco=seco, hs=hs, analista=analista)


def _retirar(client, coletor, extra=None, re='8001'):
    dados = {
        'busca_valor': coletor.numero_patrimonio, 'busca_modo': 'patrimonio',
        're_colaborador': re,
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
    }
    dados.update(extra or {})
    return client.post('/operacao/retirar', data=dados, follow_redirects=True)


# ---------------------------------------------------------------------------
# Regressão — o balcão de hoje não pode piorar
# ---------------------------------------------------------------------------

def test_retirada_sem_complemento_continua_funcionando(admin_client, cenario):
    """🔴 A tela é operada com fila na frente: quem não usa complemento não paga.

    Se a H2 tornar a retirada dependente de escolher complemento, o fluxo mais
    comum do balcão fica mais lento — e o operador começa a clicar em qualquer
    coisa para se livrar do formulário.
    """
    _retirar(admin_client, cenario.seco)

    mov = Movimentacao.query.filter_by(coletor_id=cenario.seco.id).first()
    assert mov is not None
    assert mov.complementos == []
    assert db.session.get(Coletor, cenario.seco.id).status == 'Em Uso'


# ---------------------------------------------------------------------------
# D6 — o portão de compatibilidade, com dispensa auditada
# ---------------------------------------------------------------------------

def test_bateria_seca_e_bloqueada_em_coletor_de_congelado(admin_client, cenario):
    """🔴 Bateria seca no congelado morre no meio do turno.

    E o bloqueio precisa ser DE VERDADE: nada de gravar a movimentação e só
    avisar. Se a retirada passasse, o coletor sairia com a bateria errada e o
    aviso viraria ruído na tela.
    """
    _retirar(admin_client, cenario.frio, {
        'complemento_categoria': str(cenario.seca.id),
        f'complemento_qtd_{cenario.seca.id}': '1',
    })

    assert Movimentacao.query.filter_by(coletor_id=cenario.frio.id).first() is None, \
        'a retirada inteira deve ser desfeita, não só o complemento'
    assert db.session.get(Coletor, cenario.frio.id).status == 'Disponível', \
        'o coletor não pode ficar Em Uso depois de um bloqueio'


def test_dispensa_registrada_libera_a_excecao(admin_client, cenario):
    """A válvula do D6 — sem ela, o bloqueio vira contorno por fora do sistema.

    Às 3h da manhã, sem bateria climatizada em estoque, o operador precisa
    entregar. Com a dispensa ele entrega E fica o registro de quem liberou.
    """
    _retirar(admin_client, cenario.frio, {
        'complemento_categoria': str(cenario.seca.id),
        f'complemento_qtd_{cenario.seca.id}': '1',
        f'complemento_dispensa_{cenario.seca.id}': 'sem estoque de climatizada',
    })

    mov = Movimentacao.query.filter_by(coletor_id=cenario.frio.id).first()
    assert mov is not None
    linha = mov.complementos[0]
    assert linha.dispensa_motivo == 'sem estoque de climatizada'
    assert linha.dispensa_por, 'a dispensa precisa registrar QUEM liberou'


def test_climatizada_passa_em_qualquer_coletor(admin_client, cenario):
    """A bateria boa serve em todo lugar — recusá-la seria inventar restrição."""
    for coletor, re in ((cenario.frio, '8001'), (cenario.seco, '8002')):
        _retirar(admin_client, coletor, {
            'complemento_categoria': str(cenario.clim.id),
            f'complemento_qtd_{cenario.clim.id}': '1',
        }, re=re)
        mov = Movimentacao.query.filter_by(coletor_id=coletor.id).first()
        assert mov is not None and len(mov.complementos) == 1


# ---------------------------------------------------------------------------
# D8 — uma bateria é o padrão; mais é exceção
# ---------------------------------------------------------------------------

def test_padrao_e_uma_bateria(admin_client, cenario):
    """Sem quantidade informada, sai UMA. O número não é um default de formulário:
    é a operação normal, e a tela confirma em vez de perguntar."""
    assert BATERIAS_PADRAO == 1
    _retirar(admin_client, cenario.seco, {
        'complemento_categoria': str(cenario.seca.id),
    })
    mov = Movimentacao.query.filter_by(coletor_id=cenario.seco.id).first()
    assert mov.complementos[0].qtd_saida == 1


def test_mais_de_uma_bateria_e_possivel(admin_client, cenario):
    """A exceção existe — só não é o caminho fácil."""
    _retirar(admin_client, cenario.seco, {
        'complemento_categoria': str(cenario.seca.id),
        f'complemento_qtd_{cenario.seca.id}': '3',
    })
    mov = Movimentacao.query.filter_by(coletor_id=cenario.seco.id).first()
    assert mov.complementos[0].qtd_saida == 3


def test_quantidade_zero_nao_vira_linha(admin_client, cenario):
    """Zero significa 'não levou' — gravar linha zerada poluiria a cobrança."""
    _retirar(admin_client, cenario.seco, {
        'complemento_categoria': str(cenario.seca.id),
        f'complemento_qtd_{cenario.seca.id}': '0',
    })
    mov = Movimentacao.query.filter_by(coletor_id=cenario.seco.id).first()
    assert mov.complementos == []


# ---------------------------------------------------------------------------
# D2 — o headset pareado
# ---------------------------------------------------------------------------

def test_headset_pareado_e_sugerido_para_o_coletor(app, cenario):
    """O sistema sabe qual headset acompanha ESTE coletor — o operador confirma."""
    sugestao = sugerir_para_coletor(cenario.mb.id, cenario.frio)

    assert [p['identificador'] for p in sugestao['pareados']] == ['HS-047']
    assert sugestao['camara'] == 'CONGELADO'
    # Coletor de congelado só aceita a climatizada.
    assert [b['nome'] for b in sugestao['baterias']] == ['BATERIA CLIMATIZADO']


def test_coletor_do_seco_aceita_as_duas_baterias_mas_sugere_a_do_seco(app, cenario):
    """No seco as duas servem — e a específica vem primeiro.

    A climatizada é mais cara e deve sobrar para quem precisa dela; oferecê-la
    como primeira opção no seco gastaria o estoque bom à toa.
    """
    nomes = [c.nome for c in baterias_compativeis(cenario.mb.id, cenario.seco)]
    assert nomes == ['BATERIA SECO', 'BATERIA CLIMATIZADO']


def test_peca_identificada_sai_com_item_id(admin_client, cenario):
    _retirar(admin_client, cenario.frio, {
        'complemento_categoria': str(cenario.headset.id),
        f'complemento_item_{cenario.headset.id}': str(cenario.hs.id),
    })
    mov = Movimentacao.query.filter_by(coletor_id=cenario.frio.id).first()
    linha = mov.complementos[0]
    assert linha.item_id == cenario.hs.id and linha.qtd_saida == 1


def test_peca_de_outra_categoria_e_recusada(admin_client, cenario):
    """id que chega do formulário nunca é confiável (família B do S2)."""
    _retirar(admin_client, cenario.frio, {
        'complemento_categoria': str(cenario.headset.id),
        f'complemento_item_{cenario.headset.id}': '9999',
    })
    assert Movimentacao.query.filter_by(coletor_id=cenario.frio.id).first() is None


# ---------------------------------------------------------------------------
# D7 — o disponível é derivado
# ---------------------------------------------------------------------------

def test_disponivel_cai_quando_a_bateria_esta_em_campo(admin_client, cenario):
    """O saldo é total menos o que está fora — calculado, nunca armazenado."""
    assert disponivel_por_categoria(cenario.seca.id, cenario.loc.id) == 10

    _retirar(admin_client, cenario.seco, {
        'complemento_categoria': str(cenario.seca.id),
        f'complemento_qtd_{cenario.seca.id}': '2',
    })

    assert em_campo_por_categoria(cenario.seca.id, cenario.loc.id) == 2
    assert disponivel_por_categoria(cenario.seca.id, cenario.loc.id) == 8


def test_sem_estoque_cadastrado_o_disponivel_e_desconhecido(app, cenario):
    """CD que nunca cadastrou não pode ser impedido de operar.

    None ≠ zero: 'não controlado' é diferente de 'acabou'.
    """
    nova = CategoriaComplemento(empresa_id=cenario.mb.id, nome='SUPORTE',
                                controle=COMPLEMENTO_QUANTIDADE)
    db.session.add(nova)
    db.session.commit()
    assert disponivel_por_categoria(nova.id, cenario.loc.id) is None


def test_saldo_insuficiente_nao_bloqueia_mas_fica_registrado(admin_client, cenario):
    """O estoque é um número digitado por alguém — travar o balcão por causa dele
    puniria o operador por um cadastro desatualizado. Mas a divergência fica
    marcada, para a conferência do estoque aparecer depois."""
    _retirar(admin_client, cenario.seco, {
        'complemento_categoria': str(cenario.seca.id),
        f'complemento_qtd_{cenario.seca.id}': '99',
    })
    mov = Movimentacao.query.filter_by(coletor_id=cenario.seco.id).first()
    assert mov is not None, 'a retirada não pode travar por saldo'
    assert 'saldo insuficiente' in (mov.complementos[0].dispensa_motivo or '')


# ---------------------------------------------------------------------------
# A API que alimenta o card
# ---------------------------------------------------------------------------

def test_api_sugere_complementos_do_coletor(cliente_logado, cenario):
    balcao = cliente_logado(cenario.analista)
    resposta = balcao.get(f'/api/coletor/{cenario.frio.id}/complementos')
    dados = resposta.get_json()

    assert resposta.status_code == 200 and dados['encontrado'] is True
    assert dados['baterias_padrao'] == 1
    assert dados['camara'] == 'CONGELADO'
    assert len(dados['baterias']) == 1
    assert dados['baterias'][0]['disponivel'] == 10
    assert dados['pareados'][0]['identificador'] == 'HS-047'


def test_api_respeita_o_escopo(app, cliente_logado, duas_empresas, cenario):
    """Coletor fora do escopo é inexistente — S3/família C."""
    cliente_b = cliente_logado(duas_empresas.userB)
    resposta = cliente_b.get(f'/api/coletor/{cenario.frio.id}/complementos')
    assert resposta.status_code == 404


# ---------------------------------------------------------------------------
# A tela de balcão
# ---------------------------------------------------------------------------

def test_tela_carrega_com_o_popup_de_complementos(cliente_logado, cenario):
    """A tela renderiza e traz o modal novo, sem quebrar o que já existia."""
    balcao = cliente_logado(cenario.analista)
    html = balcao.get('/operacao').get_data(as_text=True)

    assert 'modalComplementos' in html
    assert 'modalChecklist' in html, 'o checklist de entrega não pode ter sumido'
    assert 'carregarComplementos' in html
    # i18n: nenhuma chave crua vazando na tela de balcão.
    assert 'compmov.' not in html


def test_fluxo_da_tela_encadeia_checklist_e_complementos(cliente_logado, cenario):
    """🔴 A ordem do balcão: checklist → complementos → submit.

    `confirmarChecklist` não pode submeter direto quando há complemento a
    confirmar — senão a bateria sairia sem ninguém ver, e o popup do D8 viraria
    enfeite. E quem NÃO tem complemento tem de seguir reto, sem popup.
    """
    balcao = cliente_logado(cenario.analista)
    html = balcao.get('/operacao').get_data(as_text=True)

    assert 'if (temComplementos()) {' in html
    assert 'abrirComplementos();' in html


def test_popup_confirma_em_vez_de_perguntar(cliente_logado, cenario):
    """D8 — o número já vem decidido; a exceção é um link, não um campo à vista.

    Um <select> de quantidade no fluxo principal convidaria a mexer, que é o
    oposto do pedido: uma bateria é o normal, mais é exceção.
    """
    balcao = cliente_logado(cenario.analista)
    html = balcao.get('/operacao').get_data(as_text=True)

    assert 'compmov.saindo_com' not in html          # a chave foi resolvida
    assert 'revelarMaisBaterias' in html             # a exceção existe...
    assert 'comp-stepper" class="d-none' in html     # ...mas nasce escondida


def test_bateria_sem_restricao_de_camara_aparece_na_sugestao(app, cenario):
    """🔴 O bug que fez o popup nunca abrir em homolog.

    A restrição térmica é OPCIONAL. Quem cadastra "BATERIA" sem escolher câmara
    tem uma bateria igual — e o filtro exigia `camara_atendida` preenchido, o que
    deixava a sugestão vazia e a tela muda. Quem não preencheu o campo opcional
    ficava sem a funcionalidade inteira, sem nenhuma pista do porquê.
    """
    from app.complementos import baterias_compativeis

    generica = CategoriaComplemento(empresa_id=cenario.mb.id, nome='BATERIA GENERICA',
                                    controle=COMPLEMENTO_QUANTIDADE,
                                    camara_atendida=None, e_bateria=True)
    db.session.add(generica)
    db.session.commit()

    for coletor in (cenario.frio, cenario.seco):
        nomes = [c.nome for c in baterias_compativeis(cenario.mb.id, coletor)]
        assert 'BATERIA GENERICA' in nomes, \
            'bateria sem restrição serve em qualquer câmara e precisa ser sugerida'


def test_ordem_da_sugestao_poupa_a_bateria_cara(app, cenario):
    """A primeira da lista é a pré-selecionada — e isso é decisão de operação.

    Gastar a climatizada (mais cara) num coletor do seco desperdiça o estoque
    bom; a coringa sem restrição fica por último, depois das específicas.
    """
    from app.complementos import baterias_compativeis

    db.session.add(CategoriaComplemento(empresa_id=cenario.mb.id, nome='BATERIA GENERICA',
                                        controle=COMPLEMENTO_QUANTIDADE, e_bateria=True))
    db.session.commit()

    no_seco = [c.nome for c in baterias_compativeis(cenario.mb.id, cenario.seco)]
    assert no_seco[0] == 'BATERIA SECO'
    assert no_seco[-1] == 'BATERIA GENERICA'

    no_frio = [c.nome for c in baterias_compativeis(cenario.mb.id, cenario.frio)]
    assert no_frio[0] == 'BATERIA CLIMATIZADO', 'no congelado, a climatizada vem primeiro'
    assert 'BATERIA SECO' not in no_frio, 'a seca continua barrada no congelado'
