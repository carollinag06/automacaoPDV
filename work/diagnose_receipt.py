from __future__ import annotations

import contextlib
import io
import re
import time
from datetime import datetime
from pathlib import Path

from PIL import ImageGrab
from pywinauto import Desktop

from core.app import PdvApplication
from core.config import load_config
from core.keyboard import press
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


def safe_text(value: str, password: str) -> str:
    value = value.replace(password, "<redacted>") if password else value
    return re.sub(r"PDV_PASSWORD\s*[=:]\s*[^\s,;]+", "PDV_PASSWORD=<redacted>", value, flags=re.I)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    report_dir = config.report_root / datetime.now().strftime("%Y-%m-%d")
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = report_dir / f"receipt_diagnostic_{stamp}.log"
    screenshot_path = report_dir / f"receipt_diagnostic_{stamp}.png"
    output = io.StringIO()
    app: PdvApplication | None = None
    receipt = None
    try:
        app = PdvApplication(config)
        app.start()
        login_dialog = app.reveal_login_dialog()
        if login_dialog is None:
            raise RuntimeError("TFrmPassWord nao apareceu")
        LoginPage(login_dialog, config).login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        page.insert_product(config.product_code or "1")
        press(page.window, "F3")
        payment = page._wait_for_top_level_class("TFrmInserirPgto", config.action_timeout)
        if payment is None:
            raise RuntimeError("TFrmInserirPgto nao apareceu apos F3")
        payment_list = page._payment_method_list(payment)
        if payment_list is None:
            raise RuntimeError("Lista de formas de pagamento nao apareceu")
        index = page._payment_method_index(list(payment_list.item_texts()), "Dinheiro")
        if index is None:
            raise RuntimeError("Forma Dinheiro nao apareceu na lista")
        payment_list.set_focus()
        press(payment_list, "HOME")
        for _ in range(index):
            press(payment_list, "DOWN")
        press(payment_list, "ENTER")
        time.sleep(0.3)
        amount = page._focused_payment_control(payment)
        press(amount or payment, "ENTER")

        deadline = time.monotonic() + config.action_timeout
        while time.monotonic() < deadline:
            candidates = [
                window for window in app._windows()
                if window.class_name() not in {"TFrmPDV", "TFrmAbout", "TFrmInserirPgto"}
                and window.is_visible()
            ]
            if candidates:
                receipt = candidates[0]
                break
            time.sleep(0.1)
        if receipt is None:
            raise RuntimeError("Nenhuma janela surgiu apos a confirmacao do pagamento")

        with contextlib.redirect_stdout(output):
            print(f"WINDOW title={receipt.window_text()!r}")
            print(f"WINDOW class={receipt.class_name()!r}")
            print(f"WINDOW handle={receipt.handle}")
            print(f"WINDOW rectangle={receipt.rectangle()}")
            print("WIN32 texts:")
            print(page._window_text(receipt))
            print("WIN32 descendants:")
            print([(child.class_name(), child.window_text()) for child in receipt.descendants()])
            print("WIN32 print_control_identifiers:")
            receipt.print_control_identifiers()
            try:
                uia_receipt = Desktop(backend="uia").window(handle=receipt.handle)
                print("UIA texts:")
                print(page._window_text(uia_receipt))
                print("UIA descendants:")
                print([(child.class_name(), child.window_text()) for child in uia_receipt.descendants()])
                print("UIA print_control_identifiers:")
                uia_receipt.print_control_identifiers()
            except Exception as exc:
                print(f"UIA unavailable/error: {exc!r}")
            try:
                ImageGrab.grab(all_screens=True).save(screenshot_path)
                print(f"SCREENSHOT={screenshot_path}")
            except Exception as exc:
                print(f"SCREENSHOT_ERROR={exc!r}")

        close_action = "not attempted"
        try:
            receipt.set_focus()
            receipt.type_keys("{ESC}", set_foreground=True)
            close_action = "ESC"
        except Exception as exc:
            close_action = f"ESC failed: {exc!r}"
        output.write(f"CLOSE_ACTION={close_action}\n")
    finally:
        log_path.write_text(safe_text(output.getvalue(), config.password), encoding="utf-8")
        if app is not None:
            try:
                app.close(reset=False)
            except Exception:
                pass
    print(f"LOG={log_path}")
    print(f"SCREENSHOT={screenshot_path if screenshot_path.exists() else '<not created>'}")
    for line in output.getvalue().splitlines():
        if line.startswith("WINDOW ") or line.startswith("CLOSE_ACTION=") or line.startswith("UIA unavailable"):
            print(safe_text(line, config.password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
