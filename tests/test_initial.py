from __future__ import annotations

import pytest


@pytest.mark.automated
@pytest.mark.initial
def test_pause_cash_register(pdv):
    pdv.pause()
    assert pdv.has_window_class("TFrmPDVPausa"), \
        "O formulario top-level TFrmPDVPausa nao foi localizado"


@pytest.mark.automated
@pytest.mark.initial
def test_open_help(pdv):
    pdv.help()
    assert pdv.has_window_class("TFrmPDVAjuda"), \
        "O formulario top-level TFrmPDVAjuda nao foi localizado"
    pdv.escape()


@pytest.mark.automated
@pytest.mark.initial
@pytest.mark.products
def test_escape_after_product(pdv, product_code):
    pdv.insert_product(product_code)
    pdv.escape()
    modal_text = pdv.active_modal_text().lower()
    visible_text = " ".join((pdv.window.window_text(), pdv.items_text())).lower()
    accepted_messages = (
        "f3",
        "f6",
        "cancelamento de pedido",
        "cancelar venda",
        "finalizar venda",
        "deseja cancelar",
        "motivo de cancelamento",
    )
    assert any(message in modal_text or message in visible_text for message in accepted_messages), \
        f"O modal/mensagem de ESC nao foi exposto: {modal_text or visible_text}"
    if modal_text:
        pdv.escape()
    pdv.cancel_sale()
