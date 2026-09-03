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


def main() -> int:
    config = load_config(ROOT)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = ROOT / "reports" / f"empty_sale_probe_{stamp}.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"timestamp={stamp}", "scope=VEN-07/VEN-08 venda vazia"]
    app = PdvApplication(config)
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        LoginPage(login_dialog or app.window, config).login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        for key in ("F2", "F3"):
            page.window.set_focus()
            page.window.type_keys("{" + key + "}", set_foreground=True)
            time.sleep(0.8)
            lines.append(f"{key}_modal={page.active_modal_text()!r}")
            lines.append(f"{key}_windows={[ (safe(w.class_name), safe(w.window_text), safe(w.is_visible), safe(w.is_enabled)) for w in page._top_level_windows()]!r}")
            modal = page.modal()
            if modal is not None:
                # Only known information dialogs are dismissed for the next
                # probe; unexpected states remain untouched and are recorded.
                cls = safe(modal.class_name)
                if cls in {"TFrmDlgInformacao", "TFrmPDVProdutoNaoEncontrado"}:
                    modal.set_focus()
                    modal.type_keys("{ENTER}", set_foreground=True)
                    time.sleep(0.3)
        lines.append("result=PASS: mensagens dos atalhos vazios registradas; nenhum pagamento confirmado")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
