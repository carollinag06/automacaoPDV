from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import TestConfig
from .keyboard import press

try:
    from pywinauto import Desktop, findwindows
    from pywinauto.application import Application
except ImportError:  # Allows static checks without installing Windows dependencies.
    Desktop = None  # type: ignore[assignment]
    findwindows = None  # type: ignore[assignment]
    Application = None  # type: ignore[assignment]


class AutomationUnavailable(RuntimeError):
    pass


class WindowNotFound(TimeoutError):
    pass


@dataclass
class PdvApplication:
    config: TestConfig
    process: Any | None = None
    window: Any | None = None

    def start(self) -> Any:
        if Application is None or Desktop is None:
            raise AutomationUnavailable("pywinauto não está instalado")
        if not self.config.exe_path.exists():
            raise FileNotFoundError(self.config.exe_path)
        self.process = Application(backend=self.config.backend).start(
            str(self.config.exe_path),
            work_dir=str(self.config.exe_path.parent),
        )
        self.window = self._wait_for_start_form(self.config.start_timeout)
        return self.window

    def attach(self) -> Any:
        if Desktop is None:
            raise AutomationUnavailable("pywinauto não está instalado")
        self.window = self.wait_for_window(self.config.window_title_regex, self.config.action_timeout)
        return self.window

    def reveal_login_dialog(self) -> Any | None:
        """Click the real closed-cashier screen to open the PDV password form."""
        if Desktop is None:
            raise AutomationUnavailable("pywinauto nao esta instalado")

        dialog = self._find_form("TFrmPassWord")
        if dialog is not None:
            return dialog

        closed_cashier = self._wait_for_cashier_ready(self.config.start_timeout)
        if closed_cashier is None:
            return None

        for _ in range(3):
            self._activate_cashier(closed_cashier)
            dialog = self._find_form("TFrmPassWord", timeout=min(3.0, self.config.action_timeout))
            if dialog is not None:
                return dialog
            self._send_cashier_key(closed_cashier, "ENTER")
            dialog = self._find_form("TFrmPassWord", timeout=min(2.0, self.config.action_timeout))
            if dialog is not None:
                return dialog
            self._send_cashier_key(closed_cashier, "SPACE")
            dialog = self._find_form("TFrmPassWord", timeout=min(2.0, self.config.action_timeout))
            if dialog is not None:
                return dialog
        return None

    def dismiss_recovery_prompt(self, allow_recovery: bool = False, timeout: float = 2.0) -> bool:
        """Declines pending-sale recovery unless the test explicitly covers it."""
        if allow_recovery:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for dialog in self._windows():
                if self._is_recovery_prompt(dialog):
                    try:
                        if not dialog.is_visible() or not dialog.is_enabled():
                            continue
                    except Exception:
                        continue
                    if self._click_no(dialog):
                        return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _is_recovery_prompt(dialog: Any) -> bool:
        try:
            texts = [dialog.window_text() or ""]
            texts.extend(child.window_text() or "" for child in dialog.descendants())
            message = " ".join(texts)
            return re.search(
                r"recuperar\s+(?:(?:uma|a)\s+)?venda|venda\s+(?:aberta|pendente)",
                message,
                re.IGNORECASE,
            ) is not None
        except Exception:
            return False

    @staticmethod
    def _click_no(dialog: Any) -> bool:
        for class_name in ("TBitBtn", "TButton"):
            for button in dialog.descendants(class_name=class_name):
                try:
                    if button.is_visible() and button.is_enabled() and re.search(
                        r"^&?N(?:Ã£o|ao)$|^N(?:Ã£o|ao)$", button.window_text() or "", re.IGNORECASE
                    ):
                        try:
                            button.click_input()
                            return True
                        except Exception:
                            try:
                                button.click()
                                return True
                            except Exception:
                                try:
                                    press(button, "ENTER")
                                    return True
                                except Exception:
                                    pass
                except Exception:
                    continue
        try:
            if not dialog.is_enabled():
                return False
            dialog.set_focus()
            press(dialog, "ESC")
            return True
        except Exception:
            pass
        try:
            if not dialog.is_enabled():
                return False
            dialog.set_focus()
            dialog.type_keys("n", set_foreground=True)
            return True
        except Exception:
            return False

    def _wait_for_cashier_ready(self, timeout: float) -> Any | None:
        """Wait for the actual cashier screen, without depending on TFrmAbout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cashier = self._find_form("TFrmPDVCaixaFechado")
            if cashier is not None:
                try:
                    if cashier.is_visible() and cashier.is_enabled():
                        return cashier
                except Exception:
                    pass
            time.sleep(0.25)
        return None

    def _activate_cashier(self, cashier: Any) -> None:
        try:
            cashier.restore()
        except Exception:
            pass
        try:
            cashier.set_focus()
        except Exception:
            pass
        try:
            cashier.click_input()
        except Exception:
            pass

    def _send_cashier_key(self, cashier: Any, key: str) -> None:
        try:
            press(cashier, key)
        except Exception:
            pass

    def _wait_for_start_form(self, timeout: float) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            main = self._find_form("TFrmPDV", timeout=0.1)
            if main is not None:
                return main
            closed_cashier = self._find_form("TFrmPDVCaixaFechado", timeout=0.1)
            if closed_cashier is not None:
                return closed_cashier
            time.sleep(0.1)
        raise WindowNotFound("SATPDV nao abriu TFrmPDV nem TFrmPDVCaixaFechado")

    def _find_form(self, class_name: str | None = None, title_regex: str | None = None, timeout: float = 0.1) -> Any | None:
        deadline = time.monotonic() + timeout
        pattern = re.compile(title_regex, re.IGNORECASE) if title_regex else None
        while time.monotonic() < deadline:
            for window in self._windows():
                try:
                    if class_name and window.class_name() != class_name:
                        continue
                    if pattern and not pattern.search(window.window_text() or ""):
                        continue
                    return window
                except Exception:
                    continue
            time.sleep(0.05)
        return None

    def _windows(self) -> list[Any]:
        if self.process is not None:
            try:
                pid = getattr(self.process, "process", None)
                if callable(pid):
                    pid = pid()
                handles = findwindows.find_windows(process=pid)
                return [Desktop(backend=self.config.backend).window(handle=handle) for handle in handles]
            except Exception:
                pass
        if Desktop is None:
            return []
        return Desktop(backend=self.config.backend).windows()

    def wait_for_window(self, title_regex: str, timeout: float) -> Any:
        if Desktop is None:
            raise AutomationUnavailable("pywinauto não está instalado")
        deadline = time.monotonic() + timeout
        pattern = re.compile(title_regex, re.IGNORECASE)
        while time.monotonic() < deadline:
            candidates = self._windows()
            if self.process is not None:
                pid = getattr(self.process, "process", None)
                if callable(pid):
                    pid = pid()
                candidates = [window for window in candidates if _safe_process_id(window) == pid]
            for window in candidates:
                try:
                    text = " ".join((window.window_text() or "", window.class_name() or ""))
                    if pattern.search(text):
                        return window
                except Exception:
                    continue
            time.sleep(0.25)
        raise WindowNotFound(f"Janela não encontrada: /{title_regex}/")

    def wait_until_ready(self, timeout: float, allow_recovery: bool = False) -> Any:
        """Return the main PDV form after the password dialog has closed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            main = self._find_form("TFrmPDV")
            password = self._find_form("TFrmPassWord")
            information = self._find_form("TFrmDlgInformacao")
            if information is not None and self._is_expired_password_warning(information):
                self._dismiss_information(information)
                time.sleep(0.2)
                continue
            if self.dismiss_recovery_prompt(allow_recovery=allow_recovery, timeout=0.1):
                time.sleep(0.1)
                continue
            if information is not None and password is not None:
                raise WindowNotFound("PDV exibiu TFrmDlgInformacao apos o login; PDV_READY nao foi alcancado")
            if main is not None and password is None:
                try:
                    if main.is_visible() and main.is_enabled():
                        self.window = main
                        return main
                except Exception:
                    pass
            time.sleep(0.1)
        raise WindowNotFound("PDV_READY nao foi observado: TFrmPassWord ainda esta aberto")

    def _is_expired_password_warning(self, dialog: Any) -> bool:
        texts: list[str] = []
        try:
            texts.append(dialog.window_text() or "")
            texts.extend(child.window_text() or "" for child in dialog.descendants())
        except Exception:
            pass
        message = " ".join(texts)
        return re.search(r"senha\s+de\s+acesso\s+antiga\s+detectada", message, re.IGNORECASE) is not None

    def _dismiss_information(self, dialog: Any) -> None:
        try:
            press(dialog, "ENTER")
            return
        except Exception:
            pass
        for button in dialog.descendants(class_name="TBitBtn"):
            try:
                if re.search(r"^&?OK$", button.window_text() or "", re.IGNORECASE):
                    button.click()
                    return
            except Exception:
                continue

    def child_windows(self) -> list[Any]:
        if self.window is None:
            return []
        try:
            return self.window.descendants()
        except Exception:
            return []

    def close(self) -> None:
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                try:
                    self.window.send_keystrokes("{ESC}")
                except Exception:
                    pass
        self.window = None
        self.process = None

    def diagnostics(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for child in self.child_windows():
            try:
                rows.append({
                    "title": child.window_text(),
                    "class_name": child.class_name(),
                    "control_type": getattr(child.element_info, "control_type", ""),
                    "automation_id": getattr(child.element_info, "automation_id", ""),
                })
            except Exception:
                continue
        return rows

    def top_level_diagnostics(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for window in self._windows():
            try:
                messages = []
                for child in window.descendants():
                    if child.class_name() == "TEdit":
                        continue
                    text = child.window_text() or ""
                    if text.strip():
                        messages.append(text)
                rows.append({
                    "title": window.window_text(),
                    "class_name": window.class_name(),
                    "visible": window.is_visible(),
                    "enabled": window.is_enabled(),
                    "messages": list(dict.fromkeys(messages)),
                })
            except Exception:
                continue
        return rows


def _safe_process_id(window: Any) -> int | None:
    try:
        return window.process_id()
    except Exception:
        return None
