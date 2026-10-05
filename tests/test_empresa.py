"""
Testes do T1 — Fundação multi-empresa.

Cobrem: seed idempotente da empresa da instalação, backfill de empresa_id nas
tabelas de vínculo direto e as constraints únicas compostas (empresa_id + re /
nome / sigla+pais) — que continuam valendo porque o empresa_id fica.
"""
import pytest
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Empresa, Usuario, Colaborador, Grupo, Localidade, Pais,
)
from app.startup import seed_empresa, _TABELAS_EMPRESA


def _empresa_mb():
    return Empresa.query.filter_by(nome='Martin Brower').first()


def _nova_empresa(nome='Acme Logística'):
    e = Empresa(nome=nome)
    db.session.add(e)
    db.session.commit()
    return e


# ---------------------------------------------------------------------------
# Seed e backfill
# ---------------------------------------------------------------------------

def test_boot_cria_a_empresa(app):
    mb = _empresa_mb()
    assert mb is not None
    assert mb.ativa is True


def test_backfill_vincula_registros_existentes(app):
    mb = _empresa_mb()
    admin = Usuario.query.filter_by(re='admin').first()
    assert admin.empresa_id == mb.id
    assert all(g.empresa_id == mb.id for g in Grupo.query.all())


def test_seed_empresa_idempotente(app):
    seed_empresa()
    seed_empresa()
    assert Empresa.query.count() == 1


def test_backfill_autocicatriza_linhas_novas(app):
    """Linha criada sem empresa_id (rotas pré-T2) é capturada no boot seguinte."""
    db.session.add(Colaborador(re='90001', nome='Órfão Temporário'))
    db.session.commit()
    seed_empresa()
    colab = Colaborador.query.filter_by(re='90001').first()
    assert colab.empresa_id == _empresa_mb().id


def test_backfill_cobre_todas_as_tabelas_de_vinculo(app):
    """Nenhuma linha das tabelas de tenant fica órfã após o boot."""
    from sqlalchemy import text
    for tabela in _TABELAS_EMPRESA:
        orfaos = db.session.execute(text(
            f'SELECT COUNT(*) FROM {tabela} WHERE empresa_id IS NULL'
        )).scalar()
        assert orfaos == 0, f'{tabela} tem {orfaos} linha(s) sem empresa_id'


# ---------------------------------------------------------------------------
# Constraints compostas
# ---------------------------------------------------------------------------

def test_re_usuario_duplicado_na_mesma_empresa_bloqueado(app):
    mb = _empresa_mb()
    db.session.add(Usuario(nome='Clone do Admin', re='admin', empresa_id=mb.id,
                           senha_hash=generate_password_hash('x', method='scrypt')))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_re_usuario_igual_em_empresas_diferentes_permitido(app):
    outra = _nova_empresa()
    db.session.add(Usuario(nome='Admin da Acme', re='admin', empresa_id=outra.id,
                           senha_hash=generate_password_hash('x', method='scrypt')))
    db.session.commit()
    assert Usuario.query.filter_by(re='admin').count() == 2


def test_re_colaborador_composto_por_empresa(app):
    mb    = _empresa_mb()
    outra = _nova_empresa()
    db.session.add(Colaborador(re='12345', nome='Colab MB', empresa_id=mb.id))
    db.session.add(Colaborador(re='12345', nome='Colab Acme', empresa_id=outra.id))
    db.session.commit()

    db.session.add(Colaborador(re='12345', nome='Duplicado MB', empresa_id=mb.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_nome_grupo_igual_em_empresas_diferentes_permitido(app):
    outra = _nova_empresa()
    db.session.add(Grupo(nome='TI_MASTER', empresa_id=outra.id,
                         descricao='TI_MASTER do outro tenant'))
    db.session.commit()
    assert Grupo.query.filter_by(nome='TI_MASTER').count() == 2


def test_sigla_localidade_composta_por_empresa(app):
    mb    = _empresa_mb()
    outra = _nova_empresa()
    br    = Pais.query.filter_by(sigla='BR').first()
    db.session.add(Localidade(sigla='GR', nome='Guarulhos MB',
                              pais_id=br.id, empresa_id=mb.id))
    db.session.add(Localidade(sigla='GR', nome='Guarulhos Acme',
                              pais_id=br.id, empresa_id=outra.id))
    db.session.commit()

    db.session.add(Localidade(sigla='GR', nome='Guarulhos duplicado',
                              pais_id=br.id, empresa_id=mb.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_nome_empresa_unico(app):
    db.session.add(Empresa(nome='Martin Brower'))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
