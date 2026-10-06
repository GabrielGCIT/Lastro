"""
Testes da H5 — prazo de cobrança e rastreio em tempo real.

Duas coisas que o Gabriel pediu e que são a mesma moeda: o sistema tem de saber
ONDE está cada peça a qualquer momento, e tem de COBRAR quando o tempo passa.

O teste que dá nome à fatia é `test_saldo_conta_a_peca_que_nao_voltou`: até a H4
a bateria perdida sumia da conta. A movimentação fechava, a peça continuava na
rua, e o saldo disponível voltava a contá-la como se estivesse na prateleira — o
CD acreditava ter mais bateria do que tem. Nenhum teste cobria isso.
"""
from datetime import datetime, timedelta

import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import (SITUACAO_ATRASADO, SITUACAO_FALTOU, SITUACAO_NO_PRAZO,
                              PRAZO_PADRAO_HORAS, disponivel_por_categoria,
                              em_campo_por_categoria, horas_fora, pendencias_abertas,
                              prazo_horas, rastreio_itens, rastreio_quantidades,
                              situacao_da_pendencia, sugerir_para_coletor, tempo_fora)
from app.models import (CategoriaComplemento, Coletor, Colaborador, Empresa,
                        EmpresaPolitica, EstoqueComplemento, ItemComplementar,
                        Movimentacao, MovimentacaoComplemento,
                        COMPLEMENTO_QUANTIDADE, COMPLEMENTO_UNIDADE)

# 🔴 Base de tempo RELATIVA, nunca uma data fixa. A primeira versão fixava
# 28/08/2026 12:00: os testes de DOMÍNIO recebem `agora=AGORA` e passavam, mas os
# testes de TELA batem numa rota que usa o relógio de verdade — e dois dias
# depois o mesmo empréstimo "de 1 hora atrás" já estava 49 horas no passado,
# virando ATRASADO. Passaram no dia em que foram escritos e quebraram sozinhos
# no dia 30. Numa suíte que é portão de deploy, isso pararia o deploy sem que
# nada tivesse mudado no produto.
AGORA = datetime.now()


@pytest.fixture()
def cenario(app, loc):
    """Um CD com 10 baterias cadastradas, 1 headset, e nada em campo ainda."""
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    bat = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    hs_cat = CategoriaComplemento(empresa_id=mb.id, nome='HEADSET',
                                  controle=COMPLEMENTO_UNIDADE)
    db.session.add_all([bat, hs_cat])
    db.session.flush()
    db.session.add(EstoqueComplemento(empresa_id=mb.id, categoria_id=bat.id,
                                      localidade_id=loc.id, qtd_cadastrada=10))

    coletor = Coletor(serial_number='SN-H5', numero_patrimonio='PAT-H5',
                      localidade_id=loc.id, status='Em Uso', camara='SECO')
    colab = Colaborador(re='5500', nome='Ana Portadora', empresa_id=mb.id)
    db.session.add_all([coletor, colab])
    db.session.flush()

    hs = ItemComplementar(empresa_id=mb.id, categoria_id=hs_cat.id,
                          identificador='HS-H5', coletor_id=coletor.id,
                          localidade_id=loc.id)
    db.session.add(hs)
    db.session.commit()
    return SimpleNamespace(mb=mb, loc=loc, bat=bat, hs_cat=hs_cat, hs=hs,
                           coletor=coletor, colab=colab)


