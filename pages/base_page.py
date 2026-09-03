from __future__ import annotations

import contextlib
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class UnknownDialogError(RuntimeError):
    """Raised when the UI exposes a modal/state without a safe handler."""

    def __init__(self, message: str, log_path: Path, screenshot_path: Path) -> None:
        super().__init__(message)
        self.log_path = log_path
        self.screenshot_path = screenshot_path


def capture_unknown_state(app: Any, context_label: str) -> UnknownDialogError:
    """Capture an unmapped UI state and return an exception ready to raise.

    This function deliberately performs no UI interaction. It only reads the
    control tree and captures the desktop so the state can be mapped manually.
    """
    candidate = _safe_attribute(app, "window")
    target = candidate if candidate is not None and not callable(candidate) else app
    report_root = _report_root(app)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", context_label).strip("._") or "state"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    report_root.mkdir(parents=True, exist_ok=True)
    log_path = report_root / f"unknown_state_{safe_label}_{timestamp}.log"
    screenshot_path = report_root / f"unknown_state_{safe_label}_{timestamp}.png"

    log_parts = [
        f"timestamp={datetime.now().isoformat(timespec='milliseconds')}",
        f"context={context_label}",
        "", 
        "WINDOW:",
        _window_metadata(target),
        "",
        "WINDOW_TEXTS:",
        _control_texts(target),
        "",
        "PRINT_CONTROL_IDENTIFIERS:",
        _control_identifiers(target),
    ]
    log_path.write_text("\n".join(log_parts), encoding="utf-8")
    _capture_full_screen(screenshot_path, log_path, target)

    message = (
        f"Estado nao mapeado encontrado em '{context_label}'. "
        f"Log: {log_path} | Screenshot: {screenshot_path}. "
        "Mapear manualmente antes de prosseguir com seguranca."
    )
    return UnknownDialogError(message, log_path, screenshot_path)


def _report_root(app: Any) -> Path:
    config = _safe_attribute(app, "config")
    configured = _safe_attribute(config, "report_root")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "reports"


def _safe_attribute(value: Any, name: str, default: Any = None) -> Any:
    """Read an object attribute without triggering pywinauto best-match lookup."""
    if value is None:
        return default
    try:
        return object.__getattribute__(value, name)
    except Exception:
        try:
            return getattr(value, name)
        except Exception:
            return default


def _window_metadata(window: Any) -> str:
    if window is None:
        return "<window unavailable>"
    values: list[str] = []
    for name in ("window_text", "class_name", "process_id"):
        try:
            values.append(f"{name}={getattr(window, name)()!r}")
        except Exception as exc:
            values.append(f"{name}=<error: {type(exc).__name__}: {exc}>")
    return "\n".join(values)


def _control_texts(window: Any) -> str:
    if window is None:
        return "<window unavailable>"
    controls = [window]
    try:
        controls.extend(window.descendants())
    except Exception as exc:
        return f"<descendants error: {type(exc).__name__}: {exc}>"

    lines: list[str] = []
    for index, control in enumerate(controls):
        try:
            text_values: Any = control.texts()
        except Exception:
            try:
                text_values = [control.window_text() or ""]
            except Exception as exc:
                lines.append(f"[{index}] <error: {type(exc).__name__}: {exc}>")
                continue
        try:
            class_name = control.class_name()
        except Exception:
            class_name = "<unknown>"
        if class_name == "TEdit":
            text_values = ["<redacted>"]
        lines.append(f"[{index}] class={class_name!r} texts={list(text_values)!r}")
    return "\n".join(lines) or "<no controls>"


def _control_identifiers(window: Any) -> str:
    if window is None:
        return "<window unavailable>"
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            window.print_control_identifiers()
    except Exception as exc:
        output.write(f"<print_control_identifiers error: {type(exc).__name__}: {exc}>\n")
    identifiers = output.getvalue()
    # print_control_identifiers includes TEdit captions/titles. Redact every
    # edit caption rather than trying to infer which field is the password.
    identifiers = re.sub(r"(Edit - ')[^']*(')", r"\1<redacted>\2", identifiers)
    identifiers = re.sub(
        r"(child_window\(title=\")[^\"]*(\", class_name=\"TEdit\")",
        r"\1<redacted>\2",
        identifiers,
    )
    return identifiers.rstrip() or "<no identifiers returned>"


def _capture_full_screen(path: Path, log_path: Path, target: Any = None) -> None:
    try:
        from mss import mss
        from PIL import Image

        with mss() as screen:
            monitor = screen.monitors[0]
            shot = screen.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            _redact_edit_regions(image, target, monitor.get("left", 0), monitor.get("top", 0))
            image.save(path)
            return
    except Exception as first_error:
        try:
            from PIL import ImageGrab

            image = ImageGrab.grab(all_screens=True)
            _redact_edit_regions(image, target, 0, 0)
            image.save(path)
            return
        except Exception as second_error:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(
                    "\nSCREENSHOT_ERROR:\n"
                    f"mss={type(first_error).__name__}: {first_error}\n"
                    f"ImageGrab={type(second_error).__name__}: {second_error}\n"
                )


def _redact_edit_regions(image: Any, target: Any, origin_x: int, origin_y: int) -> None:
    """Mask edit controls in evidence screenshots so credentials never leak."""
    if target is None:
        return
    try:
        from PIL import ImageDraw

        controls = []
        try:
            controls.extend(target.descendants(class_name="TEdit"))
        except Exception:
            pass
        try:
            if target.class_name() == "TEdit":
                controls.append(target)
        except Exception:
            pass
        draw = ImageDraw.Draw(image)
        for control in controls:
            try:
                rect = control.rectangle()
                draw.rectangle(
                    (
                        rect.left - origin_x,
                        rect.top - origin_y,
                        rect.right - origin_x,
                        rect.bottom - origin_y,
                    ),
                    fill="black",
                )
            except Exception:
                continue
    except Exception:
        return
