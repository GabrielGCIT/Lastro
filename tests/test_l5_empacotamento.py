"""
O que entra — e o que nunca pode entrar — no pacote entregue.

🔴 Este arquivo existe por causa da fronteira entre os dois produtos.

O pacote é a única coisa que sai daqui. Um arquivo a mais nele é um arquivo que
quem recebe o produto passa a ter, para sempre, sem ninguém perceber: o histórico
do Git carregaria todo o desenvolvimento; `instance/` levaria o banco de quem
desenvolveu; `tests/` levaria os cenários, com nomes e REs de exemplo.

E o modo de falhar é silencioso — o pacote funciona igual com ou sem esses
arquivos. Ninguém descobre abrindo o sistema; só abrindo o ZIP.

🔴 A primeira versão destes testes passava POR ENGANO. Descobri sabotando:
apaguei a lista `LIXO` inteira do empacotador e tudo continuou verde, porque no
momento em que os testes rodaram não havia `__pycache__` nas pastas copiadas, e
`tests/` ou `.git` jamais estariam DENTRO de `app/`. Os testes não provavam a
guarda; provavam que o cenário estava limpo. Agora o cenário é sujo de propósito
(ver a fixture `sujeira_plantada`) e a varredura percorre a árvore inteira, não
só a raiz — porque é dentro das pastas que esse tipo de coisa se esconde.
"""
import importlib.util
import os

import pytest

from app import ROOT_DIR
from app.startup import NOME_EMPRESA

_spec = importlib.util.spec_from_file_location(
    'empacotar', os.path.join(ROOT_DIR, 'scripts', 'empacotar.py'))
empacotar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(empacotar)


# Nada disto pode acabar no pacote. Lista separada da do empacotador de
# propósito: se as duas saírem do mesmo lugar, o teste concorda com o erro.
JAMAIS = [
    '.git',           # o desenvolvimento inteiro
    'tests',          # cenários, nomes e REs de exemplo
    'instance',       # o banco de quem desenvolveu
    'uploads',        # fotos de quem desenvolveu
    'backups',
    'logs',
    'venv',
    '.claude',        # ferramenta de desenvolvimento
    'dist',
    'requirements-dev.txt',
]


@pytest.fixture()
def sujeira_plantada():
    """Põe lixo DENTRO das pastas que o pacote copia, e limpa depois.

    Sem isto os testes deste arquivo não exercitam nada: a cópia é por inclusão,
    então o que mora fora de `app/templates/static/translations` nunca entraria
    de qualquer jeito, com ou sem a lista de exclusão.
    """
    plantados = [
        os.path.join(ROOT_DIR, 'app', '__pycache__', 'naodeveir.pyc'),
        os.path.join(ROOT_DIR, 'app', 'instance', 'banco-de-quem-desenvolveu.db'),
        os.path.join(ROOT_DIR, 'templates', '__pycache__', 'naodeveir.pyc'),
        os.path.join(ROOT_DIR, 'static', 'uploads', 'foto-de-alguem.png'),
        os.path.join(ROOT_DIR, 'app', 'tests', 'cenario.py'),
    ]
    criados = []
    try:
        for caminho in plantados:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(caminho, 'wb') as fh:
                fh.write(b'nao deve viajar no pacote')
            criados.append(caminho)
        yield
    finally:
        # O repositório tem de voltar exatamente como estava.
        for caminho in criados:
            try:
                os.remove(caminho)
            except OSError:
                pass
        for caminho in criados:
            pasta = os.path.dirname(caminho)
            try:
                if os.path.isdir(pasta) and not os.listdir(pasta):
                    os.rmdir(pasta)
            except OSError:
                pass


@pytest.fixture()
def pacote(tmp_path, sujeira_plantada):
    """Monta o pacote numa pasta temporária, SEM baixar dependência.

    O download é lento, precisa de rede e não é o que este arquivo defende.
    """
    destino = str(tmp_path / 'Lastro-teste')
    os.makedirs(destino)
    empacotar._copiar(destino)
    empacotar._escrever_instalador(destino, '9.9.9')
    empacotar._escrever_iniciar(destino)
    empacotar._escrever_servico_run(destino)
    empacotar._escrever_servico(destino, '9.9.9')
    empacotar._escrever_atualizar(destino, '9.9.9')
    empacotar._escrever_leiame(destino, '9.9.9')
    return destino


