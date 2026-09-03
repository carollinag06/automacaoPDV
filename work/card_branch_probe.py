from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app import PdvApplication
from core.config import load_config
from core.keyboard import press
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


def safe(call, default=""):
    try:
        return call()
    except Exception as exc:
        return f"<error {type(exc).__name__}: {exc}>"


def record(control):
    cls = safe(control.class_name)
    return {
        "class": cls,
        "text": "<redacted>" if cls == "TEdit" else safe(control.window_text),
        "texts": "<redacted>" if cls == "TEdit" else safe(control.texts),
        "visible": safe(control.is_visible),
        "enabled": safe(control.is_enabled),
    }


def main() -> int:
    config = load_config(ROOT)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"card_branch_probe_{timestamp}.txt"
    app = PdvApplication(config)
    lines = [f"timestamp={timestamp}", "scope=mapear ramo Cartao ou TEF sem escolher bandeira"]
    code = 1
    submitted = False
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        login = LoginPage(login_dialog or app.window, config)
        login.login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        page.insert_product(config.product_code or "1")
        total = page.wait_until_total()
        lines.append(f"product={config.product_code or '1'}")
        lines.append(f"total={total}")

        press(page.window, "F3")
        payment = page._wait_for_top_level_class("TFrmInserirPgto", config.action_timeout)
        if payment is None:
            raise AssertionError("TFrmInserirPgto nao abriu")
        payment_list = page._payment_method_list(payment)
        if payment_list is None:
            raise AssertionError("Lista de formas nao encontrada")
        options = payment_list.item_texts()
        lines.append(f"payment_options={options!r}")
        card_index = page._payment_method_index(options, "Cartao ou TEF")
        if card_index is None:
            raise AssertionError("Cartao ou TEF nao esta disponivel")

        payment_list.set_focus()
        press(payment_list, "HOME")
        for _ in range(card_index):
            press(payment_list, "DOWN")
        lines.append(f"selected_payment_index={safe(payment_list.selected_indices)!r}")
        press(payment_list, "ENTER")
        time.sleep(0.3)
        amount = page._focused_payment_control(payment)
        lines.append(f"amount_control={record(amount)!r}" if amount is not None else "amount_control=None")
        press(amount or payment, "ENTER")
        time.sleep(1.0)

        lines.append("after_amount_enter_top_windows=")
        for index, window in enumerate(page._top_level_windows()):
            lines.append(f"window[{index}]={record(window)!r}")
            if safe(window.class_name) == "TFrmInserirPgto":
                lines.append("payment_controls_after_amount=")
                for child_index, child in enumerate(window.descendants()):
                    if safe(child.is_visible) and safe(child.is_enabled):
                        lines.append(f"visible_control[{child_index}]={record(child)!r}")

        # Do not choose a card or submit TEF during discovery. Close the known
        # payment form, then cancel the test sale with the required reason.
        current_payment = page._wait_for_top_level_class("TFrmInserirPgto", 0.5)
        if current_payment is not None:
            current_payment.set_focus()
            press(current_payment, "ESC")
            time.sleep(0.4)
        page.cancel_sale("Teste automatizado: encerramento apos mapeamento de cartao")
        lines.append("probe_result=PASS: etapa pos-valor registrada; nenhuma bandeira/TEF confirmado")
        code = 0
    except Exception as exc:
        lines.append(f"probe_result=ERROR: {type(exc).__name__}: {exc}")
    finally:
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            app.close()
        except Exception as exc:
            lines.append(f"close_error={type(exc).__name__}: {exc}")
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
