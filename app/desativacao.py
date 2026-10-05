"""
Desativar e excluir coletor — a fatia B6.

A regra é de negócio e mora num lugar só: **item com histórico não se exclui**.

As três decisões:

    DESATIVAR   sempre disponível, reversível, **motivo obrigatório**. O item
                sai das telas de operação e não pode mais ser movimentado; o
                histórico continua inteiro e cobrável.
    EXCLUIR     só para o que NUNCA foi usado — cadastro errado, duplicado.
                Item com movimentação, checklist ou reativação não se apaga: a
                trilha de auditoria não pode ter buraco.
    SENHA       só na exclusão. Desativar é reversível e já pede motivo; exigir
                senha nas duas travaria quem desativa dez itens numa tarde, e
                fricção que não protege nada só ensina a clicar rápido.

🔴 O REGISTRO DA EXCLUSÃO É A ÚNICA COISA QUE SOBREVIVE AO APAGAMENTO. Por isso
`registrar_exclusao` NÃO usa o `registrar_log` comum, que engole falhas de
propósito para nunca interromper o negócio: aqui, log que falha em silêncio
significa dado apagado sem rastro nenhum. Ver a nota na função.
"""
from datetime import datetime

from werkzeug.security import check_password_hash

from app import db
from app.models import (ChecklistEntrega, Coletor, ItemComplementar, LogAuditoria,
                        Movimentacao, ReativacaoIdentificacao, Usuario)


class ExclusaoRecusada(Exception):
    """Levantada quando algo impede o apagamento. A mensagem vai para a tela."""


# ---------------------------------------------------------------------------
# O QUE CONTA COMO HISTÓRICO
# ---------------------------------------------------------------------------
# Cada entrada é (modelo, coluna, rótulo). O rótulo entra na mensagem que a tela
# mostra: "não dá para excluir porque existem 3 movimentações" é acionável;
# "violação de integridade referencial" manda o operador procurar a TI.
#
# 🔴 A lista NÃO inclui o pareamento de peça (`ItemComplementar.coletor_id`).
# Ele é vínculo ATUAL, não histórico: desfazê-lo é um clique na própria tela do
# coletor (B4), e tratá-lo como histórico impediria de apagar um cadastro errado
# só porque alguém encostou um headset nele.

_HISTORICO_COLETOR = [
    (Movimentacao, 'coletor_id', 'movimentação'),
    (ChecklistEntrega, 'coletor_id', 'checklist de entrega'),
    (ReativacaoIdentificacao, 'coletor_id', 'reativação de identificação'),
]


def _tabela_de(obj):
    if isinstance(obj, Coletor):
        return _HISTORICO_COLETOR
    raise TypeError(f'{type(obj).__name__} não tem regra de desativação')


def historico_de(obj):
    """O que impede o apagamento, como lista de frases prontas para a tela.

    Vazia significa "nunca foi usado" — o único caso em que excluir é permitido.

    Uma consulta de contagem por relação, e não `len(obj.movimentacoes)`: a
    segunda forma carrega as linhas inteiras só para contá-las, e esta tela abre
    em cima de um item que pode ter centenas delas.
    """
    achados = []
    for modelo, coluna, rotulo in _tabela_de(obj):
        n = (db.session.query(db.func.count(modelo.id))
             .filter(getattr(modelo, coluna) == obj.id).scalar()) or 0
        if n:
            achados.append(f'{n} {rotulo}' + ('' if n == 1 else 's'))
    return achados


# 🔴 Todo banco tem teto de parâmetros por comando (o SQLite antigo parava em
# 999), e um `IN (?, ?, ...)` com a lista inteira de coletores estoura num
# inventário grande — em produção, na tela mais usada do módulo. Fatiar custa
# nada e tira o teto da conta.
_LOTE_PARAMETROS = 500


def _em_lotes(ids, tamanho=_LOTE_PARAMETROS):
    """Fatia a lista de ids para caber no limite de parâmetros do banco."""
    for inicio in range(0, len(ids), tamanho):
        yield ids[inicio:inicio + tamanho]


def ids_com_historico(classe, ids):
    """Dos `ids` dados, quais já foram usados — em CONSULTAS POR RELAÇÃO.

    🔴 A listagem não pode chamar `historico_de` por linha: seriam 4 consultas
    por coletor, e o inventário tem centenas. Aqui é uma consulta por relação,
    com `IN`, e o cruzamento acontece em memória — o mesmo remédio que a H4
    aplicou nas pendências depois de o N+1 se esconder atrás de uma property.
    """
    ids = list(ids or [])
    if not ids:
        return set()

    if classe is not Coletor:
        raise TypeError(f'{classe.__name__} não tem regra de desativação')
    campos = [getattr(modelo, coluna) for modelo, coluna, _r in _HISTORICO_COLETOR]

    usados = set()
    for campo in campos:
        for lote in _em_lotes(ids):
            usados |= {linha[0] for linha in
                       db.session.query(campo).filter(campo.in_(lote)).distinct().all()}
    return usados


