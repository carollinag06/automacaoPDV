"""REI-02: pausa explícita do revisor; não executar fechamento nem kill."""
import pytest


@pytest.mark.blocked(reason=(
    "REI-02: Pendente de Decisão / Ambiguidade de Roteiro. Confirmar se fechar "
    "a tela significa fechamento normal ou interrupção abrupta. Não executar kill."
))
def test_rei02_pending_closure_decision():
    """Seção 17: produto 3, fechar/reabrir, preservar pedido; fluxo não aprovado."""
    pytest.fail("REI-02 pendente de decisão chegou indevidamente à execução")
