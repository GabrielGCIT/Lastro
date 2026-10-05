"""
Testes do `rotulo` — como o sistema CHAMA um coletor numa frase.

`patrimonio_display` foi feito para CÉLULA DE TABELA: o '—' existe para a
coluna não ficar vazia, e a coluna de serial ao lado identifica a linha. Numa
frase não há coluna vizinha.

O teste que dá nome ao arquivo é `test_balcao_nao_diz_coletor_traco`: o
patrimônio é opcional (equipamento novo ainda não etiquetado), e o balcão
respondia "SUCESSO: Coletor — liberado para João da Silva". Na hora em que a
fila anda, o operador fica sem saber qual equipamento acabou de sair.

Que o problema era conhecido, o código dizia: o gráfico do painel operacional
comparava com o traço literal (`if patrimonio_display != '—'`) para desviar
dele caso a caso.
"""
import pytest

from app import db
from app.models import Coletor, Colaborador, Empresa


@pytest.fixture()
def sem_etiqueta(app, loc):
    """Coletor cadastrado antes de receber a plaqueta de patrimônio."""
    c = Coletor(serial_number='SN-SEM-PLACA', numero_patrimonio=None,
                localidade_id=loc.id, status='Disponível')
    db.session.add(c)
    db.session.commit()
    return c


@pytest.fixture()
def com_etiqueta(app, loc):
    c = Coletor(serial_number='SN-COM-PLACA', numero_patrimonio='253',
                localidade_id=loc.id, status='Disponível')
    db.session.add(c)
    db.session.commit()
    return c


def test_rotulo_cai_para_o_serial(sem_etiqueta):
    assert sem_etiqueta.rotulo == 'SN-SEM-PLACA'
    assert sem_etiqueta.patrimonio_display == '—', \
        'a COLUNA continua com traço — quem muda é a frase'


def test_rotulo_prefere_o_patrimonio(com_etiqueta, loc):
    """Com plaqueta, a frase fala a língua da etiqueta física."""
    assert com_etiqueta.rotulo == f'{loc.sigla} 253'


def test_balcao_nao_diz_coletor_traco(app, admin_client, sem_etiqueta, loc):
    """🔴 O nome do arquivo, no caminho real da retirada."""
    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    db.session.add(Colaborador(re='3001', nome='João da Silva', empresa_id=mb.id))
    db.session.commit()

    r = admin_client.post('/operacao/retirar', data={
        're_colaborador': '3001', 'busca_modo': 'serial',
        'busca_valor': 'SN-SEM-PLACA',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
    }, follow_redirects=True)
    html = r.get_data(as_text=True)

    assert 'Coletor — liberado' not in html, 'o traço mudo voltou'
    assert 'SN-SEM-PLACA' in html, 'a frase precisa dizer QUAL coletor saiu'


def test_card_do_balcao_manda_rotulo(app, cliente_logado, sem_etiqueta):
    """O título do card é `rotulo`; sem ele o operador lê um traço em negrito.

    Vai por `cliente_logado` e não por `admin_client`: sob /api/ o hook que
    re-sincroniza a sessão não roda, e sem empresa/owner na sessão o
    fail-closed do T3 responde "não encontrado" antes de chegar ao rótulo.
    """
    from app.models import Usuario
    admin = Usuario.query.filter_by(re='admin').first()
    r = cliente_logado(admin).get('/api/coletor/buscar?modo=serial&q=SN-SEM-PLACA')

    assert r.status_code == 200
    dados = r.get_json()
    assert dados['encontrado'] and dados['rotulo'] == 'SN-SEM-PLACA'


# ---------------------------------------------------------------------------
# AS TELAS ONDE O COLETOR APARECE SOZINHO
# ---------------------------------------------------------------------------

def test_bateria_na_rua_diz_em_qual_coletor(app, admin_client, sem_etiqueta, loc):
    """🔴 "O que está na rua" respondia "—" na coluna COLETOR.

    Aqui não há coluna de serial ao lado: essa coluna É a resposta para "em qual
    coletor está a bateria". Sem ela, quem procura a bateria não sabe por onde
    começar.
    """
    from app.models import (CategoriaComplemento, Colaborador, Empresa,
                            EstoqueComplemento, COMPLEMENTO_QUANTIDADE)

    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    cat = CategoriaComplemento(empresa_id=mb.id, nome='BATERIA PADRAO',
                               controle=COMPLEMENTO_QUANTIDADE, e_bateria=True)
    db.session.add_all([cat, Colaborador(re='3001', nome='João da Silva',
                                         empresa_id=mb.id)])
    db.session.flush()
    db.session.add(EstoqueComplemento(empresa_id=mb.id, categoria_id=cat.id,
                                      localidade_id=loc.id, qtd_cadastrada=10))
    db.session.commit()

    admin_client.post('/operacao/retirar', data={
        're_colaborador': '3001', 'busca_modo': 'serial',
        'busca_valor': 'SN-SEM-PLACA',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
        'complemento_categoria': str(cat.id),
        f'complemento_qtd_{cat.id}': '1',
    }, follow_redirects=True)

    html = admin_client.get(f'/baterias/{loc.id}').get_data(as_text=True)
    assert 'João da Silva' in html, 'a bateria não entrou em "o que está na rua"'
    linha = html[html.index('João da Silva'):][:1500]
    assert 'SN-SEM-PLACA' in linha, 'a coluna COLETOR não diz qual coletor é'


def test_historico_e_dashboard_nao_mostram_traco(app, admin_client, sem_etiqueta,
                                                 loc):
    """Histórico e bloco de pendências: coletor sem coluna de serial ao lado."""
    from app.models import Colaborador, Empresa

    mb = Empresa.query.filter_by(nome='Martin Brower').first()
    db.session.add(Colaborador(re='3001', nome='João da Silva', empresa_id=mb.id))
    db.session.commit()
    admin_client.post('/operacao/retirar', data={
        're_colaborador': '3001', 'busca_modo': 'serial',
        'busca_valor': 'SN-SEM-PLACA',
        'estado_visual': 'true', 'bateria_ok': 'true', 'leitura_ok': 'true',
        'identificacao_ok': 'true', 'equipamento_limpo': 'true',
    }, follow_redirects=True)

    html = admin_client.get('/historico').get_data(as_text=True)
    assert 'SN-SEM-PLACA' in html, 'o histórico não diz qual coletor saiu'
