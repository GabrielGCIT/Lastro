"""
Testes do backup automático e da tela de saúde.

🔴 O teste que dá nome ao arquivo é `test_servidor_ligado_depois_das_tres_faz_backup`.

A regra ingênua seria "rode às 3h". Num servidor que passa a madrugada
desligado — e servidor de CD é desligado, por manutenção, por queda de energia,
por política de economia — essa regra significa NUNCA fazer backup. E o modo de
falhar é o pior que existe: nada quebra, nada aparece no log, nenhum teste fica
vermelho. Só não há backup, e ninguém descobre até precisar de um.

Por isso a regra é "já passou das 3 e ainda não rodou hoje".
"""
import os
import sqlite3
from datetime import datetime, time, timedelta

import pytest

from app.agendador import iniciar, precisa_rodar, rodar_uma_vez
from app.saude import (diagnostico, verificar_backup, verificar_banco,
                       verificar_pastas, versao_instalada)

TRES_DA_MANHA = time(3, 0)


# ---------------------------------------------------------------------------
# QUANDO O BACKUP DEVE RODAR
# ---------------------------------------------------------------------------

def test_servidor_ligado_depois_das_tres_faz_backup():
    """🔴 O nome do arquivo: ligou às 8h, nunca fez backup — tem de fazer agora."""
    oito_da_manha = datetime(2026, 10, 6, 8, 0)

    assert precisa_rodar(oito_da_manha, ultimo_backup=None) is True


def test_antes_da_hora_nao_roda():
    assert precisa_rodar(datetime(2026, 10, 6, 2, 59), None) is False


def test_nao_roda_duas_vezes_no_mesmo_dia():
    """A thread acorda de 5 em 5 minutos; sem isso seriam 250 cópias por dia."""
    hoje_cedo = datetime(2026, 10, 6, 3, 1)
    mais_tarde = datetime(2026, 10, 6, 17, 30)

    assert precisa_rodar(mais_tarde, ultimo_backup=hoje_cedo) is False


def test_no_dia_seguinte_roda_de_novo():
    ontem = datetime(2026, 10, 5, 3, 1)

    assert precisa_rodar(datetime(2026, 10, 6, 3, 1), ultimo_backup=ontem) is True


def test_a_thread_nao_sobe_durante_os_testes(app):
    """Teste que copia o banco de verdade a cada boot é teste lento e traiçoeiro."""
    assert iniciar(app) is None


# ---------------------------------------------------------------------------
# A RODADA COMPLETA
# ---------------------------------------------------------------------------

@pytest.fixture()
def instalacao(tmp_path):
    """Uma raiz com banco, como a instalação de verdade."""
    root = tmp_path / 'lastro'
    (root / 'instance').mkdir(parents=True)
    banco = root / 'instance' / 'lastro.db'
    con = sqlite3.connect(str(banco))
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE coletores (id INTEGER PRIMARY KEY)')
    con.execute('INSERT INTO coletores DEFAULT VALUES')
    con.commit()
    con.close()
    return str(root), f'sqlite:///{banco}'


def test_rodar_uma_vez_copia_e_limpa(instalacao):
    root, uri = instalacao
    velho = datetime(2026, 1, 1, 3, 0)
    hoje = datetime(2026, 10, 6, 3, 0)

    rodar_uma_vez(uri, root, agora=velho)
    caminho, apagados = rodar_uma_vez(uri, root, agora=hoje)

    assert os.path.exists(caminho)
    assert apagados == ['lastro-20260101-0300.db'], \
        'a cópia de janeiro devia ter sido limpa'


# ---------------------------------------------------------------------------
# A TELA DE SAÚDE
# ---------------------------------------------------------------------------

def test_instalacao_nova_avisa_que_ainda_nao_ha_backup(instalacao):
    """No primeiro dia não há cópia — e isso não é erro, é "ainda não"."""
    root, _uri = instalacao

    item = verificar_backup(root, agora=datetime(2026, 10, 6, 10, 0))

    assert item['situacao'] == 'atencao'
    assert 'ainda' in item['resumo'].lower() or 'Nenhuma' in item['resumo']


