"""
T4a — Fundações + dívidas técnicas (sem UI nova).

Cobre as frentes da fatia:
  1/2. GrupoDeletado por empresa (schema surrogate + unicidade composta) e
       seed de grupos POR empresa (cada tenant tem seu conjunto).
  3.   Deleção/restauração de grupo isoladas por empresa (B não afeta A).
  4.   criterio_empresa fail-closed: sessão quebrada zera (não pega o órfão).
  Boot duplo idempotente: seeds convergem, não duplicam.
"""
from app import db


# ===========================================================================
# 1/2 — GrupoDeletado por empresa + seed de grupos por empresa
# ===========================================================================

def test_grupo_deletado_schema_por_empresa(app, duas_empresas):
    """PK surrogate + (empresa_id, nome) único: o mesmo nome coexiste em tenants."""
    from app.models import GrupoDeletado
    du = duas_empresas
    r1 = GrupoDeletado(nome='LIDER', empresa_id=du.acme.id, deletado_por='B')
    db.session.add(r1)
    db.session.commit()
    assert r1.id is not None                       # id surrogate, não mais o nome

    # Mesmo nome, outra empresa — a unicidade é composta, então coexiste.
    r2 = GrupoDeletado(nome='LIDER', empresa_id=du.mb.id, deletado_por='A')
    db.session.add(r2)
    db.session.commit()
    assert r2.id is not None and r2.id != r1.id


def test_seed_grupos_por_empresa(app, duas_empresas):
    """seed_grupos_e_permissoes dá a cada empresa seu próprio conjunto de grupos."""
    from app.models import Grupo
    from app.startup import seed_grupos_e_permissoes, GRUPOS_DEFAULTS
    du = duas_empresas

    # A Acme nasce sem grupos (o fixture só cria a empresa).
    assert Grupo.query.filter_by(empresa_id=du.acme.id).count() == 0

    seed_grupos_e_permissoes()

    for nome in GRUPOS_DEFAULTS:
        assert Grupo.query.filter_by(empresa_id=du.mb.id,   nome=nome).first() is not None
        assert Grupo.query.filter_by(empresa_id=du.acme.id, nome=nome).first() is not None

    # O TI_MASTER da MB e o da Acme são registros DISTINTOS (isolamento real).
    ti_mb   = Grupo.query.filter_by(empresa_id=du.mb.id,   nome='TI_MASTER').first()
    ti_acme = Grupo.query.filter_by(empresa_id=du.acme.id, nome='TI_MASTER').first()
    assert ti_mb.id != ti_acme.id


# ===========================================================================
# 3 — Deleção/restauração de grupo isoladas por empresa
# ===========================================================================

def test_deletar_grupo_em_B_nao_afeta_seed_de_A(app, duas_empresas):
    """A regressão da PK global: B deletando LIDER matava o seed do LIDER de A."""
    from app.models import Grupo, GrupoDeletado
    from app.startup import seed_grupos_e_permissoes
    du = duas_empresas
    seed_grupos_e_permissoes()                     # ambas com o conjunto completo

    # Acme "exclui" o LIDER (registra a deleção + remove o grupo).
    lider_acme = Grupo.query.filter_by(empresa_id=du.acme.id, nome='LIDER').first()
    db.session.delete(lider_acme)
    db.session.add(GrupoDeletado(empresa_id=du.acme.id, nome='LIDER', deletado_por='B'))
    db.session.commit()

    seed_grupos_e_permissoes()                     # "reboot" do seed

    # A (MB) mantém o LIDER; B (Acme) respeita a deleção e não o recria.
    assert Grupo.query.filter_by(empresa_id=du.mb.id,   nome='LIDER').first() is not None
    assert Grupo.query.filter_by(empresa_id=du.acme.id, nome='LIDER').first() is None


def test_restaurar_nao_alcanca_deletado_de_outro_tenant(app, duas_empresas, cliente_logado):
    """Restaurar da MB não toca o registro de deleção da Acme (escopo por empresa)."""
    from app.models import Grupo, GrupoDeletado
    from app.startup import seed_grupos_e_permissoes
    du = duas_empresas
    seed_grupos_e_permissoes()

    lider_acme = Grupo.query.filter_by(empresa_id=du.acme.id, nome='LIDER').first()
    db.session.delete(lider_acme)
    db.session.add(GrupoDeletado(empresa_id=du.acme.id, nome='LIDER', deletado_por='B'))
    db.session.commit()

    # userA (MB) tenta restaurar 'LIDER'. Para a MB não há registro de deleção,
    # então a rota não recria nada nem limpa o registro da Acme.
    cliente_logado(du.userA).post('/grupos/LIDER/restaurar')

    assert GrupoDeletado.query.filter_by(empresa_id=du.acme.id, nome='LIDER').first() is not None
    assert Grupo.query.filter_by(empresa_id=du.mb.id, nome='LIDER').count() == 1


# ===========================================================================
# 4 — criterio_empresa fail-closed (sessão quebrada zera, não pega o órfão)
# ===========================================================================

def test_criterio_empresa_sessao_quebrada_zera(app, duas_empresas):
    """Não-owner sem empresa_id na sessão devolve db.false() — nunca IS NULL."""
    from flask import session
    from app.models import Colaborador
    from app.helpers import criterio_empresa
    du = duas_empresas

    orfao = Colaborador(re='C-ORFAO', nome='Colab Orfao', empresa_id=None)
    db.session.add(orfao)
    db.session.commit()

    with app.test_request_context():
        session['is_owner'] = False
        # sessão SEM empresa_id (estado inválido) — o antigo `== None` pegaria o
        # órfão via IS NULL; criterio_empresa zera.
        res = Colaborador.query.filter(criterio_empresa(Colaborador.empresa_id)).all()
        assert res == []

    # Contraprova do Owner: enxerga inclusive o órfão.
    with app.test_request_context():
        session['is_owner'] = True
        res = Colaborador.query.filter(criterio_empresa(Colaborador.empresa_id)).all()
        assert orfao in res

    # Não-owner com empresa: vê o da própria empresa, não o órfão nem o de outra.
    with app.test_request_context():
        session['is_owner']   = False
        session['empresa_id'] = du.acme.id
        res = Colaborador.query.filter(criterio_empresa(Colaborador.empresa_id)).all()
        assert du.colB in res
        assert orfao not in res


# ===========================================================================
# Boot duplo idempotente (seeds convergem, não duplicam)
# ===========================================================================

def test_boot_duplo_idempotente(app, duas_empresas):
    """Rodar o startup de novo não duplica grupos nem quebra."""
    from app.models import Grupo
    from app.startup import executar_startup, seed_grupos_e_permissoes
    du = duas_empresas
    seed_grupos_e_permissoes()                     # semeia a Acme também
    antes = Grupo.query.count()

    executar_startup()                             # segundo boot completo

    assert Grupo.query.count() == antes
