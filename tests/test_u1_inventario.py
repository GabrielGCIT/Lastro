"""
Testes da U1 — inventário respirável e a primeira ação em lote.

Oito colunas, e na operação típica cinco viram parede de traços. As ações eram
dois ícones cinza idênticos, um com "i" e outro com um cadeado. E tudo era um de
cada vez.

O teste que dá nome à fatia é `test_nao_move_coletor_de_outro_tenant`: numa rota
que recebe ids em MASSA, validar só o destino deixaria alguém puxar equipamento
alheio para dentro do próprio CD forjando a lista.
"""
import pytest

from app import db
from app.models import Coletor, Localidade


@pytest.fixture()
def frota(app, loc):
    """Dois CDs da MB e três coletores livres no primeiro."""
    from types import SimpleNamespace

    outro = Localidade(sigla='OS', nome='Osasco', empresa_id=loc.empresa_id)
    db.session.add(outro)
    db.session.flush()

    livres = []
    for i in range(1, 4):
        c = Coletor(serial_number=f'SN-U1{i}', numero_patrimonio=f'U1{i}',
                    localidade_id=loc.id, status='Disponível')
        db.session.add(c)
        livres.append(c)
    db.session.commit()
    return SimpleNamespace(origem=loc, destino=outro, livres=livres)


def _mover(client, ids, destino_id):
    return client.post('/coletores/mover',
                       data={'coletor_ids': [str(i) for i in ids],
                             'localidade_destino': str(destino_id)},
                       follow_redirects=True)


# ---------------------------------------------------------------------------
# a tabela
# ---------------------------------------------------------------------------

def test_colunas_extras_ficam_escondidas_por_padrao(admin_client, frota):
    """Alcance, diagnóstico e observação vêm vazias na maioria das linhas e
    escondem o que importa. Quem precisa liga."""
    padrao = admin_client.get('/coletores').get_data(as_text=True)
    assert 'Mostrar mais colunas' in padrao
    assert 'col_diagnostico' not in padrao

    tudo = admin_client.get('/coletores?colunas=tudo').get_data(as_text=True)
    assert 'Mostrar menos colunas' in tudo
    # o cabeçalho extra só existe na visão completa
    assert tudo.count('<th') > padrao.count('<th')


def test_coluna_de_local_saiu_por_ser_redundante(admin_client, frota):
    """O patrimônio já mostra a sigla ("GR 1001"); a coluna repetia com um selo."""
    html = admin_client.get('/coletores').get_data(as_text=True)
    assert 'GR U11' in html                      # sigla vem no patrimônio
    assert html.count('badge bg-secondary">GR<') == 0


def test_acoes_tem_rotulo_e_nao_so_icone(admin_client, frota):
    """Dois ícones cinza idênticos obrigavam a passar o mouse para descobrir."""
    html = admin_client.get('/coletores').get_data(as_text=True)
    assert 'Histórico' in html
    assert 'Editar' in html


# ---------------------------------------------------------------------------
# a ação em lote
# ---------------------------------------------------------------------------

def test_move_os_marcados(admin_client, frota):
    ids = [c.id for c in frota.livres]
    r = _mover(admin_client, ids, frota.destino.id)
    assert r.status_code == 200
    for c in frota.livres:
        db.session.refresh(c)
        assert c.localidade_id == frota.destino.id


def test_destino_invalido_nao_move_nada(admin_client, frota):
    r = _mover(admin_client, [frota.livres[0].id], 99999)
    db.session.refresh(frota.livres[0])
    assert frota.livres[0].localidade_id == frota.origem.id
    assert 'destino' in r.get_data(as_text=True)


def test_sem_selecao_nao_faz_nada(admin_client, frota):
    r = admin_client.post('/coletores/mover',
                          data={'localidade_destino': str(frota.destino.id)},
                          follow_redirects=True)
    assert 'Marque ao menos um' in r.get_data(as_text=True)