def test_backup_de_ontem_esta_certo(instalacao):
    """O de hoje sai às 3h: às 10h de hoje, o de ontem é o esperado."""
    root, uri = instalacao
    ontem = datetime(2026, 10, 5, 3, 0)
    rodar_uma_vez(uri, root, agora=ontem)

    item = verificar_backup(root, agora=datetime(2026, 10, 6, 10, 0))

    assert item['situacao'] == 'ok'


def test_backup_parado_ha_dias_e_problema(instalacao):
    """🔴 O caso que a tela existe para pegar: parou e ninguém viu."""
    root, uri = instalacao
    rodar_uma_vez(uri, root, agora=datetime(2026, 10, 1, 3, 0))

    item = verificar_backup(root, agora=datetime(2026, 10, 6, 10, 0))

    assert item['situacao'] == 'erro'
    assert item['detalhe'], 'a tela tem de dizer o que fazer, não só pintar de vermelho'


def test_banco_sumido_e_erro(tmp_path):
    item = verificar_banco(f'sqlite:///{tmp_path}/nao-existe.db')

    assert item['situacao'] == 'erro'


def test_pasta_sem_escrita_e_erro(instalacao, monkeypatch):
    """🔴 Testa gravando — `os.access` mente em pasta de rede no Windows."""
    root, _uri = instalacao
    real = open

    def _recusar(caminho, *args, **kwargs):
        if '.escrita-teste' in str(caminho):
            raise OSError(13, 'Permissão negada')
        return real(caminho, *args, **kwargs)

    monkeypatch.setattr('builtins.open', _recusar)
    item = verificar_pastas(root)

    assert item['situacao'] == 'erro'
    assert 'instance' in item['detalhe']


def test_o_geral_e_o_PIOR_dos_itens(instalacao):
    """Backup parado com disco sobrando não é "quase bem"."""
    root, uri = instalacao
    rodar_uma_vez(uri, root, agora=datetime(2026, 10, 1, 3, 0))   # vencido

    diag = diagnostico(uri, root, agora=datetime(2026, 10, 6, 10, 0))

    assert diag['geral'] == 'erro'
    assert any(i['situacao'] == 'ok' for i in diag['itens']), \
        'o cenário precisa ter item bom junto, senão não prova que pegou o pior'


def test_a_versao_aparece(instalacao):
    """🔴 Sem versão na tela, "já atualizei" e "ainda não" são indistinguíveis."""
    root, uri = instalacao
    with open(os.path.join(root, 'VERSAO'), 'w', encoding='utf-8') as fh:
        fh.write('1.2.3\n')

    assert versao_instalada(root) == '1.2.3'
    assert diagnostico(uri, root)['versao'] == '1.2.3'


def test_sem_arquivo_de_versao_nao_quebra(instalacao):
    """Pasta montada à mão, sem o VERSAO: a tela ainda tem de abrir."""
    root, _uri = instalacao

    assert versao_instalada(root) == 'desconhecida'


def test_a_tela_abre_para_quem_administra(admin_client):
    html = admin_client.get('/saude').get_data(as_text=True)

    assert 'Saúde do sistema' in html


def test_a_tela_nao_abre_para_o_balcao(app, cliente_logado, duas_empresas):
    """Informação de administração da máquina não é do operador de pista."""
    from app.models import Grupo

    balcao = Grupo.query.filter_by(nome='BALCAO').first()
    du = duas_empresas
    du.userA.grupo_id = balcao.id
    from app import db
    db.session.commit()

    c = cliente_logado(du.userA)
    resposta = c.get('/saude', follow_redirects=False)

    assert resposta.status_code in (302, 403), 'o balcão alcançou a tela de saúde'