def test_o_pacote_nao_leva_o_que_e_so_nosso(pacote):
    """🔴 O nome do arquivo — varrendo a árvore INTEIRA, não só a raiz."""
    vazou = []
    for raiz, dirs, arquivos in os.walk(pacote):
        for nome in list(dirs) + list(arquivos):
            if nome in JAMAIS:
                vazou.append(os.path.relpath(os.path.join(raiz, nome), pacote))

    assert not vazou, f'o pacote levaria: {vazou}'


def test_nenhum_pycache_viaja_junto(pacote):
    """Bytecode do nosso ambiente, e peso à toa no pacote."""
    achados = []
    for raiz, dirs, _arquivos in os.walk(pacote):
        if '__pycache__' in dirs:
            achados.append(os.path.relpath(raiz, pacote))

    assert not achados, f'__pycache__ em: {achados[:5]}'


def test_o_pacote_leva_o_que_o_sistema_precisa(pacote):
    """O contraponto: um empacotador que copiasse NADA passaria nos de cima."""
    for essencial in ('run.py', 'VERSAO', 'requirements.txt',
                      'app', 'templates', 'static', 'translations'):
        assert os.path.exists(os.path.join(pacote, essencial)), \
            f'o pacote ficaria sem {essencial}'


def test_os_estaticos_vao_junto(pacote):
    """Sem o vendor local, a tela abre sem estilo numa rede sem internet.

    É o tipo de coisa que passa no teste de servidor e aparece só na máquina do
    cliente — onde o CDN não é alcançável.
    """
    vendor = os.path.join(pacote, 'static', 'vendor')

    assert os.path.isdir(vendor), 'os estáticos locais não foram'
    assert len(os.listdir(vendor)) >= 3, 'faltou biblioteca no vendor'


def test_quem_instala_recebe_instrucao_em_portugues(pacote):
    """O pacote é operado por quem não construiu o sistema."""
    for arquivo in ('LEIAME.txt', 'instalar.bat', 'iniciar.bat',
                    'instalar-servico.bat', 'atualizar.bat'):
        caminho = os.path.join(pacote, arquivo)
        assert os.path.exists(caminho), f'faltou {arquivo}'
        with open(caminho, encoding='utf-8') as fh:
            assert fh.read().strip(), f'{arquivo} está vazio'


def test_a_atualizacao_nao_toca_no_banco_nem_nas_fotos(pacote):
    """🔴 O maior risco da atualização é levar o dado do cliente junto.

    Se alguém acrescentar `instance` ou `uploads` à lista de cópia, este teste
    fica vermelho — e é a única coisa entre esse erro e um banco de produção
    sobrescrito.
    """
    import re

    with open(os.path.join(pacote, 'atualizar.bat'), encoding='utf-8') as fh:
        script = fh.read()

    # 🔴 Inspeciona as LISTAS do loop, não uma string literal com o nome da
    # pasta. A primeira versão deste teste procurava `rmdir "%ALVO%\instance"`,
    # que nunca apareceria: o script apaga através da variável `%%P`. Quem
    # acrescentasse `instance` à lista passaria batido — descobri isso tentando
    # sabotar a guarda e vendo o teste continuar verde.
    listas = re.findall(r'for %%P in \(([^)]*)\) do', script)
    assert listas, 'não achei o loop de cópia no atualizar.bat'

    for lista in listas:
        pastas = lista.split()
        assert pastas == ['app', 'templates', 'static', 'translations'], \
            f'a atualização mexeria em {pastas} — só o programa pode ser trocado'


def test_a_atualizacao_guarda_a_versao_anterior(pacote):
    """Sem o caminho de volta, uma versão ruim é um problema sem saída."""
    with open(os.path.join(pacote, 'atualizar.bat'), encoding='utf-8') as fh:
        script = fh.read()

    assert 'versao-anterior' in script


def test_o_instalador_nao_busca_nada_na_internet(pacote):
    """🔴 A exigência que o time de segurança vai cobrar.

    `--no-index` é o que impede o pip de procurar o PyPI mesmo havendo rede.
    Sem ele, a instalação "funciona" na máquina com internet e falha na que não
    tem — e o motivo não seria óbvio para quem estivesse instalando.
    """
    with open(os.path.join(pacote, 'instalar.bat'), encoding='utf-8') as fh:
        script = fh.read()

    assert '--no-index' in script, 'o instalador buscaria pacote na internet'
    assert '--find-links' in script, 'o instalador não aponta para as dependências locais'


