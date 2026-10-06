"""
Testes da B2 — a tela de baterias.

Um card por CD, com quantas há, quantas estão na rua, quantas dá para emprestar
agora e quantas estouraram o prazo. Cadastro em cartões com mais e menos.

O teste que dá nome à fatia é `test_cd_zerado_aparece_igual`: um CD que some da
tela é um CD que ninguém lembra de abastecer — e é justamente o que mais precisa
aparecer.
"""
from datetime import datetime, timedelta

import pytest

from app import db
from app.startup import NOME_EMPRESA
from app.complementos import painel_baterias
from app.models import (CategoriaComplemento, Coletor, Colaborador, Empresa,
                        EstoqueComplemento, Localidade, Movimentacao,
                        MovimentacaoComplemento, COMPLEMENTO_QUANTIDADE,
                        COMPLEMENTO_CAMARA_CLIMATIZADO, COMPLEMENTO_CAMARA_SECO,
                        ICONE_BATERIA_PADRAO, COR_BATERIA_PADRAO)

AGORA = datetime.now()


@pytest.fixture()
def cenario(app, loc):
    """GR com 40 comuns e 20 climatizadas; OS cadastrado e vazio."""
    from types import SimpleNamespace

    mb = Empresa.query.filter_by(nome=NOME_EMPRESA).first()
    vazio = Localidade(sigla='OS', nome='Osasco', empresa_id=mb.id)
    db.session.add(vazio)

    comum = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA SECO',
                                 controle=COMPLEMENTO_QUANTIDADE, e_bateria=True,
                                 camara_atendida=COMPLEMENTO_CAMARA_SECO)
    fria = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA CLIMATIZADO',
                                controle=COMPLEMENTO_QUANTIDADE, e_bateria=True,
                                camara_atendida=COMPLEMENTO_CAMARA_CLIMATIZADO)
    # Não é bateria: não pode entrar em nenhuma conta desta tela.
    cabo = CategoriaComplemento(empresa_id=mb.id, nome='CABO USB',
                                controle=COMPLEMENTO_QUANTIDADE)
    db.session.add_all([comum, fria, cabo])
    db.session.flush()

    for cat, qtd in ((comum, 40), (fria, 20), (cabo, 99)):
        db.session.add(EstoqueComplemento(empresa_id=mb.id, categoria_id=cat.id,
                                          localidade_id=loc.id, qtd_cadastrada=qtd))

    coletor = Coletor(serial_number='SN-B2', numero_patrimonio='B2',
                      localidade_id=loc.id, status='Em Uso')
    colab = Colaborador(re='2200', nome='Quem Levou', empresa_id=mb.id)
    db.session.add_all([coletor, colab])
    db.session.commit()
    return SimpleNamespace(mb=mb, gr=loc, os=vazio, comum=comum, fria=fria,
                           cabo=cabo, coletor=coletor, colab=colab)


def _emprestar(c, cat, qtd, horas_atras, devolvidas=0):
    mov = Movimentacao(coletor_id=c.coletor.id, re_colaborador=c.colab.re,
                       colaborador_id=c.colab.id,
                       data_saida=AGORA - timedelta(hours=horas_atras))
    db.session.add(mov)
    db.session.flush()
    db.session.add(MovimentacaoComplemento(movimentacao_id=mov.id,
                                           categoria_id=cat.id, qtd_saida=qtd,
                                           qtd_devolvida=devolvidas))
    db.session.commit()
    return mov


def _por_tipo(cenario, loc_id):
    """{nome do tipo: quantidade} daquele CD — a quebra deixou de ser o par fixo
    comum/climatizada e passou a ser uma lista por tipo."""
    cd = painel_baterias([loc_id], cenario.mb.id)[0]
    return {t['categoria'].nome: t['cadastrada'] for t in cd['tipos']}


# ---------------------------------------------------------------------------
# o painel
# ---------------------------------------------------------------------------

def test_cd_zerado_aparece_igual(app, cenario):
    """🔴 `rastreio_quantidades` só devolve par com estoque OU movimento.

    Usá-la sozinha faria o CD sem bateria nenhuma sumir da tela — e um CD que
    some é um CD que ninguém lembra de abastecer.
    """
    cds = {c['localidade'].sigla: c for c in painel_baterias(None, cenario.mb.id)}
    assert set(cds) == {'GR', 'OS'}
    assert cds['OS']['cadastrada'] is None, 'ninguém contou ≠ contaram e deu zero'
    assert cds['OS']['disponivel'] is None
    assert cds['OS']['em_campo'] == 0


