from __future__ import annotations

import re
import time

import pytest

from core.test_results import record_dialog_observation
from pages.base_page import capture_unknown_state
from pages.login_page import LoginPage


# PDV.pas, TFrmPDV.FormKeyDown: Ctrl+F2 chama InserirSupSan(tsSuprimento)
# e Ctrl+F3 chama InserirSupSan(tsSangria). O valor é digitado no formulário
# VCL de suprimento/sangria. O próprio formulário documenta F10 - Inserir e
# Esc - Cancelar; não usar Enter como confirmação implícita.
SUPPLY_DIALOG_PATTERN = r"suprimento|sangria|valor|inserir|digite"


def _open_cash_movement(pdv, shortcut: str, scenario_id: str):
    pdv.send_shortcut(shortcut)
    dialog = pdv.wait_for_dialog_text(SUPPLY_DIALOG_PATTERN, timeout=5.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_movement_dialog_not_open")
    record_dialog_observation(dialog, f"before_fill_cash_movement_{scenario_id}")
    return dialog


def _fill_amount(pdv, dialog, amount: str, scenario_id: str) -> None:
    # Do not use the generic dialog_edits ordering here: the form also has a
    # TEdit for the payment description and a TJvComboEdit for its code. The
    # amount control is specifically TJvValidateEdit in the runtime class.
    controls = [
        control
        for control in dialog.descendants(class_name="TJvValidateEdit")
        if control.is_visible() and control.is_enabled()
    ]
    if not controls:
        raise capture_unknown_state(dialog, f"{scenario_id}_movement_amount_field_missing")
    field = controls[0]
    value = str(amount)
    for _ in range(3):
        try:
            dialog.set_focus()
            field.set_focus()
            field.click_input()
            field.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.05)
            field.type_keys(value, set_foreground=True, pause=0.06)
        except Exception:
            pass
        try:
            field.set_edit_text(value)
        except Exception:
            pass
        observed = (field.window_text() or "").replace(".", ",")
        if observed == value or observed.endswith(value):
            return
        time.sleep(0.15)
    raise AssertionError(
        f"{scenario_id}: campo TJvValidateEdit não recebeu o valor; "
        f"esperado={value!r}, observado={field.window_text()!r}"
    )


def _submit_amount(dialog) -> None:
    # TDlgInserirSuprimentoOuSangria documenta F10 - Inserir; Enter não é o
    # comando de confirmação desse formulário. ESC continua sendo o cancelamento.
    dialog.set_focus()
    # Controle VCL observado em runtime: TSatSpeedButton com caption
    # "F10 - Inserir". O clique no controle mantém a mesma semântica do
    # atalho documentado e não depende de o formulário estar com foco lógico.
    for button in dialog.descendants(class_name="TSatSpeedButton"):
        if re.sub(r"\s+", " ", button.window_text() or "").strip().casefold() == "f10 - inserir":
            button.click_input()
            return
    dialog.type_keys("{F10}", set_foreground=True, pause=0.05)


def _close_movement_dialog(dialog, scenario_id: str) -> None:
    """Close the known movement form after its result was observed.

    ESC is the documented ``Esc - Cancelar`` action on
    ``TDlgInserirSuprimentoOuSangria``. When called after a successful
    insertion, it closes the reusable entry screen; it does not roll back the
    movement already confirmed by the information dialog.
    """
    # First use the documented keyboard command. The button is a known
    # control-level fallback for VCL builds that do not route ESC to the form.
    for action in ("keyboard", "button"):
        try:
            dialog.set_focus()
            if action == "keyboard":
                dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
            else:
                for button in dialog.descendants(class_name="TSatSpeedButton"):
                    caption = re.sub(r"\s+", " ", button.window_text() or "").strip().casefold()
                    if caption == "esc - cancelar":
                        button.click_input()
                        break
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                if not dialog.exists() or not dialog.is_visible():
                    return
                time.sleep(0.1)
        except Exception:
            continue
    _assert_dialog_closed(dialog, scenario_id)


def _authorize_if_requested(pdv, test_config) -> bool:
    authorization = pdv.wait_for_password_dialog(timeout=1.0)
    if authorization is None:
        return False
    user, password = test_config.manager_credentials
    if not user or not password:
        raise AssertionError("Autorizacao solicitada, mas credenciais de gerente nao configuradas")
    LoginPage(authorization, test_config).login(user=user, password=password)
    return True


def _dismiss_known_information(pdv, pattern: str, scenario_id: str, timeout: float = 1.5) -> str:
    information = pdv.wait_for_information_text(pattern, timeout=timeout)
    if information is None:
        return ""
    text = pdv._window_text(information)
    record_dialog_observation(information, f"before_dismiss_movement_message_{scenario_id}")
    pdv.confirm_known_dialog(information)
    return text


