from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.test_results import record_dialog_observation
from pages.base_page import capture_unknown_state
from pages.pdv_page import PdvPage
from tests.config.test_data import CLIENTES, PRODUTOS


VOUCHER_STATE = Path("reports") / "vouchers" / "ven11_code.txt"


def _identify_client(pdv: PdvPage, document: str, scenario_id: str) -> str:
    """F5 -> CPF/CNPJ real -> Enter -> F10 - OK, without creating data."""
    pdv.send_shortcut("F5")
    dialog = pdv.wait_for_dialog_text(r"CPF|CNPJ|cliente|documento", timeout=4.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_client_form_not_open")
    control = pdv.fill_client_document(dialog, document)
    name = pdv.lookup_client_document(dialog, control)
    if not name:
        raise AssertionError(
            f"{scenario_id}: CPF {document} nao foi localizado no cadastro do PDV"
        )
    record_dialog_observation(dialog, f"{scenario_id}_client_full_form")
    pdv.confirm_client_dialog(dialog)
    assert pdv.has_window_class("TFrmPDV"), (
        f"{scenario_id}: F10 - OK nao retornou ao TFrmPDV"
    )
    return name


def _read_voucher_code() -> str:
    if not VOUCHER_STATE.exists():
        raise AssertionError(
            "VEN-12/14: o artefato do VEN-11 nao existe; execute VEN-11 antes "
            f"de consumir o voucher ({VOUCHER_STATE})"
        )
    code = VOUCHER_STATE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d{8,30}", code):
        raise AssertionError(f"Codigo de voucher invalido no artefato: {code!r}")
    return code


@pytest.mark.automated
@pytest.mark.sales
def test_ven11_generate_voucher(pdv, evidence):
    """VEN-11: gera Vale Troca de R$ 2,00 e lê o código do comprovante salvo."""
    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-11")
    pdv.enter_reference(f"-2*{PRODUTOS['PADRAO']}")
    assert pdv.wait_until_cancelled("1", timeout=5.0), (
        "VEN-11: item negativo -2*1 nao apareceu no TMemo pnlProdutos"
    )

    proof_path, code = pdv.finalize_voucher_sale(
        proof_stem="VEN-11_voucher_2_reais",
        timeout=12.0,
    )
    proof_text = PdvPage._read_saved_proof(proof_path)
    assert re.search(r"2[,.]00", proof_text), (
        f"VEN-11: comprovante nao confirmou valor R$ 2,00; path={proof_path}"
    )
    VOUCHER_STATE.parent.mkdir(parents=True, exist_ok=True)
    VOUCHER_STATE.write_text(code, encoding="utf-8")
    evidence[1].info(
        f"VEN-11 PASS: cliente localizado por F5; comprovante salvo em {proof_path}; "
        f"valor R$ 2,00 e codigo de barras capturado (tamanho={len(code)})."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_ven12_consume_voucher(pdv, evidence):
    """VEN-12: consome o voucher gerado em VEN-11 em uma venda nova."""
    code = _read_voucher_code()
    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-12")
    pdv.insert_product(PRODUTOS["PADRAO"])
    payment = pdv.open_voucher_payment_form(code, timeout=12.0)
    pdv.submit_voucher_payment(payment, timeout=12.0)
    assert pdv.has_window_class("TFrmPDV"), "VEN-12: PDV nao retornou ao estado pronto"
    evidence[1].info(
        f"VEN-12 PASS: voucher de VEN-11 informado no campo Cód. Barras; "
        "venda finalizada e PDV_READY confirmado."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_ven13_consume_larger_voucher_without_change(pdv, evidence):
    """VEN-13: gera voucher de R$ 5,00 e o consome em produto de R$ 1,00."""
    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-13_GERACAO")
    pdv.enter_reference(f"-5*{PRODUTOS['PADRAO']}")
    assert pdv.wait_until_cancelled("1", timeout=5.0)
    proof_path, code = pdv.finalize_voucher_sale(
        proof_stem="VEN-13_voucher_5_reais",
        timeout=12.0,
    )
    proof_text = PdvPage._read_saved_proof(proof_path)
    assert re.search(r"5[,.]00", proof_text), (
        f"VEN-13: comprovante nao confirmou valor R$ 5,00; path={proof_path}"
    )

    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-13_CONSUMO")
    pdv.insert_product(PRODUTOS["PADRAO"])
    payment = pdv.open_voucher_payment_form(code, timeout=12.0)
    pdv.submit_voucher_payment(payment, timeout=12.0)
    assert pdv.has_window_class("TFrmPDV"), "VEN-13: PDV nao retornou ao estado pronto"
    evidence[1].info(
        f"VEN-13 PASS: voucher de R$ 5,00 salvo em {proof_path}, usado em venda "
        "de R$ 1,00 sem aceitar troco."
    )


@pytest.mark.automated
@pytest.mark.sales
@pytest.mark.xfail(
    reason="XFAIL: Regra VEN-14 - Campo de Código de Barras do Vale Troca não é limpo automaticamente após mensagem de erro.",
    strict=False,
)
def test_ven14_reject_used_voucher(pdv, evidence):
    """VEN-14: o código consumido em VEN-12 não pode ser reutilizado."""
    code = _read_voucher_code()
    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-14")
    pdv.insert_product(PRODUTOS["PADRAO"])
    payment = pdv.open_voucher_payment_form(code, timeout=12.0)
    field = pdv._focused_payment_control(payment)
    if field is None:
        raise capture_unknown_state(payment, "VEN-14_used_voucher_field_missing")
    field.set_focus()
    field.type_keys("{ENTER}", set_foreground=True, pause=0.05)
    warning = pdv.wait_for_information_text(
        r"c[oó]digo\s+de\s+barras.*vale\s+troca.*inv[aá]lido|"
        r"j[aá]\s+utilizado|voucher.*inv[aá]lido",
        timeout=5.0,
    )
    if warning is None:
        raise capture_unknown_state(payment, "VEN-14_used_voucher_warning_missing")
    text = pdv._window_text(warning)
    record_dialog_observation(warning, "VEN-14_used_voucher_warning")
    pdv.confirm_known_dialog(warning)
    assert re.search(r"inv[aá]lido|utilizado", text, re.IGNORECASE), (
        f"VEN-14: mensagem inesperada para voucher usado: {text!r}"
    )
    assert not (field.window_text() or "").strip(), (
        f"VEN-14: campo Cód. Barras nao foi limpo: {field.window_text()!r}"
    )
    pdv.close_payment_dialog()
    evidence[1].info(
        f"VEN-14 PASS: código de VEN-11 rejeitado como usado; mensagem={text!r}."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_ven15_smaller_voucher_sale(pdv, evidence):
    """VEN-15: registra o resultado bruto do voucher menor, sem quitá-lo manualmente.

    A classificação PASS/XFAIL permanece deliberadamente pendente de decisão
    de negócio. O teste termina após a observação e não escolhe uma segunda
    forma de pagamento para mascarar o comportamento exibido pelo PDV.
    """
    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-15_GERACAO")
    pdv.enter_reference(f"-1*{PRODUTOS['PADRAO']}")
    assert pdv.wait_until_cancelled("1", timeout=5.0)
    _, code = pdv.finalize_voucher_sale(
        proof_stem="VEN-15_voucher_menor",
        timeout=12.0,
    )

    _identify_client(pdv, CLIENTES["VOUCHER"], "VEN-15_CONSUMO")
    pdv.insert_product("33")
    payment = pdv.open_voucher_payment_form(code, timeout=12.0)
    raw = pdv.observe_voucher_payment_without_settling(payment, timeout=12.0)
    evidence[1].info(
        "VEN-15 DIAGNOSTICO (sem classificacao): voucher menor informado no "
        f"produto 33; estado_bruto={raw['state']}; classe={raw['class_name']}; "
        f"texto_completo={raw['text']!r}"
    )
    pytest.skip(
        "VEN-15 diagnóstico sem classificação PASS/XFAIL; resultado bruto "
        f"registrado como {raw['state']} em dialogs_observed.jsonl"
    )
