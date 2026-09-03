from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.app import PdvApplication
from core.config import load_config
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage
from core.keyboard import hotkey


def safe(call, default=""):
    try:
        return call()
    except Exception as exc:
        return f"<error {type(exc).__name__}: {exc}>"


def main() -> int:
    config = load_config(ROOT)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = ROOT / "reports" / f"price_probe_{stamp}.txt"
    screenshot = ROOT / "reports" / f"price_probe_{stamp}.png"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"timestamp={stamp}", "scope=INI-08/INI-09 consulta de preco e estoque"]
    app = PdvApplication(config)
    try:
        app.start()
        app.dismiss_recovery_prompt(timeout=config.action_timeout)
        login_dialog = app.reveal_login_dialog()
        LoginPage(login_dialog or app.window, config).login()
        app.wait_until_ready(config.start_timeout)
        page = PdvPage(app.window, config.action_timeout)
        page.window.restore()
        page.window.set_focus()
        page.window.click_input()
        page.window.type_keys("{F1}", set_foreground=True)
        try:
            import pyautogui
            pyautogui.press("f1")
        except Exception as exc:
            lines.append(f"physical_f1_error={type(exc).__name__}: {exc}")
        time.sleep(0.6)
        lines.append(f"after_f1_windows={[ (safe(w.class_name), safe(w.window_text), safe(w.is_visible), safe(w.is_enabled)) for w in page._top_level_windows()]!r}")
        grids = page.window.descendants(class_name="TDBGrid")
        lines.append(f"after_f1_grids={[ (safe(g.window_text), safe(g.is_visible), safe(g.is_enabled), safe(g.rectangle)) for g in grids]!r}")
        lines.append(f"after_f1_panels={[ (safe(p.window_text), safe(p.is_visible), safe(p.is_enabled), safe(p.rectangle)) for p in page.window.descendants(class_name='TPanel') if safe(p.is_visible)]!r}")
        try:
            candidate = page.window.child_window(best_match="Panel1", class_name="TPanel")
            lines.append(f"panel1_lookup={(safe(candidate.exists), safe(candidate.is_visible), safe(candidate.rectangle), safe(candidate.window_text))!r}")
        except Exception as exc:
            lines.append(f"panel1_lookup_error={type(exc).__name__}: {exc}")
        edit = page.product_edit
        edit.set_focus()
        edit.set_edit_text("4")
        edit.type_keys("{ENTER}", set_foreground=True)
        time.sleep(1.0)
        lines.append(f"after_product_windows={[ (safe(w.class_name), safe(w.window_text), safe(w.is_visible), safe(w.is_enabled)) for w in page._top_level_windows()]!r}")
        lines.append(f"after_product_pdV_text={safe(page.window.window_text)!r}")
        lines.append(f"after_product_controls={[ (safe(c.class_name), safe(c.window_text), safe(c.texts)) for c in page.window.descendants() if safe(c.is_visible) and safe(c.window_text)]!r}")
        lines.append(f"after_product_labels={[safe(c.window_text) for c in page.window.descendants(class_name='TLabel') if safe(c.is_visible)]!r}")
        ImageGrab.grab(all_screens=True).save(screenshot)
        lines.append(f"screenshot={screenshot}")
        edit = page.product_edit
        hotkey(edit, "CTRL", "E")
        try:
            candidate = page.window.child_window(best_match="Panel1", class_name="TPanel")
            candidate.click_input()
        except Exception as exc:
            lines.append(f"panel1_click_error={type(exc).__name__}: {exc}")
        try:
            import pyautogui
            pyautogui.hotkey("ctrl", "e")
        except Exception as exc:
            lines.append(f"physical_ctrl_e_error={type(exc).__name__}: {exc}")
        time.sleep(0.8)
        lines.append(f"after_ctrl_e_modal={safe(page.active_modal_text)!r}")
        lines.append(f"after_ctrl_e_windows={[ (safe(w.class_name), safe(w.window_text), safe(w.is_visible), safe(w.is_enabled)) for w in page._top_level_windows()]!r}")
        lines.append(f"after_ctrl_e_grids={[ (safe(g.window_text), safe(g.is_visible), safe(g.is_enabled), safe(g.rectangle)) for g in page.window.descendants(class_name='TDBGrid')]!r}")
        for grid in page.window.descendants(class_name="TDBGrid"):
            lines.append(f"grid_text={safe(grid.texts)!r}")
            lines.append(f"grid_children={[ (safe(c.class_name), safe(c.window_text), safe(c.texts)) for c in grid.descendants()]!r}")
        ImageGrab.grab(all_screens=True).save(ROOT / "reports" / f"price_probe_{stamp}_stock.png")
        lines.append("result=PASS: estados reais de consulta registrados")
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