def _submit_until_result(pdv, dialog, test_config, pattern: str, scenario_id: str) -> str:
    """Submit a valid movement with bounded retry for VCL repaint/focus."""
    for attempt in range(3):
        if attempt:
            try:
                if not dialog.exists() or not dialog.is_visible():
                    return ""
            except Exception:
                return ""
            record_dialog_observation(dialog, f"retry_submit_movement_{scenario_id}_{attempt}")
            _submit_amount(dialog)
        else:
            _submit_amount(dialog)
        _authorize_if_requested(pdv, test_config)
        message = _dismiss_known_information(pdv, pattern, scenario_id, timeout=2.0)
        if message:
            return message
    return ""


def _wait_for_movement_in_memo(pdv, amount: str, scenario_id: str) -> None:
    """Confirm amount/total rendered by ShowSupSan in ``TMemo``.

    The runtime form uses the payment-form description (currently ``Dinheiro``)
    as the memo row. The operation type is exposed by the confirmation dialog,
    so the two validations are intentionally kept separate.
    """
    expected_amount = amount.replace(".", ",")
    deadline = time.monotonic() + 4.0
    last_text = ""
    while time.monotonic() < deadline:
        last_text = pdv.items_text()
        normalized = re.sub(r"\s+", " ", last_text).casefold()
        if expected_amount in normalized and "total geral" in normalized:
            return
        time.sleep(0.1)
    raise AssertionError(
        f"{scenario_id}: valor/total nao apareceu no TMemo pnlProdutos; "
        f"valor esperado={amount!r}, texto={last_text!r}"
    )


def _assert_dialog_closed(dialog, scenario_id: str) -> None:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            if not dialog.exists() or not dialog.is_visible():
                return
        except Exception:
            return
        time.sleep(0.1)
    raise AssertionError(f"{scenario_id}: ESC nao fechou a dialog de suprimento/sangria")


def _assert_negative_rejected(pdv, dialog, message: str, scenario_id: str) -> None:
    """Validate either explicit warning or the form-level rejection state."""
    if re.search(r"negativ|valor", message, re.IGNORECASE):
        return

    # Some repetitions reject the value without creating TFrmDlgInformacao:
    # TJvValidateEdit keeps -1,00 in TDlgInserirSuprimentoOuSangria and the
    # ShowSupSan callback never adds a Total geral to TMemo pnlProdutos.
    record_dialog_observation(dialog, f"after_rejected_negative_{scenario_id}")
    dialog_text = pdv._window_text(dialog)
    memo_text = pdv.items_text()
    assert re.search(r"-1[,.]00", dialog_text), (
        f"{scenario_id}: valor negativo não foi mantido como rejeitado; "
        f"dialog={dialog_text!r}, mensagem={message!r}"
    )
    assert "total geral" not in re.sub(r"\s+", " ", memo_text).casefold(), (
        f"{scenario_id}: valor negativo gerou registro no TMemo; texto={memo_text!r}"
    )


SUPPLY_CASES = (
    pytest.param("SUP-01", "CTRL+F2", "Suprimento", "1,00", False, False, id="SUP-01"),
    pytest.param("SUP-02", "CTRL+F2", "Suprimento", "-1,00", False, False, id="SUP-02"),
    pytest.param(
        "SUP-03",
        "CTRL+F2",
        "Suprimento",
        "1,00",
        True,
        False,
        marks=pytest.mark.xfail(
            strict=False,
            reason=(
                "Roteiro exige abrir a entrada com venda aberta, mas PDV.pas "
                "bloqueia Ctrl+F2 com 'Impossível Inserir Suprimento com Venda em Aberto!'"
            ),
        ),
        id="SUP-03",
    ),
    pytest.param(
        "MFI-04",
        "CTRL+F2",
        "Suprimento",
        "1,00",
        True,
        False,
        marks=pytest.mark.xfail(
            strict=False,
            reason=(
                "MFI-04: o roteiro espera a entrada com venda aberta, mas "
                "PDV.pas bloqueia Ctrl+F2 com 'Impossível Inserir Suprimento "
                "com Venda em Aberto!'"
            ),
        ),
        id="MFI-04",
    ),
    pytest.param("SUP-04", "CTRL+F2", "Suprimento", "", False, True, id="SUP-04"),
    pytest.param("MFI-03", "CTRL+F2", "Suprimento", "-1,00", False, False, id="MFI-03"),
)


