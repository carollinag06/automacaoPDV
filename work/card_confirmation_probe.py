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


def control_summary(control):
    if control is None:
        return None
    cls = safe(control.class_name)
    return {"class": cls, "text": "<redacted>" if cls == "TEdit" else safe(control.window_text)}


def snapshot(page, label, lines):
    lines.append(f"[{label}]")
    for index, window in enumerate(page._top_level_windows()):
        cls = safe(window.class_name)
        title = safe(window.window_text)
        lines.append(f"window[{index}] class={cls!r} title={title!r} visible={safe(window.is_visible)} enabled={safe(window.is_enabled)}")
        if cls == "TFrmInserirPgto":
            lines.append(f"focus={control_summary(safe(window.get_focus, None))!r}")
            for child in window.descendants():
                if safe(child.is_visible) and safe(child.is_enabled):
                    child_cls = safe(child.class_name)
                    text = "<redacted>" if child_cls == "TEdit" else safe(child.window_text)
                    if child_cls in {"TListBox", "TJvValidateEdit", "TBitBtn", "TButton"}:
                        lines.append(f"control class={child_cls!r} text={text!r} texts={safe(child.texts)!r}")


def main() -> int:
    config = load_config(ROOT)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"card_confirmation_probe_{timestamp}.txt"
    app = PdvApplication(config)
    lines = [f"timestamp={timestamp}", "method=Mastercard Credito"]
    code = 1
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        LoginPage(login_dialog or app.window, config).login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        page.insert_product(config.product_code or "1")
        total = page.wait_until_total()
        lines.append(f"total={total}")
        press(page.window, "F3")
        payment = page._wait_for_top_level_class("TFrmInserirPgto", config.action_timeout)
        if payment is None:
            raise AssertionError("TFrmInserirPgto nao abriu")
        initial = page._payment_method_list(payment)
        options = initial.item_texts()
        initial_index = page._payment_method_index(options, "Cartao ou TEF")
        if initial_index is None:
            raise AssertionError(f"Forma Cartao ou TEF nao encontrada: {options!r}")
        initial.set_focus(); press(initial, "HOME")
        for _ in range(initial_index): press(initial, "DOWN")
        press(initial, "ENTER")
        time.sleep(0.3)
        press(page._focused_payment_control(payment) or payment, "ENTER")
        time.sleep(0.4)
        card_list = page._payment_method_list(payment)
        card_options = card_list.item_texts()
        lines.append(f"card_options={card_options!r}")
        card_index = page._payment_method_index(card_options, "Mastercard Credito")
        if card_index is None:
            raise AssertionError(f"Bandeira nao encontrada: {card_options!r}")
        card_list.set_focus(); press(card_list, "HOME")
        for _ in range(card_index): press(card_list, "DOWN")
        lines.append(f"selected_indices_before_enter={safe(card_list.selected_indices)!r}")
        snapshot(page, "before_card_enter", lines)
        press(card_list, "ENTER")
        time.sleep(0.5)
        snapshot(page, "after_card_enter", lines)
        focused = safe(payment.get_focus, None)
        if focused is not None:
            lines.append(f"focused_after_card_enter={control_summary(focused)!r}")
            press(focused, "ENTER")
        else:
            press(payment, "ENTER")
        time.sleep(1.0)
        snapshot(page, "after_focused_enter", lines)
        lines.append("result=OBSERVED: sequencia de confirmacao registrada; sem afirmar aprovacao externa")
        code = 0
    except Exception as exc:
        lines.append(f"result=ERROR: {type(exc).__name__}: {exc}")
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
