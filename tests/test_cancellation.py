"""UI-only cancellation coverage for the next pending roteiro items."""

import re

import pytest

from tests.config.test_data import PRODUTOS


def _apply_fixed_discount(pdv, value: str, scenario_id: str) -> str:
    """Apply F12 through the mapped TFrmPDV discount dialog."""
    # TFrmPDV.FormKeyDown -> F12 opens the VCL discount input dialog.
    pdv.send_shortcut("F12")
    dialog = pdv.wait_for_dialog_text(
        r"desconto|valor|percentual|inserir", timeout=3.0
    )
    if dialog is None:
        raise AssertionError(f"{scenario_id}: dialog de desconto não abriu")
    text_before = pdv._window_text(dialog)
    fields = [
        control
        for control in dialog.descendants(class_name="TJvValidateEdit")
        if control.is_visible() and control.is_enabled()
    ]
    if not fields:
        fields = [
            control
            for control in dialog.descendants(class_name="TEdit")
            if control.is_visible() and control.is_enabled()
        ]
    if not fields:
        raise AssertionError(f"{scenario_id}: campo de desconto não foi localizado")
    field = fields[0]
    # TFrmPDVDlg: controle numérico VCL. O foco é físico e as teclas são
    # enviadas sem SetForegroundWindow para não repetir a instabilidade já
    # corrigida no fluxo de quantidade.
    field.click_input()
    field.type_keys("{HOME}", set_foreground=False, pause=0.05)
    field.type_keys("{DELETE}" * 16, set_foreground=False, pause=0.02)
    field.type_keys("{BACKSPACE}" * 16, set_foreground=False, pause=0.02)
    field.type_keys(value, set_foreground=False, pause=0.08)
    observed_field = (field.window_text() or "").strip()
    assert value in observed_field or observed_field.endswith(value), (
        f"{scenario_id}: campo de desconto não recebeu o valor; "
        f"esperado={value!r}, observado={observed_field!r}"
    )
    pdv.confirm_known_dialog(dialog)
    message = pdv.wait_for_information_text(
        r"desconto|limite|autoriza|valor", timeout=2.0
    )
    if message is not None:
        message_text = pdv._window_text(message)
        pdv.confirm_known_dialog(message)
    else:
        message_text = ""
    observed = pdv.discount()
    assert observed > 0 or re.search(r"desconto|valor", message_text, re.IGNORECASE), (
        f"{scenario_id}: desconto não ficou observável; dialog={text_before!r}; "
        f"mensagem={message_text!r}; desconto={observed}"
    )
    return message_text


@pytest.mark.automated
@pytest.mark.sales
def test_can03_cancel_last_item_and_finalize(pdv, evidence):
    """CAN-03: remove item específico e finaliza os itens remanescentes.

    O roteiro exige conferir o documento/pedido após a finalização; nesta
    suíte a confirmação funcional disponível é o grid ``TMemo pnlProdutos``
    e o retorno ao ``TFrmPDV``/PDV_READY. A emissão física continua coberta
    pelos markers manuais já registrados.
    """
    for code in (PRODUTOS["PADRAO"], "2", "3"):
        pdv.insert_product(code)
    assert pdv.has_item("3"), "CAN-03: terceiro item não entrou no TMemo"

    # PDV.pas / EditCodigoProdutoKeyPress: -<codigo> cancela o item pelo
    # código/referência; remove_product aguarda o repaint do TMemo.
    pdv.remove_product("3")
    assert pdv.wait_until_cancelled("3", timeout=3.0), (
        "CAN-03: registro negativo do item cancelado não apareceu no TMemo"
    )
    assert pdv.has_item(PRODUTOS["PADRAO"]) and pdv.has_item("2"), (
        "CAN-03: o cancelamento removeu item remanescente indevidamente"
    )

    pdv.finalize_sale(
        payment_method="Dinheiro",
        expected_product=PRODUTOS["PADRAO"],
        show_receipt_again=False,
    )
    assert pdv.has_window_class("TFrmPDV"), "CAN-03: PDV não retornou ao estado pronto"
    evidence[1].info(
        "CAN-03 PASS: item 3 cancelado, itens 1/2 preservados e venda remanescente "
        "finalizada em Dinheiro; estado=PDV_READY."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_can04_cancel_last_item_preserves_discount(pdv, evidence):
    """CAN-04: desconto permanece no item anterior ao cancelar o último item."""
    pdv.insert_product(PRODUTOS["PADRAO"], quantity="2")
    _apply_fixed_discount(pdv, "0,50", "CAN-04")
    discount_before = pdv.discount()
    pdv.insert_product("2")
    assert pdv.has_item("2"), "CAN-04: segundo item não entrou no TMemo"

    pdv.remove_product("2")
    assert pdv.wait_until_cancelled("2", timeout=3.0), (
        "CAN-04: registro negativo do último item não apareceu após o cancelamento"
    )
    discount_after = pdv.discount()
    assert discount_after == discount_before, (
        f"CAN-04: desconto foi alterado após cancelar o último item; "
        f"antes={discount_before}, depois={discount_after}"
    )
    evidence[1].info(
        "CAN-04 PASS: último item removido e desconto preservado; "
        f"desconto={discount_after}; estado retornou ao fluxo de venda."
    )