@pytest.mark.automated
@pytest.mark.parametrize(
    "scenario_id,shortcut,label,amount,with_sale,cancel", SUPPLY_CASES
)
def test_supply_routes(
    pdv, test_config, product_code, scenario_id, shortcut, label, amount, with_sale, cancel
):
    """Suprimento: Ctrl+F2, valor, bloqueio com venda e cancelamento por ESC."""
    if with_sale:
        pdv.insert_product(product_code)
    dialog = _open_cash_movement(pdv, shortcut, scenario_id)

    # Para SUP-03 o teste exige o formulário de entrada mesmo com itens no
    # TMemo pnlProdutos; a implementação atual retorna uma mensagem de bloqueio.
    if with_sale:
        text = pdv._window_text(dialog)
        assert not re.search(r"imposs[ií]vel|venda em aberto", text, re.IGNORECASE), (
            f"{scenario_id}: o roteiro exige dialog de suprimento com venda aberta; "
            f"mensagem real={text!r}"
        )

    if cancel:
        dialog.set_focus()
        dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
        _assert_dialog_closed(dialog, scenario_id)
        assert pdv.wait_until_window_class("TFrmPDV", timeout=3.0) is not None
        return

    _fill_amount(pdv, dialog, amount, scenario_id)
    message_pattern = r"suprimento|valor.*negativ|negativ|inserido|autoriza"
    if amount.startswith("-"):
        _submit_amount(dialog)
        _authorize_if_requested(pdv, test_config)
        message = _dismiss_known_information(pdv, message_pattern, scenario_id, timeout=2.0)
    else:
        message = _submit_until_result(pdv, dialog, test_config, message_pattern, scenario_id)

    if amount.startswith("-"):
        _assert_negative_rejected(pdv, dialog, message, scenario_id)
        _close_movement_dialog(dialog, scenario_id)
        assert pdv.wait_until_window_class("TFrmPDV", timeout=3.0) is not None
        return

    # ShowSupSan grava a forma e o total no TMemo pnlProdutos. A impressão
    # física do comprovante continua fora do escopo sem impressora configurada.
    assert re.search(rf"{label}.*inserido", message, re.IGNORECASE | re.DOTALL), (
        f"{scenario_id}: confirmação de {label} não reconhecida; mensagem={message!r}"
    )
    assert amount in message, (
        f"{scenario_id}: valor não confirmado na mensagem; esperado={amount!r}, mensagem={message!r}"
    )
    _wait_for_movement_in_memo(pdv, amount, scenario_id)
    _close_movement_dialog(dialog, scenario_id)
    assert pdv.wait_until_window_class("TFrmPDV", timeout=4.0) is not None


SANGRIA_CASES = (
    pytest.param("SAN-01", "CTRL+F3", "Sangria", "1,00", False, False, id="SAN-01"),
    pytest.param("SAN-02", "CTRL+F3", "Sangria", "-1,00", False, False, id="SAN-02"),
    pytest.param(
        "SAN-03",
        "CTRL+F3",
        "Sangria",
        "1,00",
        True,
        False,
        marks=pytest.mark.xfail(
            strict=False,
            reason=(
                "Roteiro exige abrir a entrada com venda aberta, mas PDV.pas "
                "bloqueia Ctrl+F3 com 'Impossível Inserir Sangria com Venda em Aberto!'"
            ),
        ),
        id="SAN-03",
    ),
    pytest.param("MFI-06", "CTRL+F3", "Sangria", "-1,00", False, False, id="MFI-06"),
    pytest.param("SAN-04", "CTRL+F3", "Sangria", "", False, True, id="SAN-04"),
)


