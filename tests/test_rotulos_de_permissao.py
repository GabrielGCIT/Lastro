"""
Testes dos rótulos de módulo na tela de Grupos / Permissões.

🔴 A tela agrupa as permissões por módulo e desenha um selo colorido com o nome
de cada um — mas a lista de nomes é fixa no template. Um módulo fora da lista
caía num selo vazio: um retângulo branco, sem cor e sem nome, ao lado de
permissões que ninguém conseguia identificar.

O teste que dá nome ao arquivo é `test_nenhum_modulo_fica_sem_rotulo`: ele
checa que TODO módulo existente aparece nomeado — é o que impede a próxima
adição de repetir o mesmo silêncio.
"""
from app.models import Permissao


def test_nenhum_modulo_fica_sem_rotulo(admin_client, app):
    """🔴 O nome do arquivo: a tela tem de nomear todos os módulos que existem."""
    modulos = {p.modulo for p in Permissao.query.all() if p.modulo}
    assert modulos, 'o seed precisa ter permissões para este teste valer'

    html = admin_client.get('/grupos').get_data(as_text=True)

    esperado = {
        'operacional': 'Operacional',
        'relatorios': 'Relatórios',
        'admin': 'Administração',
    }
    faltando = [m for m in modulos if esperado.get(m, m).lower() not in html.lower()]
    assert not faltando, f'módulos sem rótulo na tela: {faltando}'


def test_nenhum_selo_sai_com_cor_vazia(admin_client):
    """O sintoma original: permissões num selo em branco."""
    html = admin_client.get('/grupos').get_data(as_text=True)

    assert 'bg- ' not in html and 'bg-"' not in html, (
        'sobrou selo com classe de cor vazia')


def test_modulo_novo_nao_some_da_tela(admin_client, app):
    """A proteção contra a repetição: um módulo que ninguém cadastrou no
    template ainda assim aparece, com o próprio nome."""
    from app import db

    db.session.add(Permissao(codigo='faturamento.ver',
                             descricao='Ver faturamento',
                             modulo='faturamento'))
    db.session.commit()

    html = admin_client.get('/grupos').get_data(as_text=True)

    assert 'Faturamento' in html, 'módulo novo sumiu em vez de aparecer pelo nome'
