"""
Revisão a posteriori do pacote Segurança — regressões dos escapes encontrados
na revisão:

  4. movimentacao: lookup de coletor por patrimônio (não-único) usava .first()
     global + guard pós-lookup — um homônimo de outro tenant virava falso
     "não encontrado" para o dono legítimo (família E).

Usa a fixture compartilhada duas_empresas (T6): MB=A, Acme=B.
"""
from app import db
from app.models import Coletor


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
