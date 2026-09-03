from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app import PdvApplication
from core.config import load_config
from pages.base_page import UnknownDialogError
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


def safe(call, default=""):
    try:
        return call()
    except Exception as exc:
        return f"<error {type(exc).__name__}: {exc}>"


def record(window):
    cls = safe(window.class_name)
    texts = []
    for child in [window, *safe(window.descendants, [])]:
        child_cls = safe(child.class_name)
        if child_cls == "TEdit":
            continue
        value = safe(child.window_text)
        if value:
            texts.append(value)
    return {"class": cls, "texts": texts[:80], "visible": safe(window.is_visible), "enabled": safe(window.is_enabled)}


def main() -> int:
    config = load_config(ROOT)
    method = sys.argv[1] if len(sys.argv) > 1 else "Mastercard Credito"
    installments = sys.argv[2] if len(sys.argv) > 2 else "1"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"card_sale_probe_{method.replace(' ', '_').lower()}_{timestamp}.txt"
    app = PdvApplication(config)
    page = None
    lines = [
        f"timestamp={timestamp}",
        f"method={method}",
        f"installments={installments}",
        "scope=venda real controlada; sem aprovar credencial externa",
    ]
    code = 1
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        login = LoginPage(login_dialog or app.window, config)
        login.login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        page.insert_product(config.product_code or "1")
        expected_total = page.wait_until_total()
        page.finalize_sale(
            payment_method=method,
            installments=installments,
            expected_product=config.product_code or "1",
            expected_total=expected_total,
            timeout=min(config.action_timeout, 10.0),
        )
        lines.append("result=PASS: venda concluida e interface retornou ao TFrmPDV")
        code = 0
    except Exception as exc:
        lines.append(f"result={type(exc).__name__}: {exc}")
        if isinstance(exc, UnknownDialogError):
            lines.append("classification=UNMAPPED_OR_EXTERNAL_STATE; nao foi feita interacao adicional")
        elif page is not None and page.last_sale_submitted:
            lines.append("classification=SALE_SUBMITTED_BUT_POST_FLOW_FAILED")
        else:
            lines.append("classification=PAYMENT_FLOW_NOT_CONFIRMED")
        lines.append("top_windows=")
        try:
            for index, window in enumerate(page._top_level_windows() if page is not None else []):
                lines.append(f"window[{index}]={record(window)!r}")
        except Exception as diag_exc:
            lines.append(f"diagnostic_error={type(diag_exc).__name__}: {diag_exc}")
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