def test_numeros_do_card_fecham_a_conta(app, cenario):
    _emprestar(cenario, cenario.comum, 5, horas_atras=2)

    gr = painel_baterias([cenario.gr.id], cenario.mb.id)[0]
    assert gr['cadastrada'] == 60          # 40 comuns + 20 frias, sem o cabo
    assert gr['em_campo'] == 5
    assert gr['disponivel'] == 55
    assert gr['cadastrada'] == gr['disponivel'] + gr['em_campo']


def test_quebra_por_tipo_soma_o_total(app, cenario):
    """A quebra é POR TIPO, e não o par fixo comum/climatizada.

    Os tipos são quantos a operação precisar — bateria de headset já está no
    horizonte — e cada um carrega a própria aparência.
    """
    gr = painel_baterias([cenario.gr.id], cenario.mb.id)[0]
    por_nome = {t['categoria'].nome: t['cadastrada'] for t in gr['tipos']}
    assert por_nome == {'BATERIA SECO': 40, 'BATERIA CLIMATIZADO': 20}
    assert sum(por_nome.values()) == gr['cadastrada']


def test_tipo_zerado_nao_aparece_no_card(app, cenario):
    """🔴 Pedido do Gabriel: "caso esteja zerado, esse ícone nem aparece no card".

    Um tipo com zero naquele CD é um tipo que aquele CD não usa; mostrá-lo enche
    o card de zeros que não ajudam a decidir nada.
    """
    admin = None    # o ajuste é feito direto no modelo, sem passar pela tela
    linha = EstoqueComplemento.query.filter_by(
        categoria_id=cenario.fria.id, localidade_id=cenario.gr.id).first()
    linha.qtd_cadastrada = 0
    db.session.commit()

    gr = painel_baterias([cenario.gr.id], cenario.mb.id)[0]
    nomes = [t['categoria'].nome for t in gr['tipos']]
    assert 'BATERIA SECO' in nomes
    assert 'BATERIA CLIMATIZADO' not in nomes
    # mas o total continua honesto: o zero cadastrado ainda é um cadastro
    assert gr['cadastrada'] == 40


def test_aparencia_e_escolhida_e_nao_derivada_da_camara(admin_client, cenario):
    """🔴 O bug que o Gabriel viu.

    Uma bateria climatizada cadastrada como "serve em qualquer coletor" aparecia
    com o ícone da comum, porque o ícone era derivado da CÂMARA. Câmara é regra
    de compatibilidade; ícone e cor são identificação. Coisas diferentes.
    """
    admin_client.post('/baterias/tipo/criar', data={
        'nome': 'BATERIA CLIMATIZADA', 'camara_atendida': '',   # sem restrição
        'icone': 'fa-snowflake', 'cor': 'azul',
    }, follow_redirects=True)

    nova = CategoriaComplemento.query.filter_by(nome='BATERIA CLIMATIZADA').first()
    assert nova.icone == 'fa-snowflake', 'a câmara vazia não pode ditar o ícone'
    assert nova.cor == 'azul'


def test_icone_e_cor_fora_da_lista_caem_no_padrao(admin_client, cenario):
    """🔴 Estes valores vão para dentro de `class` e de `style` no template.

    Aceitar o que o formulário mandar seria deixar o campo escrever CSS e markup
    na página. A lista branca é a defesa, e o padrão é a queda silenciosa — é
    enfeite, não pode virar erro na cara de quem só queria cadastrar.
    """
    admin_client.post('/baterias/tipo/criar', data={
        'nome': 'BATERIA XPTO', 'camara_atendida': '',
        'icone': '"><script>alert(1)</script>', 'cor': 'red; background:url(x)',
    }, follow_redirects=True)

    nova = CategoriaComplemento.query.filter_by(nome='BATERIA XPTO').first()
    assert nova.icone == ICONE_BATERIA_PADRAO
    assert nova.cor == COR_BATERIA_PADRAO


