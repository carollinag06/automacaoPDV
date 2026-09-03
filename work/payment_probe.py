from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app import PdvApplication
from core.config import load_config
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


def safe(call, default=""):
    try:
        return call()
    except Exception as exc:
        return f"<error {type(exc).__name__}: {exc}>"


def record(control):
    cls = safe(control.class_name)
    text = "<redacted>" if cls == "TEdit" else safe(control.window_text)
    texts = "<redacted>" if cls == "TEdit" else safe(control.texts)
    rect = safe(control.rectangle, None)
    bounds = "<unavailable>"
    if rect is not None and not isinstance(rect, str):
        bounds = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
    return {"class": cls, "text": text, "texts": texts, "bounds": bounds}


def main() -> int:
    config = load_config(ROOT)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"payment_probe_{timestamp}.txt"
    app = PdvApplication(config)
    lines = [f"timestamp={timestamp}", "scope=mapear pagamentos sem confirmar transacao"]
    code = 1
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        login = LoginPage(login_dialog or app.window, config)
        if not login.is_present():
            raise AssertionError("TFrmPassWord nao foi exposta")
        login.login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        page.insert_product(config.product_code or "1")
        total = page.wait_until_total()
        lines.append(f"product={config.product_code or '1'}")
        lines.append(f"total={total}")

        page.window.set_focus()
        page.window.type_keys("{F3}", set_foreground=True)
        payment = page._wait_for_top_level_class("TFrmInserirPgto", config.action_timeout)
        if payment is None:
            raise AssertionError("TFrmInserirPgto nao abriu")
        lines.append(f"payment_window={record(payment)!r}")
        lines.append("controls=")
        controls = payment.descendants()
        for index, control in enumerate(controls):
            lines.append(f"control[{index}]={record(control)!r}")
        payment_list = page._payment_method_list(payment)
        if payment_list is None:
            raise AssertionError("Nenhuma TListBox de forma de pagamento foi encontrada")
        lines.append(f"payment_list={record(payment_list)!r}")
        lines.append(f"payment_options={safe(payment_list.item_texts)!r}")
        lines.append("card_options_require_followup=Cartao/TEF sera mapeado em execucao controlada")

        # The form and sale are known states. Close only the payment screen;
        # do not select or submit any card transaction during discovery.
        payment.set_focus()
        payment.type_keys("{ESC}", set_foreground=True)
        time.sleep(0.4)
        page.cancel_sale("Teste automatizado: encerramento apos mapeamento de pagamentos")
        lines.append("probe_result=PASS: lista mapeada; nenhuma transacao de cartao confirmada")
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
