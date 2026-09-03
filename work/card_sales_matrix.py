from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app import PdvApplication
from core.config import load_config
from pages.base_page import UnknownDialogError
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


CARD_METHODS = (
    "Mastercard Credito",
    "Mastercard Debito",
    "Visa Credito",
    "Visa Debito",
    "Inter Debito",
    "Tef",
)


def safe(call, default=""):
    try:
        return call()
    except Exception as exc:
        return f"<error {type(exc).__name__}: {exc}>"


def window_record(window):
    cls = safe(window.class_name)
    texts = []
    for child in [window, *safe(window.descendants, [])]:
        child_cls = safe(child.class_name)
        if child_cls == "TEdit":
            continue
        value = safe(child.window_text)
        if value:
            texts.append(value)
    return {"class": cls, "texts": texts[:60], "visible": safe(window.is_visible), "enabled": safe(window.is_enabled)}


def run_one(config, method: str, lines: list[str]) -> str:
    app = PdvApplication(config)
    page = None
    submitted = False
    unknown = False
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        LoginPage(login_dialog or app.window, config).login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        product = config.product_code or "1"
        page.insert_product(product)
        expected_total = page.wait_until_total()
        page.finalize_sale(
            payment_method=method,
            installments=1,
            expected_product=product,
            expected_total=expected_total,
            show_receipt_again=False,
            timeout=min(config.action_timeout, 10.0),
        )
        submitted = page.last_sale_submitted
        lines.append(f"{method}: PASS - venda concluida; comprovante tratado; TFrmPDV liberado")
        return "PASS"
    except UnknownDialogError as exc:
        unknown = True
        lines.append(f"{method}: BLOCKED/ERROR - estado nao mapeado ou integracao externa: {exc}")
        return "BLOCKED"
    except Exception as exc:
        lines.append(f"{method}: FAIL - {type(exc).__name__}: {exc}")
        if page is not None:
            try:
                submitted = page.last_sale_submitted
                lines.append(f"{method}: top_windows={ [window_record(w) for w in page._top_level_windows()]!r}")
            except Exception as diag_exc:
                lines.append(f"{method}: diagnostic_error={type(diag_exc).__name__}: {diag_exc}")
        return "FAIL"
    finally:
        if page is not None and not submitted and not unknown:
            try:
                payment = page.modal()
                if payment is not None and safe(payment.class_name) == "TFrmInserirPgto":
                    payment.set_focus()
                    payment.type_keys("{ESC}", set_foreground=True)
                    time.sleep(0.3)
                page.cancel_sale(f"Teste automatizado: cancelamento apos {method}")
                lines.append(f"{method}: cleanup=cancelamento confirmado com motivo")
            except Exception as cleanup_exc:
                lines.append(f"{method}: cleanup_error={type(cleanup_exc).__name__}: {cleanup_exc}")
        try:
            app.close()
        except Exception as close_exc:
            lines.append(f"{method}: close_error={type(close_exc).__name__}: {close_exc}")


def main() -> int:
    config = load_config(ROOT)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    methods = tuple(sys.argv[1:]) or CARD_METHODS
    report = report_dir / f"card_sales_{timestamp}.txt"
    lines = [
        f"timestamp={timestamp}",
        "suite=VEN-16/TEF-02 - vendas por tipos de cartao exibidos no SATPDV",
        "parcelas=1",
        "product=" + (config.product_code or "1"),
        "policy=nao afirmar aprovacao quando TEF externo nao confirmar; sem credenciais no artefato",
    ]
    results = []
    for method in methods:
        lines.append(f"--- {method} ---")
        results.append((method, run_one(config, method, lines)))
        # Allow the executable and any VCL modal to leave the desktop before
        # the next independent real sale starts.
        time.sleep(1.0)
    lines.append("summary=" + repr(results))
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report)
    print("; ".join(f"{method}={result}" for method, result in results))
    return 0 if all(result == "PASS" for _, result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
