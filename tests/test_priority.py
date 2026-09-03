from __future__ import annotations

import re

import pytest

from core.keyboard import press
from pages.login_page import LoginPage
from tests.config.test_data import PRODUTOS


@pytest.mark.automated
@pytest.mark.sales
def test_ven18_sale_with_fifty_units(pdv, product_code, evidence):
    """VEN-18: insere uma carga de 50 itens e valida a venda no TMemo."""
    # TFrmPDV.EditCodigoProduto usa o formato ``quantidade*codigo``; o
    # asterisco é consumido por EditCodigoProdutoKeyPress/AlterarQde em
    # PDV.pas antes do código do produto ser confirmado.
    code = product_code or PRODUTOS["PADRAO"]
    pdv.insert_product(code, quantity="50")
    rendered = pdv.items_text()
    assert pdv.item_count(code) > 0, "VEN-18: produto não apareceu no TMemo pnlProdutos"
    assert re.search(r"(?m)^\s*50(?:[,.]0+)?\s+X\s+", rendered), (
        "VEN-18: quantidade 50 não foi renderizada no TMemo pnlProdutos; "
        f"texto observado={rendered[:500]!r}"
    )
    evidence[1].info(
        "VEN-18 PASS parcial: TFrmPDV recebeu quantidade 50 pelo formato "
        "quantidade*codigo e a linha foi confirmada no TMemo pnlProdutos."
    )


@pytest.mark.automated
@pytest.mark.sales
@pytest.mark.fresh_instance
def test_ven25_quantity_preserved_after_process_reopen(app, pdv, test_config, evidence):
    """VEN-25: fecha/reabre o processo e valida recuperação da quantidade."""
    code = PRODUTOS["PADRAO"]
    pdv.insert_product(code, quantity="3")
    assert re.search(r"(?m)^\s*3(?:[,.]0+)?\s+X\s+", pdv.items_text())

    # O cenário exige uma instância nova. O processo é encerrado sem reset de
    # venda para que o próprio SATPDV persista o pedido pendente.
    app.close(reset=False)
    app.start()
    app.dismiss_recovery_prompt(allow_recovery=True, timeout=test_config.action_timeout)
    login_dialog = app.reveal_login_dialog()
    if login_dialog is None:
        raise AssertionError("VEN-25: após o reinício não foi exposto TFrmPassWord")
    LoginPage(login_dialog, test_config).login()
    # O prompt de recuperação pode aparecer antes de TFrmPDV ficar habilitado;
    # confirme-o primeiro, para não fazer wait_until_ready esperar um owner
    # desabilitado por todo o timeout.
    if not app.recover_pending_sale(timeout=test_config.action_timeout):
        main_candidate = app._find_form("TFrmPDV", timeout=1.0)
        if main_candidate is None:
            raise AssertionError(
                "VEN-25: o SATPDV não exibiu a pergunta de recuperação após o login"
            )
    main = app.wait_until_ready(test_config.start_timeout, allow_recovery=True)
    app.window = main

    from pages.pdv_page import PdvPage

    reopened = PdvPage(main, test_config.action_timeout)
    assert reopened.wait_until_item_count(code, 1, timeout=5.0), (
        "VEN-25: venda recuperada não contém o item esperado"
    )
    assert re.search(r"(?m)^\s*3(?:[,.]0+)?\s+X\s+", reopened.items_text()), (
        "VEN-25: quantidade 3 não foi preservada após fechar/reabrir o processo"
    )
    evidence[1].info(
        "VEN-25 PASS: instância fresh reiniciada, recuperação confirmada pelo "
        "botão Sim e quantidade 3 validada no TMemo pnlProdutos."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_ven37_search_product_by_description(pdv, evidence):
    """VEN-37: pesquisa parcial no TDBGrid GridProcura."""
    text = pdv.search_product_by_description("Teste", timeout=6.0)
    assert "teste" in pdv._ascii(text), (
        "VEN-37: GridProcura abriu, mas a descrição pesquisada não foi identificada"
    )
    evidence[1].info(
        "VEN-37 PASS: termo parcial 'Teste' identificado na grade TDBGrid "
        "GridProcura, conforme PDV.dfm/GridProcuraKeyPress."
    )
    pdv.window.set_focus()
    press(pdv.window, "ESC")


@pytest.mark.automated
@pytest.mark.sales
def test_ven38_payment_above_limit_is_rejected(pdv, evidence):
    """VEN-38: valor de pagamento acima de 999999999999 deve ser rejeitado."""
    code = PRODUTOS["PADRAO"]
    pdv.insert_product(code)
    payment, amount_control = pdv.open_payment_amount_field("Dinheiro")
    oversized = "1000000000000"
    amount_control.set_focus()
    amount_control.type_keys("^{A}{BACKSPACE}" + oversized, set_foreground=True, pause=0.03)
    if (amount_control.window_text() or "").strip() != oversized:
        amount_control.set_edit_text(oversized)
    press(amount_control, "ENTER")
    warning = pdv.wait_for_information_text(
        r"limite|m[aá]ximo|permitid|valor.*pag|pagamento|inv[aá]lid", timeout=4.0
    )
    if warning is None:
        pdv.close_payment_dialog()
        raise AssertionError(
            "VEN-38: pagamento acima de 999999999999 não exibiu mensagem de limite"
        )
    text = pdv._window_text(warning)
    pdv.confirm_known_dialog(warning)
    pdv.close_payment_dialog()
    evidence[1].info(
        "VEN-38 PASS: TFrmInserirPgto rejeitou valor acima do limite; "
        f"mensagem observada={text!r}."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_tef10_installments_reject_non_numeric_input(pdv, evidence):
    """TEF-10: campo de parcelas aceita somente caracteres numéricos."""
    pdv.insert_product(PRODUTOS["PADRAO"])
    payment, installments = pdv.open_card_installments("Mastercard Credito", timeout=6.0)
    installments.set_focus()
    installments.type_keys("abc!", set_foreground=True, pause=0.05)
    observed = installments.window_text() or ""
    assert re.search(r"[A-Za-z!]", observed) is None, (
        f"TEF-10: campo de parcelas reteve caracteres não numéricos: {observed!r}"
    )
    evidence[1].info(
        "TEF-10 PASS: TFrmInserirPgto/TJvValidateEdit não reteve letras ou "
        "'!' no campo de parcelas."
    )
    pdv.close_payment_dialog()
