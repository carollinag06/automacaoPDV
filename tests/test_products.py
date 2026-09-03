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
    """INI-11: o item do indice 2 pode ser removido por referencia negativa -2."""
    try:
        pdv.insert_product(product_code)
        pdv.insert_product(product_code)
        assert pdv.wait_until_item_count(product_code, 2), "Dois itens nao apareceram antes da remocao"
        pdv.remove_product("2")
        assert pdv.wait_until_cancelled("2"), "O cancelamento do item 2 nao apareceu no TMemo"
    finally:
        pdv.cancel_sale()


@pytest.mark.automated
@pytest.mark.products
def test_remove_quantity_from_product(pdv):
    """INI-12: remove uma unidade de um item inserido com quantidade 2."""
    try:
        pdv.insert_product("1", quantity="2")
        assert pdv.wait_until_total(timeout=5) == 2, "Total inicial nao refletiu 2 unidades"
        pdv.enter_quantity("-1")
        pdv.enter_reference("1")
        assert pdv.wait_until_total(timeout=5) == 1, "Total nao foi atualizado para 1 unidade"
    finally:
        pdv.cancel_sale("Teste automatizado INI-12: cancelar venda apos remover quantidade")


@pytest.mark.automated
@pytest.mark.products
def test_remove_quantity_by_item_index(pdv):
    """INI-13: remove uma unidade do segundo item usando o indice 2."""
    try:
        pdv.insert_product("3", quantity="3")
        pdv.insert_product("5", quantity="4")
        assert pdv.wait_until_total(timeout=5) == 7, "Total inicial nao refletiu 7 unidades"
        # PDV.pas/EditCodigoProdutoKeyPress consome a quantidade no '*'; o
        # indice segue positivo e e interpretado pelo ParseRef: -1*2.
        pdv.cancel_quantity_by_item_index(quantity=1, index=2)
        actual_total = pdv.wait_until_total(timeout=5)
        assert actual_total == 6, (
            "INI-13: esperado total 6,00 apos remover 1 unidade do item 2; "
            f"SATPDV exibiu {actual_total} e memo={pdv.items_text()!r}"
        )
    finally:
        pdv.cancel_sale("Teste automatizado INI-13: cancelar venda apos remover por indice")


@pytest.mark.automated
@pytest.mark.products
@pytest.mark.xfail(
    strict=False,
    reason="Known issue: TFrmDlgInformacao de item inexistente pode desaparecer/retornar texto vazio antes da leitura",
)
def test_remove_nonexistent_item_shows_warning(pdv):
    """INI-14: remover item inexistente exibe a mensagem prevista."""
    try:
        pdv.insert_product("1")
        # O indice 5 nao existe nesta venda; o sinal negativo pertence apenas
        # a quantidade, portanto a referencia correta e -1*5.
        pdv.cancel_quantity_by_item_index(quantity=1, index=5)
        text = pdv.active_modal_text().casefold()
        assert (
            "item inexistente" in text
            or "ja cancelado anteriormente" in text
            or "já cancelado anteriormente" in text
        ), f"Mensagem INI-14 nao reconhecida: {text}"
        pdv.escape()
    finally:
        pdv.cancel_sale("Teste automatizado INI-14: cancelar venda apos validar aviso")


@pytest.mark.automated
@pytest.mark.products
def test_insert_nonexistent_product_shows_warning(pdv):
    """INI-15: codigo de produto inexistente exibe dialog de produto nao encontrado."""
    pdv.enter_reference("1234")
    text = pdv.active_modal_text().casefold()
    assert "produto" in text and ("nao encontrado" in text or "não encontrado" in text), (
        f"Mensagem INI-15 nao reconhecida: {text}"
    )
    # TFrmPDVProdutoNaoEncontrado documenta F2/Space, nao ESC.
    assert pdv.dismiss_product_not_found(), "INI-15: o dialogo conhecido de produto nao encontrado nao fechou"