def test_nao_move_coletor_de_outro_tenant(cliente_logado, duas_empresas):
    """🔴 A razão de a ORIGEM ser validada, e não só o destino.

    Numa rota que recebe ids em massa, validar apenas o destino deixaria o
    usuário da Acme puxar um coletor da MB para dentro do CD dele — bastaria
    forjar o id na lista. Mesma família B que o S2 fechou.
    """
    du = duas_empresas
    alheio = Coletor(serial_number='SN-MB', numero_patrimonio='MB01',
                     localidade_id=du.locA.id, status='Disponível')
    db.session.add(alheio)
    db.session.commit()
    origem = alheio.localidade_id

    # O destino precisa ser INDISCUTIVELMENTE válido para o userB. Sem isto o
    # teste passava pelo motivo errado — quem barrava era o destino, e o guarda
    # de ORIGEM, que é o que este teste existe para provar, nunca era exercitado.
    proprio = Coletor(serial_number='SN-OK', numero_patrimonio='AC00',
                      localidade_id=du.locB.id, status='Disponível')
    db.session.add(proprio)
    db.session.commit()
    conferencia = cliente_logado(du.userB).post(
        '/coletores/mover',
        data={'coletor_ids': [str(proprio.id)],
              'localidade_destino': str(du.locB.id)},
        follow_redirects=True)
    assert 'movido(s)' in conferencia.get_data(as_text=True), (
        'o destino tem de ser aceito, senão o teste não chega no guarda de origem')

    r = cliente_logado(du.userB).post(
        '/coletores/mover',
        data={'coletor_ids': [str(alheio.id)],
              'localidade_destino': str(du.locB.id)},
        follow_redirects=True)

    db.session.refresh(alheio)
    assert alheio.localidade_id == origem, 'coletor de outro tenant foi movido'
    # O texto mudou na U4 (a palavra "escopo" saiu das telas). O que o teste
    # precisa provar é que a recusa foi DITA, não a frase exata.
    assert 'não foram movidos' in r.get_data(as_text=True)


def test_nao_move_para_cd_de_outro_tenant(cliente_logado, duas_empresas):
    """A outra ponta: o destino também tem de estar no escopo de escrita."""
    du = duas_empresas
    meu = Coletor(serial_number='SN-ACME', numero_patrimonio='AC01',
                  localidade_id=du.locB.id, status='Disponível')
    db.session.add(meu)
    db.session.commit()

    cliente_logado(du.userB).post(
        '/coletores/mover',
        data={'coletor_ids': [str(meu.id)],
              'localidade_destino': str(du.locA.id)},
        follow_redirects=True)

    db.session.refresh(meu)
    assert meu.localidade_id == du.locB.id, 'coletor foi parar em CD de outro tenant'


def test_voltar_nao_manda_para_fora_do_sistema(admin_client, frota):
    """🔴 O `voltar` vem do formulário, e `redirect()` obedece a qualquer URL.

    Com um destino absoluto, a ação em lote viraria ponte para outro site — com
    a sessão do usuário já aberta. A guarda nasceu na B4, em complementos, e
    subiu para `helpers.voltar_seguro` justamente para cobrir esta rota também:
    guarda duplicada é guarda que diverge no dia em que só uma for corrigida.
    """
    resposta = admin_client.post('/coletores/mover', data={
        'coletor_ids': [str(frota.livres[0].id)],
        'localidade_destino': str(frota.destino.id),
        'voltar': 'https://exemplo-malicioso.test/colhe',
    })

    assert resposta.status_code in (301, 302)
    assert 'exemplo-malicioso' not in resposta.headers.get('Location', '')

    # e o caminho relativo legítimo continua sendo respeitado
    ok = admin_client.post('/coletores/mover', data={
        'coletor_ids': [str(frota.livres[1].id)],
        'localidade_destino': str(frota.destino.id),
        'voltar': '/coletores?status=Dispon%C3%ADvel',
    })
    assert '/coletores?status=' in ok.headers.get('Location', '')
