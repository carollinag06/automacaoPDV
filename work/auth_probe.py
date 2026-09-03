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


def snapshot(app: PdvApplication, label: str, lines: list[str]) -> None:
    process = app.process
    responding = "<unavailable>"
    pid = "<unavailable>"
    try:
        pid = str(app.window.process_id()) if app.window is not None else "<none>"
        responding = "true" if app._windows() else "false"
    except Exception as exc:
        responding = f"<error {type(exc).__name__}>"
    lines.append(f"[{datetime.now().isoformat(timespec='milliseconds')}] {label} pid={pid} running={responding}")
    for window in app._windows():
        try:
            lines.append(
                "  window="
                f"class={window.class_name()!r} title={window.window_text()!r} "
                f"visible={window.is_visible()} enabled={window.is_enabled()}"
            )
            if window.class_name() == "TFrmDlgInformacao":
                messages = []
                for child in window.descendants():
                    try:
                        value = child.window_text() or ""
                        if value:
                            messages.append(value)
                    except Exception:
                        pass
                lines.append(f"  information_text={messages!r}")
            elif window.class_name() == "TFrmPassWord":
                controls = []
                for child in window.descendants():
                    try:
                        # Do not record edit contents, which can contain
                        # credentials. Button captions are safe evidence.
                        if child.class_name() != "TEdit":
                            controls.append((child.class_name(), child.window_text() or ""))
                    except Exception:
                        pass
                lines.append(f"  password_controls={controls!r}")
        except Exception as exc:
            lines.append(f"  window=<error {type(exc).__name__}: {exc}>")


def main() -> int:
    config = load_config(ROOT)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = ROOT / "reports" / f"auth_probe_{stamp}.log"
    report.parent.mkdir(parents=True, exist_ok=True)
    app = PdvApplication(config)
    lines: list[str] = [f"timestamp={stamp}", "credentials_values_logged=false"]
    result = 0
    try:
        app.start()
        snapshot(app, "after_start", lines)
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        snapshot(app, "after_recovery_dismiss", lines)
        login_dialog = app.reveal_login_dialog()
        snapshot(app, "after_reveal_login", lines)
        if login_dialog is None:
            raise RuntimeError("login_dialog_not_found")
        login = LoginPage(login_dialog, config)
        try:
            login.login()
            lines.append("login_result=returned")
        except Exception as exc:
            lines.append(f"login_result=exception type={type(exc).__name__} message={exc}")
            result = 1
        snapshot(app, "immediately_after_login", lines)
        warning = login._find_information_modal()
        if warning is not None:
            lines.append("manual_warning_probe=found")
            try:
                login._dismiss_warning(warning)
                lines.append("manual_warning_probe=dismiss_returned")
            except Exception as exc:
                lines.append(f"manual_warning_probe=exception type={type(exc).__name__} message={exc}")
            snapshot(app, "after_manual_warning_dismiss", lines)
        for index in range(12):
            time.sleep(1.0)
            snapshot(app, f"post_login_{index + 1}s", lines)
    except Exception as exc:
        lines.append(f"probe_result=exception type={type(exc).__name__} message={exc}")
        result = 1
    finally:
        try:
            app.close()
        except Exception as exc:
            lines.append(f"close_error={type(exc).__name__}: {exc}")
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
