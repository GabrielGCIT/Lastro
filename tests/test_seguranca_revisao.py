"""
Revisão a posteriori do pacote Segurança — regressões dos escapes encontrados
na revisão:

  3. dashboard.restaurar_grupo(): o grupo restaurado nascia órfão (empresa_id
     NULL) — invisível na listagem escopada até o backfill do boot (família A).
  4. movimentacao: lookup de coletor por patrimônio (não-único) usava .first()
     global + guard pós-lookup — um homônimo de outro tenant virava falso
     "não encontrado" para o dono legítimo (família E).
  5. dashboard.index: o alerta de contas criadas em campo somava TODOS os
     tenants (família C).

Usa a fixture compartilhada duas_empresas (T6): MB=A, Acme=B.
"""
from app import db
from app.models import Grupo, GrupoDeletado, Coletor


# ---------------------------------------------------------------------------
# 3. Restauração de grupo padrão carimba o tenant
# ---------------------------------------------------------------------------

def test_restaurar_grupo_carimba_empresa_da_sessao(app, duas_empresas, cliente_logado):
    du = duas_empresas
    # Simula a exclusão via UI: grupo padrão some e fica registrado em GrupoDeletado.
    # T4a — o registro de deleção é por empresa (empresa_id carimbado).
    lider = Grupo.query.filter_by(nome='LIDER', empresa_id=du.mb.id).first()
    assert lider is not None and not lider.usuarios
    db.session.delete(lider)
    db.session.add(GrupoDeletado(nome='LIDER', empresa_id=du.mb.id, deletado_por='teste'))
    db.session.commit()

    resp = cliente_logado(du.userA).post('/grupos/LIDER/restaurar')
    assert resp.status_code == 302

    restaurado = Grupo.query.filter_by(nome='LIDER', empresa_id=du.mb.id).first()
    assert restaurado is not None
    # O ponto da regressão: nascia órfão (None) e sumia da listagem escopada.
    assert restaurado.empresa_id == du.mb.id
    # T4a — o registro de deleção (por empresa) foi consumido na restauração.
    assert GrupoDeletado.query.filter_by(empresa_id=du.mb.id, nome='LIDER').first() is None


# ---------------------------------------------------------------------------
# 4. Homônimo de patrimônio entre tenants resolve o coletor do próprio tenant
# ---------------------------------------------------------------------------

def test_busca_patrimonio_homonimo_resolve_o_do_proprio_tenant(app, duas_empresas, cliente_logado):
    du = duas_empresas
    # O da Acme entra primeiro (id menor): um .first() global o devolveria, e o
    # guard pós-lookup viraria falso "não encontrado" para o usuário da MB.
    colB = Coletor(serial_number='SER-B-DUP', numero_patrimonio='DUP-1',
                   status='Disponível', localidade_id=du.locB.id)
    db.session.add(colB)
    db.session.flush()
    colA = Coletor(serial_number='SER-A-DUP', numero_patrimonio='DUP-1',
                   status='Disponível', localidade_id=du.locA.id)
    db.session.add(colA)
    db.session.commit()

    data = (cliente_logado(du.userA)
            .get('/api/coletor/buscar?q=DUP-1&modo=patrimonio').get_json())
    assert data['encontrado'] is True
    assert data['id'] == colA.id


def test_busca_patrimonio_de_outro_tenant_segue_inexistente(app, duas_empresas, cliente_logado):
    du = duas_empresas
    colB = Coletor(serial_number='SER-B-SOLO', numero_patrimonio='SOLO-B',
                   status='Disponível', localidade_id=du.locB.id)
    db.session.add(colB)
    db.session.commit()

    data = (cliente_logado(du.userA)
            .get('/api/coletor/buscar?q=SOLO-B&modo=patrimonio').get_json())
    assert data['encontrado'] is False


# ---------------------------------------------------------------------------
# 5. Alerta de contas criadas em campo conta só o próprio tenant
# ---------------------------------------------------------------------------

def test_alerta_contas_campo_nao_soma_outro_tenant(app, duas_empresas, cliente_logado):
    du = duas_empresas
    du.userB.criado_em_campo = True
    db.session.commit()
    html = cliente_logado(du.userA).get('/').get_data(as_text=True)
    # O alerta só renderiza com usuarios_campo > 0 — para a MB deve ser 0.
    assert 'conta(s)' not in html