def _emprestar(cenario, qtd_bat=0, com_headset=False, horas_atras=1,
               devolvido=None, qtd_devolvida=0):
    """Cria uma saída no passado. `devolvido` fecha a movimentação."""
    mov = Movimentacao(coletor_id=cenario.coletor.id, re_colaborador=cenario.colab.re,
                       colaborador_id=cenario.colab.id,
                       data_saida=AGORA - timedelta(hours=horas_atras),
                       data_retorno=devolvido)
    db.session.add(mov)
    db.session.flush()
    if qtd_bat:
        db.session.add(MovimentacaoComplemento(
            movimentacao_id=mov.id, categoria_id=cenario.bat.id,
            qtd_saida=qtd_bat, qtd_devolvida=qtd_devolvida))
    if com_headset:
        db.session.add(MovimentacaoComplemento(
            movimentacao_id=mov.id, categoria_id=cenario.hs_cat.id,
            item_id=cenario.hs.id, qtd_saida=1, qtd_devolvida=0))
    db.session.commit()
    return mov


# ---------------------------------------------------------------------------
# o bug do saldo — a razão de ser desta fatia
# ---------------------------------------------------------------------------

def test_saldo_conta_a_peca_que_nao_voltou(app, cenario):
    """Peça perdida continua FORA, mesmo com a movimentação fechada.

    🔴 Regressão da H5. Antes, `em_campo_por_categoria` exigia
    `data_retorno IS NULL`: o coletor voltava sem as baterias, a movimentação
    fechava, e as baterias voltavam a ser contadas como disponíveis. O CD tinha 8
    e o sistema dizia 10.
    """
    mov = _emprestar(cenario, qtd_bat=3)
    assert em_campo_por_categoria(cenario.bat.id, cenario.loc.id) == 3
    assert disponivel_por_categoria(cenario.bat.id, cenario.loc.id) == 7

    # o coletor volta, mas só 1 bateria volta com ele
    mov.data_retorno = AGORA
    MovimentacaoComplemento.query.filter_by(movimentacao_id=mov.id).first().qtd_devolvida = 1
    db.session.commit()

    assert em_campo_por_categoria(cenario.bat.id, cenario.loc.id) == 2, \
        'a bateria que não voltou sumiu da conta'
    assert disponivel_por_categoria(cenario.bat.id, cenario.loc.id) == 8, \
        'o CD tem 8 de verdade; dizer 10 é convidar a operação a contar com o que não existe'


def test_peca_perdida_nao_volta_a_ser_oferecida(app, cenario):
    """O headset que não voltou não pode aparecer como disponível na retirada.

    Mesma família do saldo: com a movimentação fechada, `_item_em_campo` dizia
    que a peça estava na gaveta — e a tela de balcão a ofereceria de novo.
    """
    sugestao = sugerir_para_coletor(cenario.mb.id, cenario.coletor)
    pareado = next(p for p in sugestao['pareados'] if p['identificador'] == 'HS-H5')
    assert pareado['em_uso'] is False, 'sem empréstimo, a peça está na gaveta'

    mov = _emprestar(cenario, com_headset=True)
    mov.data_retorno = AGORA                     # coletor volta, headset não
    db.session.commit()

    sugestao = sugerir_para_coletor(cenario.mb.id, cenario.coletor)
    pareado = next(p for p in sugestao['pareados'] if p['identificador'] == 'HS-H5')
    assert pareado['em_uso'] is True, (
        'com a movimentação fechada, a peça perdida voltava a parecer disponível')


# ---------------------------------------------------------------------------
# o relógio
# ---------------------------------------------------------------------------

def test_dentro_do_prazo_nao_e_cobranca(app, cenario):
    """3h fora com prazo de 24h é operação normal, não dívida.

    🔴 A regra que o Gabriel pediu explicitamente: "mesmo que não fique alertando
    de imediato". Se tudo que está em campo virasse alerta, todo coletor em uso
    ficaria vermelho — e alerta que sempre acende ninguém lê.
    """
    _emprestar(cenario, qtd_bat=2, horas_atras=3)

    assert pendencias_abertas(None, agora=AGORA) == []
    tudo = pendencias_abertas(None, incluir_no_prazo=True, agora=AGORA)
    assert len(tudo) == 1
    assert tudo[0].situacao == SITUACAO_NO_PRAZO


