"""
A porta de acesso é configurável depois da instalação.

🔴 O teste que dá nome ao arquivo é `test_o_arquivo_fica_em_instance`.

O arquivo de configuração mora em `instance/` porque é a única pasta que a
atualização não substitui. Se ficasse na raiz, a primeira correção enviada
sobrescreveria a porta escolhida pelo cliente — e ela voltaria ao padrão num
reinício qualquer, semanas depois, sem ninguém relacionar uma coisa com a outra.

O resto do arquivo defende a mesma ideia: configuração estragada não derruba o
sistema. Quem edita isso faz no Bloco de Notas, às vezes com o balcão parado e
gente esperando; um erro de digitação não pode custar a manhã seguinte.
"""
import os

import pytest

from app import configuracao


@pytest.fixture()
def pasta(tmp_path):
    d = tmp_path / 'instance'
    d.mkdir()
    return str(d)


def _escrever(pasta, texto):
    with open(configuracao.caminho(pasta), 'w', encoding='utf-8') as fh:
        fh.write(texto)


def test_o_arquivo_fica_em_instance(tmp_path):
    """🔴 O nome do arquivo: é a pasta que a atualização não toca."""
    destino = configuracao.caminho(str(tmp_path / 'instance'))

    assert os.path.basename(os.path.dirname(destino)) == 'instance'


def test_nasce_comentado_no_primeiro_boot(pasta):
    """Quem abre o arquivo precisa entender o que mexer sem perguntar a ninguém."""
    criado = configuracao.garantir_arquivo(pasta)

    with open(criado, encoding='utf-8') as fh:
        conteudo = fh.read()
    assert 'porta' in conteudo
    assert '#' in conteudo, 'sem comentário explicando o que é'
    assert 'firewall' in conteudo.lower(), \
        'não avisa que trocar a porta exige liberar no firewall'


def test_nao_sobrescreve_o_que_o_cliente_escreveu(pasta):
    """🔴 Rodar de novo não pode apagar a configuração de ninguém."""
    _escrever(pasta, 'porta = 8080\n')

    configuracao.garantir_arquivo(pasta)

    assert configuracao.porta(pasta) == 8080


def test_le_a_porta_configurada(pasta):
    _escrever(pasta, '# comentário\nporta = 9000\n')

    assert configuracao.porta(pasta) == 9000


def test_espaco_e_comentario_na_mesma_linha_nao_atrapalham(pasta):
    _escrever(pasta, '  porta   =   8080   # a 5001 estava ocupada\n')

    assert configuracao.porta(pasta) == 8080


def test_valor_estragado_cai_no_padrao_e_nao_derruba(pasta):
    """🔴 Erro de digitação não pode deixar o balcão sem sistema."""
    for lixo in ('porta = oitenta\n', 'porta =\n', 'porta = 80 80\n', 'porta\n'):
        _escrever(pasta, lixo)
        assert configuracao.porta(pasta) == configuracao.PORTA_PADRAO, lixo


def test_porta_fora_da_faixa_cai_no_padrao(pasta):
    """Abaixo de 1024 exige privilégio no Windows; acima de 65535 não existe."""
    for fora in ('porta = 80\n', 'porta = 0\n', 'porta = 99999\n'):
        _escrever(pasta, fora)
        assert configuracao.porta(pasta) == configuracao.PORTA_PADRAO, fora


def test_arquivo_ausente_usa_o_padrao(pasta):
    assert configuracao.porta(pasta) == configuracao.PORTA_PADRAO


def test_a_variavel_de_ambiente_vence(pasta, monkeypatch):
    """Quem define variável de ambiente sabe o que está fazendo — e é como o
    desenvolvimento sobe duas instâncias ao mesmo tempo."""
    _escrever(pasta, 'porta = 9000\n')
    monkeypatch.setenv('PORT', '7777')

    assert configuracao.porta(pasta) == 7777


def test_pasta_sem_permissao_nao_impede_o_boot(pasta, monkeypatch):
    """Ficar sem o arquivo é ruim; não subir é pior."""
    def _recusar(*a, **k):
        raise OSError(13, 'Permissão negada')

    monkeypatch.setattr('builtins.open', _recusar)

    assert configuracao.garantir_arquivo(pasta + '_novo') is None


def test_a_tela_de_saude_mostra_a_porta_que_esta_valendo(pasta):
    """Sem isso, descobrir a porta exigiria achar o arquivo — e quem precisa
    da informação é justamente quem não sabe onde ele fica."""
    from app.saude import verificar_porta

    _escrever(pasta, 'porta = 8080\n')
    item = verificar_porta(pasta)

    assert '8080' in item['resumo']
    assert configuracao.NOME_ARQUIVO in item['detalhe'], \
        'não diz onde trocar'


def test_porta_diferente_do_padrao_avisa_sobre_o_firewall(pasta):
    """🔴 Trocar a porta sem liberar no firewall reproduz exatamente o problema
    que levou dias para ser diagnosticado: o sistema sobe e ninguém acessa."""
    from app.saude import verificar_porta

    _escrever(pasta, 'porta = 8080\n')

    assert 'firewall' in verificar_porta(pasta)['detalhe'].lower()