def test_da_para_trocar_a_aparencia_de_tipo_existente(admin_client, cenario):
    """Os tipos criados antes desta fatia nasceram sem aparência. Sem poder
    trocar, a saída seria recriar o tipo — quebrando o histórico preso a ele."""
    admin_client.post(f'/baterias/tipo/{cenario.fria.id}/aparencia',
                      data={'icone': 'fa-snowflake', 'cor': 'azul'},
                      follow_redirects=True)
    db.session.expire_all()
    tipo = db.session.get(CategoriaComplemento, cenario.fria.id)
    assert tipo.icone == 'fa-snowflake'
    assert tipo.cor == 'azul'


def test_cor_desconhecida_no_banco_nao_derruba_a_tela(app, cenario):
    """Valor estranho vindo de importação ou edição manual é enfeite quebrado,
    não pode derrubar a tela de estoque inteira."""
    from app.models import cor_de_bateria
    assert len(cor_de_bateria('inexistente')) == 3
    assert len(cor_de_bateria(None)) == 3


def test_o_que_nao_e_bateria_fica_de_fora(app, cenario):
    """O CABO USB tem 99 em estoque e é contado por quantidade. Antes da B1 ele
    entraria — a heurística confundia 'contado' com 'bateria'."""
    gr = painel_baterias([cenario.gr.id], cenario.mb.id)[0]
    assert gr['cadastrada'] == 60, 'o cabo entrou na conta de baterias'


def test_atraso_conta_pecas_e_nao_emprestimos(app, cenario):
    """🔴 "2 em atraso" no card significa DUAS BATERIAS na rua, não dois
    empréstimos. É o número que o CD precisa repor."""
    _emprestar(cenario, cenario.comum, 3, horas_atras=40)     # 1 empréstimo, 3 peças
    gr = painel_baterias([cenario.gr.id], cenario.mb.id)[0]
    assert gr['atrasadas'] == 3


def test_dentro_do_prazo_nao_conta_como_atraso(app, cenario):
    _emprestar(cenario, cenario.comum, 4, horas_atras=2)
    gr = painel_baterias([cenario.gr.id], cenario.mb.id)[0]
    assert gr['em_campo'] == 4
    assert gr['atrasadas'] == 0, 'operação normal não é dívida'


def test_painel_e_fail_closed(app, cenario):
    assert painel_baterias([], cenario.mb.id) == []


# ---------------------------------------------------------------------------
# as telas
# ---------------------------------------------------------------------------

def test_tela_mostra_um_card_por_cd(admin_client, cenario):
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'GR' in html and 'OS' in html
    assert 'Guarulhos' in html and 'Osasco' in html


def test_tela_avisa_quando_nao_ha_tipo_de_bateria(admin_client, loc):
    """Cards todos vazios sem explicação é o que confundia na tela antiga."""
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'Nenhum tipo de bateria cadastrado' in html


def test_detalhe_oferece_cartao_para_todo_tipo(admin_client, cenario):
    """🔴 O ajuste itera as CATEGORIAS, não as linhas de estoque.

    Iterando as linhas, o CD zerado — justamente o que precisa cadastrar — não
    teria cartão nenhum para preencher.
    """
    html = admin_client.get(f'/baterias/{cenario.os.id}').get_data(as_text=True)
    assert 'BATERIA SECO' in html
    assert 'BATERIA CLIMATIZADO' in html
    assert 'name="qtd_' in html


def test_salvar_grava_as_duas_de_uma_vez(admin_client, cenario):
    admin_client.post(f'/baterias/{cenario.os.id}/salvar', data={
        f'qtd_{cenario.comum.id}': '12',
        f'qtd_{cenario.fria.id}': '7',
    }, follow_redirects=True)

    tipos = _por_tipo(cenario, cenario.os.id)
    assert tipos['BATERIA SECO'] == 12
    assert tipos['BATERIA CLIMATIZADO'] == 7
    assert painel_baterias([cenario.os.id], cenario.mb.id)[0]['cadastrada'] == 19