def test_passou_do_prazo_vira_cobranca(app, cenario):
    _emprestar(cenario, qtd_bat=2, horas_atras=30)

    cobravel = pendencias_abertas(None, agora=AGORA)
    assert len(cobravel) == 1
    assert cobravel[0].situacao == SITUACAO_ATRASADO
    assert 29 < cobravel[0].horas_fora < 31


def test_voltou_faltando_cobra_na_hora_sem_prazo(app, cenario):
    """Fato consumado não espera relógio: o coletor voltou, a peça não."""
    mov = _emprestar(cenario, qtd_bat=2, horas_atras=1)
    mov.data_retorno = AGORA
    db.session.commit()

    cobravel = pendencias_abertas(None, agora=AGORA)
    assert len(cobravel) == 1
    assert cobravel[0].situacao == SITUACAO_FALTOU


def test_a_fronteira_do_prazo_e_inclusiva(app, cenario):
    """Exatamente no prazo já conta. Um limite que só dispara depois deixaria
    uma janela em que a dívida existe e ninguém vê."""
    linha_no_limite = _emprestar(cenario, qtd_bat=1, horas_atras=24)
    linha = MovimentacaoComplemento.query.filter_by(
        movimentacao_id=linha_no_limite.id).first()
    assert situacao_da_pendencia(linha, prazo=24, agora=AGORA) == SITUACAO_ATRASADO
    assert situacao_da_pendencia(linha, prazo=25, agora=AGORA) == SITUACAO_NO_PRAZO


def test_horas_fora_conta_da_saida_e_nunca_e_negativa(app, cenario):
    mov = _emprestar(cenario, qtd_bat=1, horas_atras=5)
    linha = MovimentacaoComplemento.query.filter_by(movimentacao_id=mov.id).first()
    assert round(horas_fora(linha, AGORA)) == 5
    # relógio do servidor atrás da data de saída não pode gerar tempo negativo
    assert horas_fora(linha, AGORA - timedelta(hours=99)) == 0.0


def test_prazo_e_politica_por_empresa(app, cenario):
    """O default é 24h; a empresa pode ter o seu sem deploy."""
    assert prazo_horas(cenario.mb.id) == PRAZO_PADRAO_HORAS

    db.session.add(EmpresaPolitica(empresa_id=cenario.mb.id,
                                   chave='complemento_prazo_horas', valor='12'))
    db.session.commit()
    assert prazo_horas(cenario.mb.id) == 12


def test_prazo_nao_explode_fora_de_request(app, cenario):
    """Os scripts de validação rodam em app_context puro, sem sessão.

    Um relógio de cobrança não pode derrubar o processo que só queria conferir.
    """
    assert prazo_horas() == PRAZO_PADRAO_HORAS


@pytest.mark.parametrize('horas,esperado', [
    (0.25, '15m'), (3.5, '3h 30m'), (23.99, '23h 59m'), (26, '1d 2h'), (0, '0m'),
])
def test_tempo_fora_legivel_de_relance(horas, esperado):
    """Quem varre a tela compara tempos com o olho. '52.34 horas' obriga a conta."""
    assert tempo_fora(horas) == esperado


# ---------------------------------------------------------------------------
# rastreio — onde está cada peça
# ---------------------------------------------------------------------------

def test_item_parado_aparece_no_cd(app, cenario):
    mapa = rastreio_itens(None, cenario.mb.id, agora=AGORA)
    assert len(mapa) == 1
    linha = mapa[0]
    assert linha['estado'] == 'ESTOQUE'
    assert linha['localidade'].id == cenario.loc.id
    assert linha['colaborador'] is None


def test_item_em_campo_diz_com_quem_e_em_qual_coletor(app, cenario):
    _emprestar(cenario, com_headset=True, horas_atras=2)

    linha = rastreio_itens(None, cenario.mb.id, agora=AGORA)[0]
    assert linha['estado'] == SITUACAO_NO_PRAZO
    assert linha['colaborador'].nome == 'Ana Portadora'
    assert linha['coletor'].id == cenario.coletor.id
    assert round(linha['horas_fora']) == 2
    assert linha['coletor_voltou'] is None


