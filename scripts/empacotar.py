"""
Monta o pacote de entrega — o ZIP que roda no Windows Server sem internet.

    python scripts/empacotar.py

Este script roda AQUI, na máquina de desenvolvimento, onde há internet. Ele
baixa as dependências uma vez e guarda dentro do pacote. Na máquina de destino
nada é baixado: `instalar.bat` instala a partir dos arquivos que vieram junto.

🔴 Por que o pacote não pode depender do PyPI:

O servidor fica numa rede corporativa onde o time de segurança tem opinião forte
sobre máquina de produção buscando pacote na internet — e está certo. Além
disso, "funciona porque baixou" é uma dependência invisível: no dia em que o
proxy mudar, a reinstalação falha e ninguém vai lembrar o porquê.

O que o pacote NÃO leva, de propósito:
  - o histórico do Git, que carregaria todo o desenvolvimento junto
  - o banco, os uploads e os backups de quem desenvolveu
  - a suíte de testes e as ferramentas de desenvolvimento
  - o Python em si: é o único pré-requisito da máquina, e `instalar.bat`
    confere a versão e explica o que fazer em vez de falhar com um traceback
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# O que vai para o pacote. Lista explícita, não "tudo menos": é mais fácil
# esquecer de EXCLUIR um arquivo novo e vazar algo do que esquecer de incluir —
# o que falta aparece no primeiro boot, o que vaza não aparece nunca.
PASTAS = ['app', 'templates', 'static', 'translations']
ARQUIVOS = ['run.py', 'requirements.txt', 'VERSAO']

# Nunca entram, mesmo dentro das pastas acima.
LIXO = {'__pycache__', '.pytest_cache', '.git', '.claude', 'venv',
        'instance', 'uploads', 'backups', 'logs', 'tests', 'dist', 'docs'}


def _versao():
    with open(os.path.join(RAIZ, 'VERSAO'), encoding='utf-8') as fh:
        return fh.read().strip()


def _limpar(destino):
    if os.path.exists(destino):
        shutil.rmtree(destino)
    os.makedirs(destino)


def _copiar(destino):
    def ignorar(_pasta, nomes):
        return [n for n in nomes if n in LIXO or n.endswith('.pyc')]

    for pasta in PASTAS:
        origem = os.path.join(RAIZ, pasta)
        if not os.path.isdir(origem):
            raise SystemExit(f'ERRO: a pasta {pasta} nao existe.')
        shutil.copytree(origem, os.path.join(destino, pasta), ignore=ignorar)

    for arquivo in ARQUIVOS:
        origem = os.path.join(RAIZ, arquivo)
        if not os.path.isfile(origem):
            raise SystemExit(f'ERRO: o arquivo {arquivo} nao existe.')
        shutil.copy2(origem, os.path.join(destino, arquivo))


# Versões de Python que o pacote aceita na máquina de destino. Precisa bater
# com o que o LEIAME promete — se o texto diz "3.10 ou mais novo", as cinco
# precisam estar aqui.
#
# 🔴 Isto existe porque o pacote QUEBRAVA fora da máquina onde foi montado.
# SQLAlchemy, greenlet e MarkupSafe trazem código compilado: o .whl é específico
# da versão do Python ("cp312" = CPython 3.12). Baixando só para a versão local,
# o instalador morria na máquina do cliente com "No matching distribution found"
# — e a mensagem mandava procurar a pasta `dependencias`, que estava lá.
PYTHONS_SUPORTADOS = ['3.10', '3.11', '3.12', '3.13', '3.14']
PLATAFORMA = 'win_amd64'


def _baixar_dependencias(destino):
    """Guarda os .whl dentro do pacote, para TODAS as versões suportadas.

    `--only-binary=:all:` recusa pacote que só vem como código-fonte: aquele
    precisaria de compilador na máquina de destino, que um Windows Server não
    tem. Melhor descobrir isso aqui do que no dia da instalação.

    Uma rodada por versão, tudo na mesma pasta. Na instalação o pip escolhe o
    arquivo que serve para o Python daquela máquina; os outros ficam ignorados.
    O custo é alguns MB a mais no pacote — barato perto de uma instalação que
    não acontece.
    """
    pasta = os.path.join(destino, 'dependencias')
    os.makedirs(pasta, exist_ok=True)
    reqs = os.path.join(RAIZ, 'requirements.txt')

    print('[1/3] Baixando as dependencias (so aqui, uma vez)...')
    faltaram = []
    for versao in PYTHONS_SUPORTADOS:
        comando = [sys.executable, '-m', 'pip', 'download', '-r', reqs,
                   '-d', pasta, '--only-binary=:all:',
                   '--platform', PLATAFORMA, '--python-version', versao]
        resultado = subprocess.run(comando, capture_output=True, text=True)
        if resultado.returncode != 0:
            faltaram.append(versao)
            print(f'      [AVISO] sem componentes para Python {versao}')
        else:
            print(f'      Python {versao}: ok')

    if faltaram == PYTHONS_SUPORTADOS:
        raise SystemExit('ERRO: nao consegui baixar as dependencias de nenhuma versao.')
    if faltaram:
        print(f'      ATENCAO: o pacote NAO instala em Python {", ".join(faltaram)}.')

    whls = [f for f in os.listdir(pasta) if f.endswith('.whl')]
    if not whls:
        raise SystemExit('ERRO: nenhum .whl foi baixado.')
    return whls


def _escrever_instalador(destino, versao):
    """O .bat que a pessoa da TI da MB vai executar.

    Em português, dizendo o que está fazendo e o que fazer quando falha. Um
    instalador que imprime traceback é um instalador que gera telefonema.
    """
    conteudo = f'''@echo off
setlocal
chcp 65001 >nul
title Lastro {versao} - Instalacao

echo ==========================================================
echo   Lastro {versao} - instalacao
echo ==========================================================
echo.

rem -- Python e o unico pre-requisito. Conferir a versao aqui evita um
rem -- traceback incompreensivel mais adiante.
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] O Python nao foi encontrado nesta maquina.
    echo.
    echo    Instale o Python 3.10 ou mais novo, marcando a opcao
    echo    "Add Python to PATH" durante a instalacao, e rode este
    echo    arquivo de novo.
    echo.
    pause
    exit /b 1
)

echo [1/3] Preparando o ambiente...
if not exist "%~dp0venv" (
    python -m venv "%~dp0venv"
    if errorlevel 1 (
        echo [ERRO] Nao consegui preparar o ambiente Python.
        echo    Verifique se ha espaco em disco e permissao nesta pasta.
        pause
        exit /b 1
    )
)

echo [2/3] Instalando os componentes ^(sem internet^)...
"%~dp0venv\\Scripts\\python.exe" -m pip install --quiet --no-index ^
    --find-links "%~dp0dependencias" -r "%~dp0requirements.txt"
if errorlevel 1 (
    rem 🔴 A mensagem antiga mandava procurar a pasta "dependencias" — que
    rem estava la. A causa real quase sempre e outra: a versao do Python desta
    rem maquina nao esta entre as que o pacote traz. Componentes como o
    rem SQLAlchemy sao compilados POR versao, e o erro do pip fala em
    rem "No matching distribution found" sem dizer o porque. Mandar a pessoa
    rem conferir a coisa errada custa a tarde dela.
    echo.
    echo [ERRO] Nao consegui instalar os componentes.
    echo.
    echo    O Python desta maquina e:
    python --version
    echo.
    echo    Este pacote traz componentes para Python VERSOES_SUPORTADAS.
    echo    Se a versao acima nao estiver nessa lista, instale uma delas
    echo    e rode este arquivo de novo.
    echo.
    echo    Se a versao estiver na lista, confira se a pasta "dependencias"
    echo    veio junto e esta completa.
    echo.
    pause
    exit /b 1
)

echo [3/3] Pronto.
echo.
echo ==========================================================
echo   Instalacao concluida.
echo.
echo   Para iniciar agora:        iniciar.bat
echo   Para iniciar com o Windows: instalar-servico.bat
echo ==========================================================
echo.
pause
'''
    # A lista de versões vem de um lugar só: se PYTHONS_SUPORTADOS mudar,
    # a mensagem muda junto. Texto duplicado é texto que envelhece torto.
    conteudo = conteudo.replace('VERSOES_SUPORTADAS',
                                ', '.join(PYTHONS_SUPORTADOS))
    _gravar(os.path.join(destino, 'instalar.bat'), conteudo)


def _escrever_iniciar(destino):
    conteudo = '''@echo off
setlocal
chcp 65001 >nul
title Lastro - em execucao

if not exist "%~dp0venv\\Scripts\\python.exe" (
    echo [ERRO] O sistema ainda nao foi instalado nesta pasta.
    echo    Rode primeiro o instalar.bat
    pause
    exit /b 1
)

cd /d "%~dp0"
"%~dp0venv\\Scripts\\python.exe" run.py
pause
'''
    _gravar(os.path.join(destino, 'iniciar.bat'), conteudo)


def _escrever_servico_run(destino):
    """O arquivo que a Tarefa Agendada executa.

    🔴 Existe por causa de um limite do Windows que eu só descobri testando:
    `schtasks /TR` recusa mais de 261 caracteres. Passar o caminho completo do
    python MAIS o do run.py estourava isso numa pasta de caminho fundo — que é
    onde as pessoas instalam, tipo "C:\\Users\\fulano\\Documents\\Sistemas\\...".

    O erro aparecia só na hora de registrar o serviço, com uma mensagem sobre
    tamanho de opção que não ajuda ninguém a entender o que fazer. Agora a
    tarefa aponta para ESTE arquivo — um caminho só — e ele descobre o resto
    sozinho com %~dp0.
    """
    conteudo = '''@echo off
rem Executado pela Tarefa Agendada "Lastro" quando o servidor liga.
rem Para iniciar o sistema na mao, use o iniciar.bat.
cd /d "%~dp0"
"%~dp0venv\\Scripts\\python.exe" "%~dp0run.py"
'''
    _gravar(os.path.join(destino, 'servico-run.bat'), conteudo)


def _escrever_servico(destino, versao):
    """Registra o sistema como Tarefa Agendada que roda no boot.

    🔴 Serviço do Windows "de verdade" (sc create) exige um wrapper como NSSM
    ou um executável que fale o protocolo de serviço — mais uma peça para
    instalar, e uma que o antivírus corporativo costuma barrar. Tarefa agendada
    no gatilho ONSTART faz o mesmo trabalho com ferramenta nativa, roda sem
    ninguém logado e sobrevive a reinício.

    Usa SYSTEM de propósito: tarefa amarrada a uma conta de pessoa para de rodar
    quando a senha dessa pessoa muda — e a política de senha da empresa garante
    que isso vai acontecer.
    """
    conteudo = f'''@echo off
setlocal
chcp 65001 >nul
title Lastro {versao} - iniciar junto com o Windows

net session >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Este arquivo precisa ser executado como administrador.
    echo    Clique com o botao direito e escolha "Executar como administrador".
    pause
    exit /b 1
)

echo [1/2] Liberando o acesso pela rede...
rem 🔴 Sem esta regra o sistema sobe, funciona na tela de quem instalou, e
rem NAO abre em mais nenhuma maquina do CD. O Windows aceita a conexao local
rem (que nao atravessa o firewall de entrada) e recusa a de qualquer outro
rem aparelho -- sem erro no log, sem nada quebrado: so nao abre. E a conclusao
rem de quem esta do outro lado e "o sistema nao funciona".
rem TCP de proposito: HTTP e TCP. Uma regra UDP com o mesmo numero nao serve.
netsh advfirewall firewall delete rule name="Lastro (HTTP)" >nul 2>&1
netsh advfirewall firewall add rule name="Lastro (HTTP)" dir=in action=allow ^
    protocol=TCP localport=5001 profile=any >nul
if errorlevel 1 (
    echo    [AVISO] Nao consegui liberar a porta 5001 no firewall.
    echo    O sistema vai funcionar nesta maquina, mas pode nao abrir nas outras.
    echo    Peca a quem cuida da rede para liberar a porta 5001 ^(TCP^).
) else (
    echo    Porta 5001 liberada.
)

echo [2/2] Registrando o Lastro para iniciar junto com o Windows...
schtasks /Create /TN "Lastro" /SC ONSTART /RU "SYSTEM" /RL HIGHEST /F ^
    /TR "%~dp0servico-run.bat"
if errorlevel 1 (
    echo [ERRO] Nao consegui registrar. Confira se a politica da maquina
    echo    permite criar tarefas agendadas.
    pause
    exit /b 1
)

echo.
echo Pronto. O Lastro vai subir sozinho toda vez que o servidor ligar.
echo Para iniciar agora sem reiniciar:  schtasks /Run /TN "Lastro"
echo Para desfazer:                     schtasks /Delete /TN "Lastro" /F
echo.
pause
'''
    _gravar(os.path.join(destino, 'instalar-servico.bat'), conteudo)


def _escrever_atualizar(destino, versao):
    """O caminho de uma correção futura, sem perder o que está na máquina.

    🔴 O perigo da atualização é o operador copiar a pasta nova por cima e levar
    junto `instance/` (o banco), `uploads/` e `backups/`. Este script separa o
    que é PROGRAMA do que é DADO: só o programa é trocado, e a versão anterior
    fica guardada ao lado para voltar atrás.
    """
    conteudo = f'''@echo off
setlocal
chcp 65001 >nul
title Lastro - atualizar para a versao {versao}

rem Rode este arquivo de DENTRO da pasta nova, apontando para a instalacao atual.
if "%~1"=="" (
    echo Uso:  atualizar.bat "C:\\caminho\\da\\instalacao\\atual"
    echo.
    echo    Este arquivo copia APENAS o programa para a instalacao indicada.
    echo    O banco de dados, as fotos e os backups de la nao sao tocados.
    pause
    exit /b 1
)

set "ALVO=%~1"
if not exist "%ALVO%\\run.py" (
    echo [ERRO] Nao encontrei uma instalacao do Lastro em:
    echo    %ALVO%
    pause
    exit /b 1
)

echo Guardando a versao atual para poder voltar atras...
set "GUARDA=%ALVO%\\versao-anterior"
if exist "%GUARDA%" rmdir /s /q "%GUARDA%"
mkdir "%GUARDA%"
for %%P in (app templates static translations) do (
    if exist "%ALVO%\\%%P" xcopy /e /i /q /y "%ALVO%\\%%P" "%GUARDA%\\%%P" >nul
)
if exist "%ALVO%\\run.py" copy /y "%ALVO%\\run.py" "%GUARDA%\\" >nul
if exist "%ALVO%\\VERSAO" copy /y "%ALVO%\\VERSAO" "%GUARDA%\\" >nul

echo Aplicando a versao {versao}...
for %%P in (app templates static translations) do (
    if exist "%ALVO%\\%%P" rmdir /s /q "%ALVO%\\%%P"
    xcopy /e /i /q /y "%~dp0%%P" "%ALVO%\\%%P" >nul
)
copy /y "%~dp0run.py" "%ALVO%\\" >nul
copy /y "%~dp0VERSAO" "%ALVO%\\" >nul
copy /y "%~dp0requirements.txt" "%ALVO%\\" >nul
xcopy /e /i /q /y "%~dp0dependencias" "%ALVO%\\dependencias" >nul

echo Atualizando os componentes...
"%ALVO%\\venv\\Scripts\\python.exe" -m pip install --quiet --no-index ^
    --find-links "%ALVO%\\dependencias" -r "%ALVO%\\requirements.txt"

echo.
echo ==========================================================
echo   Atualizado para a versao {versao}.
echo.
echo   Reinicie o sistema:  schtasks /End /TN "Lastro"
echo                        schtasks /Run /TN "Lastro"
echo.
echo   Confira a versao na tela "Saude do sistema".
echo   Deu errado? A versao anterior esta em:
echo     %GUARDA%
echo ==========================================================
echo.
pause
'''
    _gravar(os.path.join(destino, 'atualizar.bat'), conteudo)


def _escrever_leiame(destino, versao):
    # rf-string: o texto tem caminhos do Windows, e `C:\caminho` sem o `r`
    # vira sequência de escape inválida — hoje é só um aviso, amanhã é erro.
    conteudo = rf'''Lastro {versao}
Controle de coletores do CD

COMO INSTALAR
  1. Copie esta pasta inteira para o servidor.
  2. Execute  instalar.bat
  3. Execute  iniciar.bat
  4. O endereco de primeiro acesso aparece na janela. Abra no navegador
     e crie o administrador.

PARA SUBIR SOZINHO QUANDO O SERVIDOR LIGAR
  Execute  instalar-servico.bat  como administrador.

O QUE PRECISA TER NA MAQUINA
  Python VERSOES_SUPORTADAS (64 bits), com "Add Python to PATH" marcado.
  Outras versoes nao servem: os componentes que acompanham este pacote
  sao compilados para essas.
  Mais nada. Nenhum componente e baixado da internet: tudo que o sistema
  precisa esta na pasta "dependencias".

COMO ACESSAR DE OUTRAS MAQUINAS
  Pelo IP do servidor:     http://<ip-do-servidor>:5001
  Ou pelo nome dele:       http://<nome-do-servidor>:5001
  O nome funciona na rede local sem precisar configurar nada.

  Se nao abrir em outro aparelho, confira nesta ordem:
    1. o aparelho esta na mesma rede? (Wi-Fi da empresa, nao dados moveis)
    2. a porta esta liberada no firewall? O instalar-servico.bat faz isso.
    3. ha antivirus com firewall proprio? Precisa liberar nele tambem.
  A tela "Saude do sistema", dentro do proprio Lastro, verifica os itens 2 e 3.

TROCAR A PORTA
  Edite  instance\configuracao.txt  e reinicie o sistema.
  Ao trocar, libere a porta nova no firewall. A tela "Saude do sistema"
  mostra qual porta esta valendo.
  Este arquivo nao e substituido quando o sistema e atualizado.

ONDE FICAM AS COISAS
  instance\\lastro.db   o banco de dados
  backups\\               copias automaticas, feitas todo dia as 3h
  uploads\\               fotos
  logs\\                  registro de eventos
  Nenhuma dessas pastas e tocada por uma atualizacao.

SE ALGO PARECER ERRADO
  Entre no sistema e abra "Saude do sistema", no menu de administracao.
  Ela diz em portugues o que esta acontecendo e o que fazer.

ATUALIZAR DEPOIS
  Receba a pasta da versao nova e, de dentro dela, execute:
    atualizar.bat "C:\\caminho\\da\\instalacao\\atual"
  O banco, as fotos e os backups nao sao tocados. A versao anterior fica
  guardada em "versao-anterior", caso precise voltar.
'''
    conteudo = conteudo.replace('VERSOES_SUPORTADAS',
                                ', '.join(PYTHONS_SUPORTADOS))
    _gravar(os.path.join(destino, 'LEIAME.txt'), conteudo)


def _gravar(caminho, conteudo):
    # Windows: CRLF, porque o .bat e o .txt vao ser abertos no Bloco de Notas.
    with open(caminho, 'w', encoding='utf-8', newline='\r\n') as fh:
        fh.write(conteudo)


def _zipar(pasta, versao):
    destino = os.path.join(RAIZ, 'dist', f'Lastro-{versao}.zip')
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as zf:
        for raiz_atual, _dirs, arquivos in os.walk(pasta):
            for nome in arquivos:
                completo = os.path.join(raiz_atual, nome)
                relativo = os.path.relpath(completo, os.path.dirname(pasta))
                zf.write(completo, relativo)
    return destino


def main():
    p = argparse.ArgumentParser(description='Monta o pacote de entrega.')
    p.add_argument('--sem-zip', action='store_true',
                   help='monta a pasta mas nao gera o .zip')
    args = p.parse_args()

    versao = _versao()
    pasta = os.path.join(RAIZ, 'dist', f'Lastro-{versao}')
    print(f'Montando o Lastro {versao}...')

    _limpar(pasta)
    whls = _baixar_dependencias(pasta)
    print(f'      {len(whls)} componente(s) guardado(s) no pacote.')

    print('[2/3] Copiando o sistema...')
    _copiar(pasta)
    _escrever_instalador(pasta, versao)
    _escrever_iniciar(pasta)
    _escrever_servico_run(pasta)
    _escrever_servico(pasta, versao)
    _escrever_atualizar(pasta, versao)
    _escrever_leiame(pasta, versao)

    if args.sem_zip:
        print(f'[3/3] Pronto: {pasta}')
        return

    print('[3/3] Compactando...')
    caminho = _zipar(pasta, versao)
    mb = os.path.getsize(caminho) / (1024 ** 2)
    print(f'      Pronto: {caminho} ({mb:.1f} MB)')


if __name__ == '__main__':
    main()