@pytest.mark.automated
@pytest.mark.parametrize(
    "scenario_id,shortcut,label,amount,with_sale,cancel", SANGRIA_CASES
)
def test_withdrawal_routes(
    pdv, test_config, product_code, scenario_id, shortcut, label, amount, with_sale, cancel
):
    """Sangria: Ctrl+F3, valor, bloqueio com venda e cancelamento por ESC."""
    if with_sale:
        pdv.insert_product(product_code)
    items_before_movement = pdv.items_text() if with_sale else ""
    dialog = _open_cash_movement(pdv, shortcut, scenario_id)

    if with_sale:
        text = pdv._window_text(dialog)
        items_after_movement = pdv.items_text()
        # Item 6 da seção 14 exige a dialog aberta sem apagar os itens do grid.
        # Guardamos os dois estados para distinguir perda de itens de bloqueio
        # da abertura pelo próprio SATPDV.
        if scenario_id == "SAN-03":
            assert re.search(
                rf"^\s*\d+\s+0*{re.escape(product_code)}\b",
                items_after_movement,
                re.MULTILINE,
            ), (
                f"{scenario_id}: os itens foram removidos ao abrir a dialog; "
                f"antes={items_before_movement!r}; depois={items_after_movement!r}; "
                f"dialog={text!r}"
            )
        assert not re.search(r"imposs[ií]vel|venda em aberto", text, re.IGNORECASE), (
            f"{scenario_id}: o roteiro exige dialog de sangria com venda aberta; "
            f"mensagem real={text!r}; itens_antes={items_before_movement!r}; "
            f"itens_depois={items_after_movement!r}"
        )

    if cancel:
        dialog.set_focus()
        dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
        _assert_dialog_closed(dialog, scenario_id)
        assert pdv.wait_until_window_class("TFrmPDV", timeout=3.0) is not None
        return

    _fill_amount(pdv, dialog, amount, scenario_id)
    message_pattern = r"sangria|valor.*negativ|negativ|inserido|autoriza"
    if amount.startswith("-"):
        _submit_amount(dialog)
        _authorize_if_requested(pdv, test_config)
        message = _dismiss_known_information(pdv, message_pattern, scenario_id, timeout=2.0)
    else:
        message = _submit_until_result(pdv, dialog, test_config, message_pattern, scenario_id)

    if amount.startswith("-"):
        _assert_negative_rejected(pdv, dialog, message, scenario_id)
        _close_movement_dialog(dialog, scenario_id)
        assert pdv.wait_until_window_class("TFrmPDV", timeout=3.0) is not None
        return

    assert re.search(rf"{label}.*inserido", message, re.IGNORECASE | re.DOTALL), (
        f"{scenario_id}: confirmação de {label} não reconhecida; mensagem={message!r}"
    )
    assert amount in message, (
        f"{scenario_id}: valor não confirmado na mensagem; esperado={amount!r}, mensagem={message!r}"
    )
    _wait_for_movement_in_memo(pdv, amount, scenario_id)
    _close_movement_dialog(dialog, scenario_id)
    assert pdv.wait_until_window_class("TFrmPDV", timeout=4.0) is not None


def _focus_signature(control):
    """Return a stable, non-text signature for the currently focused VCL child."""
    try:
        handle = int(control.handle)
    except Exception:
        handle = None
    try:
        class_name = control.class_name()
    except Exception:
        class_name = ""
    try:
        name = str(getattr(control.element_info, "name", "") or "")
    except Exception:
        name = ""
    return handle, class_name, name


def _movement_focusable_controls(dialog):
    """List the real editable VCL controls in tab order approximation.

    The dialog is ``TDlgInserirSuprimentoOuSangria``.  Its inputs are exposed
    as ``TJvValidateEdit``/``TEdit`` controls; the helper deliberately does
    not choose by screen coordinates or by the value rendered in a field.
    """
    controls = []
    for class_name in ("TJvValidateEdit", "TEdit", "TJvComboEdit", "TComboBox"):
        try:
            controls.extend(
                control
                for control in dialog.descendants(class_name=class_name)
                if control.is_visible() and control.is_enabled()
            )
        except Exception:
            continue
    unique = {}
    for control in controls:
        try:
            unique[int(control.handle)] = control
        except Exception:
            unique[id(control)] = control
    return sorted(
        unique.values(),
        key=lambda control: (
            getattr(control.rectangle(), "top", 0),
            getattr(control.rectangle(), "left", 0),
        ),
    )


@pytest.mark.automated
def test_san07_enter_moves_to_next_field_like_tab(pdv, evidence):
    """248531/SAN-07: Enter na sangria deve avançar como Tab.

    Este é um registro complementar ao roteiro formal.  A validação usa a
    dialog VCL ``TDlgInserirSuprimentoOuSangria`` e seus campos
    ``TJvValidateEdit``/``TEdit``; nenhuma forma de pagamento ou impressão é
    simulada.  O valor não é confirmado, pois o caso valida somente a
    navegação de teclado.
    """
    dialog = _open_cash_movement(pdv, "CTRL+F3", "SAN-07")
    controls = _movement_focusable_controls(dialog)
    if len(controls) < 2:
        raise capture_unknown_state(dialog, "SAN-07_movement_second_field_missing")

    dialog.set_focus()
    controls[0].set_focus()
    before = _focus_signature(dialog.get_focus())
    dialog.type_keys("{ENTER}", set_foreground=True, pause=0.08)

    deadline = time.monotonic() + 2.0
    after = before
    while time.monotonic() < deadline:
        try:
            after = _focus_signature(dialog.get_focus())
        except Exception:
            after = before
        if after != before:
            break
        time.sleep(0.1)

    record_dialog_observation(dialog, "after_enter_focus_navigation_SAN-07")
    assert after != before, (
        "SAN-07: Enter nao avancou o foco como Tab na dialog de sangria; "
        f"antes={before!r}, depois={after!r}, texto={pdv._window_text(dialog)!r}"
    )
    _close_movement_dialog(dialog, "SAN-07")
    assert pdv.wait_until_window_class("TFrmPDV", timeout=3.0) is not None