def test_item_que_nao_voltou_continua_com_a_pessoa(app, cenario):
    """O coletor ter sido devolvido não traz o headset de volta."""
    mov = _emprestar(cenario, com_headset=True, horas_atras=2)
    mov.data_retorno = AGORA
    db.session.commit()

    linha = rastreio_itens(None, cenario.mb.id, agora=AGORA)[0]
    assert linha['estado'] == SITUACAO_FALTOU
    assert linha['colaborador'].nome == 'Ana Portadora'
    # Tolerância, não igualdade: há bancos que arredondam o microssegundo que o
    # Python gravou. A comparação exata amarraria o teste ao SQLite.
    assert abs((linha['coletor_voltou'] - AGORA).total_seconds()) < 0.01


def test_quantidades_mostram_saldo_e_portadores(app, cenario):
    _emprestar(cenario, qtd_bat=3, horas_atras=30)

    saldos = rastreio_quantidades(None, cenario.mb.id, agora=AGORA)
    assert len(saldos) == 1
    s = saldos[0]
    assert s['cadastrada'] == 10
    assert s['em_campo'] == 3
    assert s['disponivel'] == 7
    assert s['atrasados'] == 1
    assert len(s['portadores']) == 1
    assert s['portadores'][0].movimentacao.colaborador.nome == 'Ana Portadora'


def test_sem_estoque_cadastrado_disponivel_e_desconhecido_nao_zero(app, cenario, loc):
    """CD que nunca cadastrou quantas tem não pode aparecer como zerado.

    Zero é uma afirmação ("não há nenhuma"); None é a verdade ("ninguém contou").
    Trocar uma pela outra faria a tela mentir sobre o CD que só não preencheu.
    """
    from app.models import Localidade
    outro = Localidade(sigla='XX', nome='CD Sem Cadastro', empresa_id=cenario.mb.id)
    db.session.add(outro)
    db.session.flush()
    c2 = Coletor(serial_number='SN-X', numero_patrimonio='PAT-X',
                 localidade_id=outro.id, status='Em Uso')
    db.session.add(c2)
    db.session.flush()
    m = Movimentacao(coletor_id=c2.id, re_colaborador=cenario.colab.re,
                     colaborador_id=cenario.colab.id,
                     data_saida=AGORA - timedelta(hours=2))
    db.session.add(m)
    db.session.flush()
    db.session.add(MovimentacaoComplemento(movimentacao_id=m.id,
                                           categoria_id=cenario.bat.id,
                                           qtd_saida=2, qtd_devolvida=0))
    db.session.commit()

    saldos = {s['localidade'].sigla: s
              for s in rastreio_quantidades(None, cenario.mb.id, agora=AGORA)}
    assert saldos['XX']['cadastrada'] is None
    assert saldos['XX']['disponivel'] is None
    assert saldos['XX']['em_campo'] == 2


def test_rastreio_e_fail_closed(app, cenario):
    assert rastreio_itens([], cenario.mb.id) == []
    assert rastreio_quantidades([], cenario.mb.id) == []


def test_rastreio_nao_percorre_o_banco_por_linha(app, cenario):
    """Anti-N+1: a tela mais pesada do pacote não pode consultar por peça."""
    from sqlalchemy import event

    _emprestar(cenario, qtd_bat=3, com_headset=True, horas_atras=30)
    mapa = rastreio_itens(None, cenario.mb.id, agora=AGORA)

    consultas = []
    engine = db.session.get_bind()

    def ouvir(*args, **kwargs):
        consultas.append(1)

    event.listen(engine, 'before_cursor_execute', ouvir)
    try:
        for r in mapa:                         # o que o template faz por linha
            _ = (r['item'].identificador, r['categoria'],
                 r['localidade'].sigla if r['localidade'] else None,
                 r['coletor'].patrimonio_display if r['coletor'] else None,
                 r['colaborador'].nome if r['colaborador'] else None)
    finally:
        event.remove(engine, 'before_cursor_execute', ouvir)

    assert consultas == [], f'{len(consultas)} consulta(s) extra(s) por linha'