def test_campo_em_branco_nao_zera_o_estoque(admin_client, cenario):
    """🔴 Campo vazio é AUSÊNCIA de informação, não zero.

    Zerar por engano apagaria a base do cálculo de saldo — e o CD passaria a
    achar que não tem bateria nenhuma.
    """
    admin_client.post(f'/baterias/{cenario.gr.id}/salvar', data={
        f'qtd_{cenario.comum.id}': '',
        f'qtd_{cenario.fria.id}': '25',
    }, follow_redirects=True)

    tipos = _por_tipo(cenario, cenario.gr.id)
    assert tipos['BATERIA SECO'] == 40, 'o campo em branco zerou o estoque'
    assert tipos['BATERIA CLIMATIZADO'] == 25


def test_quantidade_negativa_e_recusada(admin_client, cenario):
    admin_client.post(f'/baterias/{cenario.gr.id}/salvar', data={
        f'qtd_{cenario.comum.id}': '-3',
    }, follow_redirects=True)
    assert _por_tipo(cenario, cenario.gr.id)['BATERIA SECO'] == 40


def test_detalhe_de_cd_alheio_e_recusado(cliente_logado, duas_empresas):
    """Trocar o id na barra de endereços não abre o CD de outro tenant."""
    du = duas_empresas
    r = cliente_logado(du.userB).get(f'/baterias/{du.locA.id}',
                                     follow_redirects=True)
    assert r.status_code == 200
    assert 'não está entre os que você acessa' in r.get_data(as_text=True)


