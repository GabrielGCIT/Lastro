"""A fronteira do produto: o que NÃO pode existir neste repositório.

O MBAssets é entregue em código-fonte (Python roda em texto), então o que ele
não contém é tão parte do produto quanto o que contém. Este teste varre cada
arquivo de texto do repositório atrás de nomes de módulos que ficaram de fora e
de marcas que não pertencem a ele.

Se um termo daqui aparecer de volta, a resposta não é tirá-lo desta lista: é
entender de onde veio. Os termos são montados por partes para que o próprio
teste não se denuncie.
"""
import os
import re
import sys

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

_IGNORAR_DIRS = {'.git', 'venv', '.venv', '__pycache__', '.pytest_cache',
                 'instance', 'uploads', 'vendor'}
_EXTENSOES = {'.py', '.html', '.json', '.txt', '.ini', '.md', '.css', '.js',
              '.cfg', '.toml', '.bat', '.ps1', '.sh', '.gitignore'}


def _j(*partes):
    return ''.join(partes)


# Sem distinção de maiúsculas: a marca de outro produto, em qualquer grafia.
PROIBIDOS_IGNORA_CAIXA = [
    _j('ko', 'da'),
]

# Com distinção de maiúsculas: nomes de código dos módulos que saíram. São
# identificadores, não palavras — "Ativo" (status, ou o coletor chamado de ativo
# na tela) e "Fornecedor" (de quem o coletor não voltou) são português legítimo.
# Por isso essas duas só contam no FORMATO de código: classe instanciada,
# acessada ou importada, e a rota do módulo.
PROIBIDOS = [
    _j(r'\bAti', r'vo\s*[(.]'), _j(r'import\b.*\bAti', r'vo\b'), _j('/ati', r'vos\b'),
    _j(r'\bFornece', r'dor\s*[(.]'), _j(r'import\b.*\bFornece', r'dor\b'),
    _j('Movimentacao', 'Ativo'), _j('Manutencao', 'Ativo'),
    _j('Licenca', 'Software'), _j('Atribuicao', 'Licenca'), _j('Descarte', 'Ativo'),
    _j('CicloInventario', 'Ativo'), _j('Categoria', 'Ativo'),
    _j('Nota', 'Fiscal'), _j('Filial', 'Fiscal'), _j('Natureza', 'OperacaoFiscal'),
    _j('Pendencia', 'Documental'),
    _j('Lote', 'Manutencao'), _j(r'\bItem', r'Lote\b'),
    _j('Tenant', 'Session'), _j('Empresa', 'Catalogo'), _j('Empresa', 'Modulo'),
    _j('Empresa', 'Selo'), _j('Plataforma', 'Usuario'),
    _j('app.', 'tenancy'), _j('app.', 'catalogo'), _j('provision', 'amento'),
    _j('py', 'odbc'), _j('ms', 'sql'), _j('open', 'pyxl'), _j('dan', 'fe'),
    _j('quota_', 'upload'),
    # CNPJ formatado: nenhum documento fiscal real viaja neste repositório.
    r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b',
]


def _arquivos():
    for pasta, dirs, arquivos in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in _IGNORAR_DIRS]
        for nome in arquivos:
            if os.path.splitext(nome)[1] in _EXTENSOES or nome in _EXTENSOES:
                caminho = os.path.join(pasta, nome)
                if os.path.abspath(caminho) != os.path.abspath(__file__):
                    yield caminho


def _achados():
    padroes = ([re.compile(p, re.IGNORECASE) for p in PROIBIDOS_IGNORA_CAIXA]
               + [re.compile(p) for p in PROIBIDOS])
    achados = []
    for caminho in _arquivos():
        with open(caminho, encoding='utf-8', errors='replace') as fh:
            for n, linha in enumerate(fh, 1):
                for p in padroes:
                    if p.search(linha):
                        rel = os.path.relpath(caminho, RAIZ)
                        achados.append(f'{rel}:{n}: [{p.pattern}] {linha.strip()[:100]}')
    return achados


def test_repositorio_nao_contem_o_que_ficou_de_fora():
    achados = _achados()
    assert not achados, f'{len(achados)} ocorrências:\n' + '\n'.join(achados[:60])


def test_a_varredura_enxerga_um_termo_proibido(tmp_path, monkeypatch):
    """Prova que o teste morde: um arquivo plantado com termo proibido é achado."""
    plantado = tmp_path / 'plantado.py'
    plantado.write_text('x = "' + _j('Ko', 'da') + '"\n', encoding='utf-8')
    monkeypatch.setattr(sys.modules[__name__], 'RAIZ', str(tmp_path))
    assert any('plantado.py' in a for a in _achados())