# ---------------------------------------------------------------------------
# a tela
# ---------------------------------------------------------------------------

def test_tela_de_rastreio_mostra_so_as_pecas_identificadas(admin_client, cenario):
    """🔴 B5 — a seção de QUANTIDADES saiu, e a tela ficou com uma pergunta só.

    Ela virava uma tabela de saldos por CD — exatamente o que os cards da tela
    de Baterias passaram a mostrar, com mais clareza e no lugar certo. Duas
    telas respondendo a mesma pergunta divergem no dia em que só uma for
    ajustada.

    O domínio não mudou: `rastreio_quantidades` continua viva e testada logo
    acima; quem a consome agora é o painel de baterias.
    """
    _emprestar(cenario, qtd_bat=3, com_headset=True, horas_atras=30)

    html = admin_client.get('/complementos/rastreio').get_data(as_text=True)
    assert 'HS-H5' in html                       # peça identificada
    assert 'Ana Portadora' in html               # com quem está
    assert 'PAT-H5' in html                      # em qual coletor
    # A categoria se chama 'BATERIA' em maiúsculas; o link do menu lateral diz
    # "Baterias", então a ausência abaixo é da SEÇÃO, não do menu.
    assert 'BATERIA' not in html,         'a seção de quantidades continua na tela — a B5 mandou tirar'


def test_tela_filtra_so_o_que_estourou_o_prazo(admin_client, cenario):
    _emprestar(cenario, com_headset=True, horas_atras=1)      # dentro do prazo

    html = admin_client.get('/complementos/rastreio?atrasados=1').get_data(as_text=True)
    assert 'HS-H5' not in html


def test_filtro_de_localidade_nao_amplia_o_escopo(cliente_logado, duas_empresas):
    """IDOR de leitura: o id na query string estreita, nunca abre.

    Sem a checagem, bastaria trocar ?localidade= para o usuário da Acme ler o CD
    da MB — a mesma família B que o S2 fechou.
    """
    du = duas_empresas
    # Uma peça REAL no CD da MB — sem isso o teste passaria à toa, porque o CD
    # alheio nunca aparece na tela do userB de qualquer jeito.
    cat = CategoriaComplemento(empresa_id=du.mb.id, nome='HEADSET A',
                               controle=COMPLEMENTO_UNIDADE)
    db.session.add(cat)
    db.session.flush()
    db.session.add(ItemComplementar(empresa_id=du.mb.id, categoria_id=cat.id,
                                    identificador='SEGREDO-A',
                                    localidade_id=du.locA.id))
    db.session.commit()

    # o dono enxerga a peça...
    dono = cliente_logado(du.userA).get('/complementos/rastreio').get_data(as_text=True)
    assert 'SEGREDO-A' in dono, 'o teste não vale nada se o dono também não vê'

    # ...e o vizinho não, nem apontando o id do CD alheio na query string
    r = cliente_logado(du.userB).get(
        f'/complementos/rastreio?localidade={du.locA.id}')
    assert r.status_code == 200
    assert 'SEGREDO-A' not in r.get_data(as_text=True)


def test_inventario_ignora_quem_esta_no_prazo(admin_client, cenario):
    """A outra metade da regra: em campo dentro do prazo não pinta o inventário."""
    _emprestar(cenario, qtd_bat=2, com_headset=True, horas_atras=1)

    html = admin_client.get('/coletores').get_data(as_text=True)
    assert 'VOLTOU FALTANDO' not in html
    assert 'FORA DO PRAZO' not in html