def pode_excluir(obj):
    """Atalho de leitura para a tela decidir se mostra o botão."""
    return not historico_de(obj)


# ---------------------------------------------------------------------------
# AS AÇÕES
# ---------------------------------------------------------------------------

def desativar(obj, motivo, usuario):
    """Tira o item de operação SEM tocar no histórico. Reversível.

    O motivo é obrigatório porque desativar sem justificativa produz um item que
    sumiu e ninguém sabe por quê — e três meses depois alguém recadastra o mesmo
    equipamento achando que ele se perdeu.
    """
    motivo = (motivo or '').strip()
    if not motivo:
        raise ExclusaoRecusada('Informe o motivo da desativação.')
    obj.desativado_em = datetime.now()
    obj.desativado_por_id = usuario.id if usuario else None
    obj.desativado_motivo = motivo[:200]
    db.session.commit()


def reativar(obj):
    """Devolve o item à operação. Os três campos voltam a nulo juntos.

    Limpar tudo é deliberado: um `desativado_motivo` sobrevivente descreveria
    uma desativação que não existe mais, e a próxima leitura acreditaria nele.
    """
    obj.desativado_em = None
    obj.desativado_por_id = None
    obj.desativado_motivo = None
    db.session.commit()


def senha_confere(usuario, senha):
    """Confirma que quem está no teclado é o dono da sessão (modo elevado).

    A senha não abre nada novo — o usuário já tem a permissão. Ela responde
    outra pergunta: *é você mesmo?*, para o caso da estação destravada que
    alguém encostou. Por isso vale a senha de login, e não uma senha à parte.
    """
    if not usuario or not senha:
        return False
    return check_password_hash(usuario.senha_hash, senha)


def registrar_exclusao(obj, usuario, empresa_id=None):
    """Grava a lápide do item na trilha de auditoria, ANTES de ele sumir.

    🔴 NÃO passa pelo `registrar_log`. Aquele helper engole exceções por decisão
    consciente — log não pode derrubar o negócio. Aqui a relação se inverte: o
    log É o negócio. Apagar um coletor e perder o registro do apagamento deixa a
    trilha com um buraco exatamente onde ela mais importa, e ninguém descobre.

    Fica na MESMA transação do delete: ou os dois acontecem, ou nenhum.
    """
    descricao = descrever(obj)
    db.session.add(LogAuditoria(
        empresa_id=empresa_id,
        usuario=(usuario.nome if usuario else 'sistema'),
        # O RE identifica o autor sem ambiguidade — dois usuários podem ter o
        # mesmo nome, e a lápide precisa apontar para uma pessoa só.
        user_re=(usuario.re if usuario else None),
        acao='EXCLUSAO_DEFINITIVA',
        detalhe=descricao))


def descrever(obj):
    """O que o item ERA, em uma linha — o que sobra dele depois do delete."""
    return (f'Coletor #{obj.id} excluído definitivamente: '
            f'patrimônio {obj.numero_patrimonio or "—"}, '
            f'serial {obj.serial_number}, modelo {obj.modelo or "—"}, '
            f'status {obj.status}.')


def excluir(obj, usuario, senha, empresa_id=None):
    """Apaga de vez. Três travas, todas obrigatórias.

    1. senha do próprio usuário confere;
    2. o item não tem histórico nenhum;
    3. a lápide entra na trilha, na mesma transação.

    Sem histórico o delete é seguro: nada aponta para ele. O pareamento de peça,
    que é o único vínculo possível, é desfeito aqui — é vínculo atual, e a peça
    continua existindo, só que livre.
    """
    if not senha_confere(usuario, senha):
        raise ExclusaoRecusada('Senha incorreta. O item não foi excluído.')

    impedimentos = historico_de(obj)
    if impedimentos:
        raise ExclusaoRecusada(
            'Este item tem histórico (' + ', '.join(impedimentos) +
            ') e não pode ser excluído. Desative-o: ele sai das telas de '
            'operação e o histórico continua inteiro.')

    if isinstance(obj, Coletor):
        for peca in ItemComplementar.query.filter_by(coletor_id=obj.id).all():
            peca.coletor_id = None

    registrar_exclusao(obj, usuario, empresa_id)
    db.session.delete(obj)
    db.session.commit()


def usuario_da_sessao():
    """O Usuario logado, ou None. Quem chama decide o que fazer com o None."""
    from flask import session
    uid = session.get('user_id')
    return db.session.get(Usuario, uid) if uid else None
