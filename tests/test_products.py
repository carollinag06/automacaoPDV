from __future__ import annotations

import pytest


@pytest.mark.automated
@pytest.mark.products
def test_insert_product_from_roteiro(pdv, product_code):
    """INI-10: produto real aparece no memo da venda."""
    try:
        pdv.insert_product(product_code)
        assert pdv.has_item(product_code), "Produto nao apareceu no TMemo pnlProdutos"
    finally:
        pdv.cancel_sale()


@pytest.mark.automated
@pytest.mark.products
def test_remove_product_from_sale(pdv, product_code):
    """INI-11: produto inserido pode ser removido pela referencia negativa."""
    try:
        pdv.insert_product(product_code)
        assert pdv.has_item(product_code), "Produto nao apareceu antes da remocao"
        pdv.remove_product(product_code)
        assert not pdv.has_item(product_code), "Produto permaneceu apos a remocao"
    finally:
        pdv.cancel_sale()
