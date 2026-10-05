"""
Fundações do isolamento por empresa.

Cobre:
  - perfis semeados POR empresa (cada empresa tem seu conjunto);
  - criterio_empresa fail-closed: sessão quebrada zera (não pega o órfão);
  - boot duplo idempotente: seeds convergem, não duplicam.
"""
from app import db


def test_seed_grupos_por_empresa(app, duas_empresas):
    """seed_grupos_e_permissoes dá a cada empresa seu próprio conjunto de perfis."""
    from app.models import Grupo
    from app.startup import seed_grupos_e_permissoes, GRUPOS_DEFAULTS
    du = duas_empresas

    # A Acme nasce sem perfis (o fixture só cria a empresa).
    assert Grupo.query.filter_by(empresa_id=du.acme.id).count() == 0

    seed_grupos_e_permissoes()

    for nome in GRUPOS_DEFAULTS:
        assert Grupo.query.filter_by(empresa_id=du.mb.id,   nome=nome).first() is not None
        assert Grupo.query.filter_by(empresa_id=du.acme.id, nome=nome).first() is not None

    # O TI da MB e o da Acme são registros DISTINTOS (isolamento real).
    ti_mb   = Grupo.query.filter_by(empresa_id=du.mb.id,   nome='TI').first()
    ti_acme = Grupo.query.filter_by(empresa_id=du.acme.id, nome='TI').first()
    assert ti_mb.id != ti_acme.id


def test_criterio_empresa_sessao_quebrada_zera(app, duas_empresas):
    """Sessão sem empresa_id devolve db.false() — nunca IS NULL."""
    from flask import session
    from app.models import Colaborador
    from app.helpers import criterio_empresa
    du = duas_empresas

    orfao = Colaborador(re='C-ORFAO', nome='Colab Orfao', empresa_id=None)
    db.session.add(orfao)
    db.session.commit()

    with app.test_request_context():
        # sessão SEM empresa_id (estado inválido) — o antigo `== None` pegaria o
        # órfão via IS NULL; criterio_empresa zera.
        res = Colaborador.query.filter(criterio_empresa(Colaborador.empresa_id)).all()
        assert res == []

    # Com empresa: vê o da própria empresa, não o órfão nem o de outra.
    with app.test_request_context():
        session['empresa_id'] = du.acme.id
        res = Colaborador.query.filter(criterio_empresa(Colaborador.empresa_id)).all()
        assert du.colB in res
        assert du.colA not in res
        assert orfao not in res


def test_boot_duplo_idempotente(app, duas_empresas):
    """Rodar o startup de novo não duplica perfis nem quebra."""
    from app.models import Grupo
    from app.startup import executar_startup, seed_grupos_e_permissoes
    seed_grupos_e_permissoes()                     # semeia a Acme também
    antes = Grupo.query.count()

    executar_startup()                             # segundo boot completo

    assert Grupo.query.count() == antes
