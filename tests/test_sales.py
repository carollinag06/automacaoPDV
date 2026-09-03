from __future__ import annotations

import pytest

from pages.base_page import UnknownDialogError


CARD_PAYMENT_METHODS = (
    "Mastercard Credito",
    "Mastercard Debito",
    "Visa Credito",
    "Visa Debito",
    "Inter Debito",
    "Tef",
)


@pytest.mark.automated
@pytest.mark.sales
def test_finalize_sale_with_cash(pdv, product_code):
    """Finaliza uma venda real usando a forma Dinheiro e o valor integral."""
    completed = False
    unknown_state = False
    try:
        pdv.insert_product(product_code)
        expected_total = pdv.wait_until_total()
        pdv.finalize_sale(
            payment_method="Dinheiro",
            expected_product=product_code,
            expected_total=expected_total,
            show_receipt_again=False,
        )
        completed = True
        assert pdv.has_window_class("TFrmPDV"), "TFrmPDV nao ficou liberado apos o pagamento"
    except UnknownDialogError:
        unknown_state = True
        raise
    finally:
        if not completed and not pdv.last_sale_submitted and not unknown_state:
            # Fecha apenas um modal de pagamento residual; ESC no TFrmPDV
            # abriria o cancelamento antes de o F6 ser enviado.
            if pdv.modal() is not None:
                pdv.escape()
            pdv.cancel_sale("Teste automatizado: cancelamento apos falha no pagamento")


@pytest.mark.automated
@pytest.mark.sales
@pytest.mark.skip(
    reason=(
        "VEN-16/TEF-02: a finalizacao por Cartao/TEF exige terminal TEF, "
        "cartao/pinpad e autorizacao externa; a suite nao simula hardware ou servico."
    )
)
@pytest.mark.parametrize("payment_method", CARD_PAYMENT_METHODS)
def test_finalize_sale_with_card(pdv, product_code, payment_method):
    """Finaliza uma venda real por cada tipo de cartão configurado no PDV."""
    completed = False
    unknown_state = False
    try:
        pdv.insert_product(product_code)
        expected_total = pdv.wait_until_total()
        pdv.finalize_sale(
            payment_method=payment_method,
            installments=1,
            expected_product=product_code,
            expected_total=expected_total,
            show_receipt_again=False,
        )
        completed = True
        assert pdv.has_window_class("TFrmPDV"), "TFrmPDV nao ficou liberado apos o pagamento"
    except UnknownDialogError:
        unknown_state = True
        raise
    finally:
        if not completed and not pdv.last_sale_submitted and not unknown_state:
            modal = pdv.modal()
            if modal is not None and modal.class_name() == "TFrmInserirPgto":
                try:
                    modal.set_focus()
                    modal.type_keys("{ESC}", set_foreground=True)
                except Exception:
                    pass
            pdv.cancel_sale(f"Teste automatizado: cancelamento apos falha em {payment_method}")


@pytest.mark.automated
@pytest.mark.sales
@pytest.mark.xfail(
    strict=False,
    reason="Known issue VEN-21: o SATPDV homologado aceitou 101 parcelas, acima do limite de 100.",
)
def test_card_installments_over_100_are_rejected(pdv, product_code):
    """VEN-21: o PDV deve rejeitar mais de 100 parcelas."""
    pdv.insert_product(product_code)
    expected_total = pdv.wait_until_total()
    try:
        pdv.finalize_sale(
            payment_method="Mastercard Credito",
            installments=101,
            expected_product=product_code,
            expected_total=expected_total,
            show_receipt_again=False,
        )
    except UnknownDialogError:
        raise
    else:
        pytest.fail(
            "VEN-21: o SATPDV aceitou 101 parcelas e finalizou a venda; "
            "o roteiro exige rejeicao acima de 100"
        )


@pytest.mark.automated
@pytest.mark.sales
def test_fractional_quantity_is_limited_to_three_decimal_places(pdv):
    """VEN-34: a entrada fracionada deve ser armazenada com no maximo 3 casas."""
    # Produto 46 e a quantidade 0,12345 sao os dados do roteiro; nao criamos
    # cadastro nem substituimos o item por um produto ficticio.
    pdv.insert_product("46", quantity="0,12345")
    rendered = pdv.items_text()
    assert pdv.item_count("46") > 0, "VEN-34: produto 46 nao apareceu no TMemo pnlProdutos"
    assert _contains_fractional_quantity(rendered), (
        "VEN-34: a quantidade nao foi limitada a tres casas no TMemo pnlProdutos; "
        f"texto observado={rendered!r}"
    )


def _contains_fractional_quantity(rendered: str) -> bool:
    """Aceita separador decimal local e evita aprovar 0,12345 por engano."""
    import re

    normalized = rendered.replace(".", ",")
    return re.search(r"(?<!\d)0,123(?!\d)", normalized) is not None


@pytest.mark.automated
@pytest.mark.sales
@pytest.mark.parametrize("shortcut", ("F2", "F3"))
def test_empty_sale_shortcut_does_not_open_payment(pdv, shortcut):
    """VEN-07/VEN-08: F2/F3 vazios mantêm o PDV pronto sem pagamento."""
    pdv.window.set_focus()
    pdv.window.type_keys("{" + shortcut + "}", set_foreground=True)
    assert pdv.has_window_class("TFrmPDV"), f"TFrmPDV nao permaneceu aberto apos {shortcut} vazio"
    assert pdv.modal() is None, f"{shortcut} vazio abriu modal inesperado"