def test_o_servico_nao_depende_da_senha_de_ninguem(pacote):
    """🔴 Tarefa amarrada a uma conta de pessoa morre na troca de senha.

    E a política de senha corporativa garante que a troca vai acontecer. O
    sintoma seria o sistema deixar de subir depois de um reinício, meses depois,
    sem relação aparente com nada.
    """
    with open(os.path.join(pacote, 'instalar-servico.bat'), encoding='utf-8') as fh:
        script = fh.read()

    assert '/RU "SYSTEM"' in script, 'o serviço rodaria como uma conta de pessoa'
    assert '/SC ONSTART' in script, 'o serviço não subiria sozinho no boot'


def test_o_comando_do_servico_cabe_no_limite_do_windows(pacote):
    """🔴 `schtasks /TR` recusa mais de 261 caracteres.

    Achado rodando o comando de verdade: com o caminho do python E o do run.py,
    uma instalação em pasta funda estoura o limite e o serviço simplesmente não
    registra — com uma mensagem sobre "tamanho de opção" que não diz a ninguém o
    que fazer. O /TR passou a apontar para um .bat só, que resolve o resto.

    O teste mede o pior caso realista: uma pasta de caminho bem fundo.
    """
    with open(os.path.join(pacote, 'instalar-servico.bat'), encoding='utf-8') as fh:
        script = fh.read()

    assert '/TR "%~dp0servico-run.bat"' in script, \
        'o /TR voltou a conter caminho completo — vai estourar o limite de 261'

    pasta_funda = os.path.join(
        r'C:\Users\nome.sobrenome\Documents', 'Sistemas Internos',
        'Controle de Coletores', 'Lastro-1.0.0')
    comando = os.path.join(pasta_funda, 'servico-run.bat')
    assert len(comando) < 261, f'{len(comando)} caracteres no pior caso'


def test_o_arquivo_que_a_tarefa_executa_existe(pacote):
    """O /TR aponta para ele; se não vier no pacote, o serviço falha no boot —
    e o sintoma seria o sistema simplesmente não subir, sem erro em lugar algum
    que alguém vá procurar."""
    caminho = os.path.join(pacote, 'servico-run.bat')

    assert os.path.exists(caminho)
    with open(caminho, encoding='utf-8') as fh:
        conteudo = fh.read()
    assert '%~dp0run.py' in conteudo, 'o .bat não chama o sistema'
    assert 'cd /d "%~dp0"' in conteudo,         'sem o cd, o banco e os uploads seriam procurados na pasta errada'


# ---------------------------------------------------------------------------
# COMPONENTES COMPILADOS: O PACOTE SÓ SERVE SE COBRIR A VERSÃO DO CLIENTE
# ---------------------------------------------------------------------------

def test_o_leiame_so_promete_versoes_que_o_pacote_cobre(pacote):
    """🔴 O pacote prometia "Python 3.10 ou mais novo" e trazia componentes de
    UMA versão só — a da máquina onde foi montado.

    Falhou na primeira instalação fora daqui, com "No matching distribution
    found for SQLAlchemy". SQLAlchemy, greenlet e MarkupSafe são compilados por
    versão do Python, e o pip não tinha o arquivo que servia.

    Este teste prende a promessa ao que o empacotador realmente baixa: as duas
    listas saem da mesma constante, e o LEIAME deixou de dizer "ou mais novo".
    """
    with open(os.path.join(pacote, 'LEIAME.txt'), encoding='utf-8') as fh:
        leiame = fh.read()

    for versao in empacotar.PYTHONS_SUPORTADOS:
        assert versao in leiame, f'o LEIAME não cita o Python {versao}'
    assert 'ou mais novo' not in leiame, \
        'o LEIAME voltou a prometer versões que o pacote não cobre'


def test_a_mensagem_de_erro_fala_da_versao_do_python(pacote):
    """🔴 A mensagem antiga mandava conferir a pasta `dependencias` — que
    estava lá. Mandar a pessoa procurar a coisa errada custa a tarde dela."""
    with open(os.path.join(pacote, 'instalar.bat'), encoding='utf-8') as fh:
        script = fh.read()

    assert 'python --version' in script.lower(), \
        'a mensagem de erro não mostra qual Python a máquina tem'
    for versao in empacotar.PYTHONS_SUPORTADOS:
        assert versao in script, f'a mensagem não cita o Python {versao}'


def test_as_versoes_suportadas_incluem_as_usadas_hoje():
    """Uma lista vazia ou curta passaria nos dois testes acima — eles comparam
    a promessa com a própria lista."""
    assert len(empacotar.PYTHONS_SUPORTADOS) >= 4
    assert '3.12' in empacotar.PYTHONS_SUPORTADOS   # a desta máquina
    assert empacotar.PLATAFORMA == 'win_amd64'      # Windows Server 64 bits
