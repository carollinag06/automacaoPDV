from __future__ import annotations

import pytest

from pages.login_page import LoginPage


@pytest.mark.automated
@pytest.mark.smoke
@pytest.mark.products
def test_poc_open_login_and_insert_product(pdv, product_code):
    """PoC: login, tela principal, produto verificável e cancelamento seguro."""
    try:
        pdv.insert_product(product_code)
        assert pdv.has_item(product_code), "Produto não apareceu no TMemo pnlProdutos"
    finally:
        try:
            pdv.cancel_sale()
        except Exception:
            # Fixture teardown closes residual dialogs and the SATPDV process.
            pass


@pytest.mark.automated
@pytest.mark.initial
@pytest.mark.fresh_instance
def test_poc_invalid_login(raw_app, test_config):
    if not test_config.user:
        pytest.skip("PDV_USER real nao configurado para o cenario invalido")
    dialog = LoginPage(raw_app.window, test_config).invalid_login()
    text = dialog.window_text()
    assert text, "A janela de login não expôs texto verificável após credenciais inválidas"
