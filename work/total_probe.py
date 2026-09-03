from __future__ import annotations

import ctypes
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import ImageGrab
from pywinauto import Desktop, findwindows

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app import PdvApplication
from core.app import WindowNotFound
from core.config import load_config
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


def safe_call(function, default=""):
    try:
        return function()
    except Exception as exc:
        return f"<error {type(exc).__name__}: {exc}>"


def control_record(control):
    rectangle = safe_call(control.rectangle, None)
    if rectangle is None or isinstance(rectangle, str):
        bounds = "<unavailable>"
    else:
        bounds = f"({rectangle.left},{rectangle.top},{rectangle.right},{rectangle.bottom})"
    return {
        "class": safe_call(control.class_name),
        "text": safe_call(control.window_text),
        "texts": safe_call(control.texts),
        "bounds": bounds,
        "handle": safe_call(lambda: control.handle, None),
    }


def wm_gettext(handle: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(max(length + 1, 256))
    user32.GetWindowTextW(handle, buffer, len(buffer))
    return buffer.value


def main() -> int:
    root = ROOT
    config = load_config(root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"total_probe_{timestamp}.txt"
    panel_image_path = report_dir / f"total_probe_panel_{timestamp}.png"
    app = PdvApplication(config)
    lines: list[str] = [f"timestamp={timestamp}"]
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        login = LoginPage(login_dialog or app.window, config)
        login.login()
        try:
            app.wait_until_ready(config.start_timeout)
        except WindowNotFound as exc:
            # The diagnostic must still inspect the rendered total if this
            # build leaves a known password form alive over an already-ready
            # main form. Record that deviation; production tests still use
            # the strict PDV_READY gate.
            lines.append(f"READY_GATE={exc}")
            main = app._find_form("TFrmPDV")
            if main is None or not main.is_visible() or not main.is_enabled():
                raise
            app.window = main
        page = PdvPage(app.window, config.action_timeout)
        page.insert_product(config.product_code or "1")

        panels = []
        for panel in app.window.descendants(class_name="TPanel"):
            try:
                if panel.is_visible():
                    panels.append(panel)
            except Exception:
                continue
        lines.append("[1] WIN32 PANELS")
        for index, panel in enumerate(panels):
            record = control_record(panel)
            lines.append(f"panel[{index}]={record!r}")

        candidate = None
        candidate_top = -1
        for panel in panels:
            rectangle = safe_call(panel.rectangle, None)
            if rectangle is None or isinstance(rectangle, str):
                continue
            height = max(0, rectangle.height())
            width = max(0, rectangle.width())
            # pnlValorTotalAPagar is the bottom 47px panel inside the
            # left-side pnlTotalizadores. Exclude the full-width footer and
            # select the lowest compact panel, not the largest ancestor.
            if (
                rectangle.left < 800
                and rectangle.top > 850
                and 40 <= height <= 120
                and 300 <= width <= 800
                and rectangle.top > candidate_top
            ):
                candidate = panel
                candidate_top = rectangle.top
        if candidate is None:
            raise AssertionError("pnlValorTotalAPagar nao foi localizado por geometria")

        lines.append("[1] SELECTED_PANEL")
        lines.append(f"selected={control_record(candidate)!r}")
        lines.append(f"panel_window_text={safe_call(candidate.window_text)!r}")
        lines.append(f"panel_texts={safe_call(candidate.texts)!r}")
        lines.append(f"panel_descendants={[control_record(child) for child in candidate.descendants()]!r}")

        handle = candidate.handle
        lines.append("[2] WM_GETTEXT")
        lines.append(f"win32_get_window_text={safe_call(lambda: __import__('win32gui').GetWindowText(handle))!r}")
        lines.append(f"user32_get_window_text={wm_gettext(handle)!r}")

        lines.append("[3] UIA")
        uia = Desktop(backend="uia").window(handle=app.window.handle)
        uia_records = []
        for control in uia.descendants():
            text = safe_call(control.window_text)
            if re.search(r"total|1,00|valor", text, re.IGNORECASE):
                uia_records.append(control_record(control))
        lines.append(f"matching_controls={uia_records!r}")
        uia_panel = Desktop(backend="uia").window(handle=candidate.handle)
        lines.append(f"panel_matching_controls={[control_record(control) for control in uia_panel.descendants()]!r}")

        lines.append("[4] OCR")
        rectangle = candidate.rectangle()
        ImageGrab.grab(
            bbox=(rectangle.left, rectangle.top, rectangle.right, rectangle.bottom),
            all_screens=True,
        ).save(panel_image_path)
        try:
            import pytesseract

            for candidate in (
                Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
            ):
                if candidate.exists():
                    pytesseract.pytesseract.tesseract_cmd = str(candidate)
                    break

            ocr_text = pytesseract.image_to_string(
                ImageGrab.grab(
                    bbox=(rectangle.left, rectangle.top, rectangle.right, rectangle.bottom),
                    all_screens=True,
                ),
                config="--psm 6",
            )
            lines.append(f"ocr_text={ocr_text!r}")
        except Exception as exc:
            lines.append(f"ocr_error={type(exc).__name__}: {exc}")
        lines.append(f"panel_screenshot={panel_image_path}")
        return_code = 0
    except Exception as exc:
        lines.append(f"ERROR={type(exc).__name__}: {exc}")
        return_code = 1
    finally:
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            app.close()
        except Exception as exc:
            lines.append(f"CLOSE_ERROR={type(exc).__name__}: {exc}")
            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path)
    print(panel_image_path if panel_image_path.exists() else "<no panel screenshot>")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