def test_salvar_em_cd_alheio_e_recusado(cliente_logado, duas_empresas):
    du = duas_empresas
    cat = CategoriaComplemento(empresa_id=du.mb.id, nome='BATERIA X',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    db.session.add(cat)
    db.session.commit()

    cliente_logado(du.userB).post(f'/baterias/{du.locA.id}/salvar',
                                  data={f'qtd_{cat.id}': '99'},
                                  follow_redirects=True)
    assert EstoqueComplemento.query.filter_by(categoria_id=cat.id,
                                              localidade_id=du.locA.id).first() is None


def test_bateria_saiu_do_menu_de_complementos_e_ganhou_o_proprio(admin_client, cenario):
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert '/baterias' in html
    assert '/complementos' in html          # as duas entradas convivem


# ---------------------------------------------------------------------------
# os tipos, geridos na propria tela
# ---------------------------------------------------------------------------

def test_criar_tipo_pela_tela_de_baterias(admin_client, cenario):
    """🔴 O Gabriel: "não tenho que cadastrar baterias em outra tela, tem que ser
    aqui, tudo sobre baterias".

    A primeira versão mandava para Complementos — o lugar de onde a bateria
    acabara de sair. Criar tipo de bateria é da tela de baterias.
    """
    admin_client.post('/baterias/tipo/criar', data={
        'nome': 'bateria de headset', 'camara_atendida': '',
    }, follow_redirects=True)

    nova = CategoriaComplemento.query.filter_by(nome='BATERIA DE HEADSET').first()
    assert nova is not None
    assert nova.e_bateria is True
    assert nova.controle == COMPLEMENTO_QUANTIDADE, 'bateria é sempre contada'


def test_o_tipo_novo_aparece_no_ajuste(admin_client, cenario):
    """Criar e não ver é o mesmo que não criar."""
    admin_client.post('/baterias/tipo/criar',
                      data={'nome': 'BATERIA HEADSET', 'camara_atendida': ''},
                      follow_redirects=True)
    html = admin_client.get(f'/baterias/{cenario.gr.id}').get_data(as_text=True)
    assert 'BATERIA HEADSET' in html


def test_a_tela_nao_manda_mais_para_complementos(admin_client, loc):
    """O aviso apontava para a tela de onde a bateria acabou de sair."""
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'Cadastre em Complementos' not in html
    assert 'Crie o primeiro no formulário abaixo' in html


def test_o_formulario_nao_pergunta_como_controla(admin_client, cenario):
    """🔴 A pergunta que mais confundia na tela antiga não existe aqui: bateria é
    SEMPRE contada, então não há decisão a tomar."""
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'Como você controla' not in html
    assert 'name="controle"' not in html


def test_detalhe_oferece_criar_quando_falta_tipo(admin_client, cenario):
    """O cartão que falta é o próprio convite — foi assim que o Gabriel travou."""
    html = admin_client.get(f'/baterias/{cenario.gr.id}').get_data(as_text=True)
    assert 'Falta um tipo?' in html
    assert '#tipos' in html


def test_tipo_duplicado_e_recusado(admin_client, cenario):
    admin_client.post('/baterias/tipo/criar',
                      data={'nome': 'BATERIA SECO', 'camara_atendida': ''},
                      follow_redirects=True)
    iguais = CategoriaComplemento.query.filter_by(empresa_id=cenario.mb.id,
                                                  nome='BATERIA SECO').count()
    assert iguais == 1


def test_desativar_tipo_nao_apaga(admin_client, cenario):
    """Movimentação antiga aponta para o tipo; apagar deixaria a cobrança órfã."""
    admin_client.post(f'/baterias/tipo/{cenario.comum.id}/toggle',
                      follow_redirects=True)
    db.session.expire_all()
    tipo = db.session.get(CategoriaComplemento, cenario.comum.id)
    assert tipo is not None
    assert tipo.ativa is False


def test_tipo_inativo_continua_visivel_para_reativar(admin_client, cenario):
    """Um tipo que some da tela vira um tipo recriado duplicado."""
    admin_client.post(f'/baterias/tipo/{cenario.comum.id}/toggle',
                      follow_redirects=True)
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'BATERIA SECO' in html


def test_nao_mexe_em_tipo_de_outra_empresa(cliente_logado, duas_empresas):
    du = duas_empresas
    alheio = CategoriaComplemento(empresa_id=du.mb.id, nome='BATERIA ALHEIA',
                                  controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    db.session.add(alheio)
    db.session.commit()

    cliente_logado(du.userB).post(f'/baterias/tipo/{alheio.id}/toggle',
                                  follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(CategoriaComplemento, alheio.id).ativa is True


def test_toggle_recusa_categoria_que_nao_e_bateria(admin_client, cenario):
    """A rota é da tela de baterias: não pode virar porta dos fundos para mexer
    no catálogo de complementos."""
    admin_client.post(f'/baterias/tipo/{cenario.cabo.id}/toggle',
                      follow_redirects=True)
    db.session.expire_all()
    assert db.session.get(CategoriaComplemento, cenario.cabo.id).ativa is True


def test_o_seletor_de_aparencia_realmente_renderiza(admin_client, cenario):
    """🔴 O teste que faltava, e a falha que ele teria pego.

    Os testes de aparência POSTavam direto na rota e passavam — mas as listas
    brancas nunca chegaram ao template: a inserção caiu na rota de COMPLEMENTOS,
    que tem a mesma linha de âncora do `render_template`. Resultado em homolog:
    selects vazios, zero rádios, e nenhum erro em lugar nenhum.

    Testar o POST prova que o back-end aceita; só renderizar prova que o usuário
    consegue escolher.
    """
    from app.models import BATERIA_ICONES, BATERIA_CORES

    html = admin_client.get('/baterias').get_data(as_text=True)
    for chave, _rotulo in BATERIA_ICONES:
        assert f'value="{chave}"' in html, f'ícone {chave} não foi oferecido'
    for chave, _rot, _f, _b, _t in BATERIA_CORES:
        assert f'value="{chave}"' in html, f'cor {chave} não foi oferecida'


def test_a_tabela_de_tipos_oferece_a_troca(admin_client, cenario):
    """Sem as opções dentro do select, o formulário existe mas não muda nada."""
    html = admin_client.get('/baterias').get_data(as_text=True)
    assert f'/baterias/tipo/{cenario.comum.id}/aparencia' in html
    assert 'fa-snowflake' in html, 'o select de ícone veio sem opções'


# ---------------------------------------------------------------------------
# Os três defeitos que o Gabriel viu na tela e nenhum teste meu pegava.
# ---------------------------------------------------------------------------
def test_a_pergunta_da_camara_oferece_so_o_que_existe(admin_client, cenario):
    """Duas opções reais, e nenhuma delas repetida.

    🔴 A tela nascia com TRÊS: "Serve em qualquer coletor" e "Sim — e por isso
    serve em qualquer coletor" eram a MESMA coisa escrita de dois jeitos, porque
    eu reaproveitei os rótulos da tela de complementos, que respondiam outra
    pergunta ("aguenta o congelado?"). Aqui a pergunta é onde a bateria é usada.

    Só no climatizado não é opção porque não existe na prateleira: a bateria
    feita para o frio funciona no seco também — é a assimetria da D5.
    """
    html = admin_client.get('/baterias').get_data(as_text=True)
    form = html[html.index('/baterias/tipo/criar'):]
    form = form[:form.index('</form>')]
    # Recorta o SELETOR DA CÂMARA, e não o formulário inteiro: contar as opções
    # do form todo media também as perguntas vizinhas, e quebrava sozinho quando
    # o cadastro ganhou "de qual equipamento é esta bateria?".
    seletor = form[form.index('name="camara_atendida"'):]
    seletor = seletor[:seletor.index('</select>')]

    assert seletor.count('<option') == 3, 'a pergunta da câmara mudou de tamanho'
    form = seletor
    assert 'value="SECO"' in form and 'value="CLIMATIZADO"' in form
    assert 'Só no ambiente seco' in form
    assert 'Nos dois' in form
    assert 'Serve em qualquer coletor' not in form, (
        'voltou o rótulo da tela de complementos, que duplicava a resposta')


def test_a_coluna_fala_a_mesma_lingua_do_seletor(admin_client, cenario):
    """O que se escolhe no formulário é o que se lê na lista.

    Antes o seletor dizia "Sim — e por isso serve em qualquer coletor" e a mesma
    bateria aparecia na tabela como "Climatizado — serve em qualquer". Duas
    frases para um estado só.
    """
    cenario.comum.camara_atendida = 'CLIMATIZADO'
    cenario.fria.camara_atendida = 'SECO'
    db.session.commit()

    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'Seco e climatizado' in html
    assert 'Só no seco' in html
    assert 'serve em qualquer' not in html


def test_tipo_sem_camara_e_denunciado(admin_client, cenario):
    """Sem câmara declarada o portão do D6 não protege nada.

    `atende_camara` devolve True para qualquer coletor quando o campo é NULL —
    exatamente o oposto do que a tela sugeria ao mostrar isso como um traço
    cinza discreto. É pendência, e a linha tem de dizer isso.
    """
    cenario.comum.camara_atendida = None
    db.session.commit()

    html = admin_client.get('/baterias').get_data(as_text=True)
    assert 'Não informado' in html
    assert 'bg-danger' in html


def test_a_linha_conserta_a_camara_que_nasceu_vazia(admin_client, cenario):
    """Os tipos criados antes desta fatia precisam de conserto SEM recriar.

    Recriar o tipo quebraria o histórico de movimentação preso a ele.
    """
    cenario.comum.camara_atendida = None
    db.session.commit()

    admin_client.post(f'/baterias/tipo/{cenario.comum.id}/aparencia', data={
        'icone': 'fa-snowflake', 'cor': 'azul', 'camara_atendida': 'SECO',
    }, follow_redirects=True)

    db.session.refresh(cenario.comum)
    assert cenario.comum.camara_atendida == 'SECO'
    assert cenario.comum.icone == 'fa-snowflake'


def test_camara_invalida_nao_entra_pela_linha(admin_client, cenario):
    """O campo não pode gravar um estado que o domínio não conhece."""
    cenario.comum.camara_atendida = 'SECO'
    db.session.commit()

    admin_client.post(f'/baterias/tipo/{cenario.comum.id}/aparencia', data={
        'icone': 'fa-bolt', 'cor': 'azul', 'camara_atendida': 'CONGELADOR',
    }, follow_redirects=True)

    db.session.refresh(cenario.comum)
    assert cenario.comum.camara_atendida == 'SECO', 'aceitou câmara inventada'


def test_os_seletores_da_linha_tem_rotulo(admin_client, cenario):
    """🔴 "aquelas setinhas pra baixo ali eu não entendi o que é" — o Gabriel.

    Eram três selects sem rótulo E sem opções (as listas brancas não chegavam ao
    template). Setinha muda que não faz nada.
    """
    html = admin_client.get('/baterias').get_data(as_text=True)
    linha = html[html.index(f'/baterias/tipo/{cenario.comum.id}/aparencia'):]
    linha = linha[:linha.index('</form>')]

    for campo in (f'for="ic{cenario.comum.id}"', f'for="co{cenario.comum.id}"',
                  f'for="ca{cenario.comum.id}"'):
        assert campo in linha, f'select sem rótulo: {campo}'
    assert 'fa-snowflake' in linha, 'o select de ícone veio vazio'
    assert 'Salvar' in linha, 'o botão não diz o que faz'
