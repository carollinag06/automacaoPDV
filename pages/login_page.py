from __future__ import annotations

import re
import time
from typing import Any

from core.config import TestConfig
from core.keyboard import press

try:
    from pywinauto import Desktop, findwindows
    from pywinauto.controls.hwndwrapper import InvalidWindowHandle
    from pywinauto.findwindows import ElementNotFoundError
except ImportError:  # Allows static checks without Windows dependencies.
    Desktop = None  # type: ignore[assignment]
    findwindows = None  # type: ignore[assignment]
    InvalidWindowHandle = RuntimeError  # type: ignore[assignment,misc]
    ElementNotFoundError = RuntimeError  # type: ignore[assignment,misc]


class LoginPage:
    """Adapter for the real VCL password form."""

    def __init__(self, root: Any, config: TestConfig) -> None:
        self.root = root
        self.config = config

    def find_dialog(self) -> Any:
        pattern = re.compile(self.config.login_title_regex, re.IGNORECASE)
        candidates = [self.root]
        try:
            candidates += self.root.descendants()
        except Exception:
            pass
        for candidate in candidates:
            try:
                if pattern.search(candidate.window_text() or ""):
                    return candidate
            except Exception:
                continue
        return self.root

    def is_present(self) -> bool:
        candidates = []
        try:
            candidates = self.root.top_level_parent().descendants()
        except Exception:
            candidates = [self.root]
        for candidate in [self.root, *candidates]:
            try:
                if candidate.class_name() == "TFrmPDV":
                    continue
                title = candidate.window_text() or ""
                if re.search(self.config.login_title_regex, title, re.IGNORECASE) and len(self._edits(candidate)) >= 2:
                    return True
            except Exception:
                continue
        return False

    def _edits(self, dialog: Any) -> list[Any]:
        edits = []
        for child in dialog.descendants(class_name="TEdit"):
            try:
                headless_vcl_password_form = dialog.class_name() == "TFrmPassWord"
                if child.is_enabled() and (child.is_visible() or headless_vcl_password_form):
                    edits.append(child)
            except Exception:
                continue
        try:
            edits.sort(key=lambda child: child.rectangle().top)
        except Exception:
            pass
        return edits

    def login(self, attempts: int = 3) -> None:
        """Fill and submit the real VCL login with explicit focus and retries."""
        last_error = "Login nao foi confirmado"
        for _ in range(attempts):
            dialog = self.find_dialog()
            self._dismiss_intercepting_warning()
            edits = self._edits(dialog)
            if len(edits) < 2:
                last_error = "Login nao expos dois TEdit verificaveis"
                time.sleep(0.25)
                continue
            try:
                self._fill_edit(edits[0], self.config.user)
                self._fill_edit(edits[1], self.config.password)
                self._submit(dialog)
            except Exception as exc:
                last_error = f"Falha ao preencher/enviar login: {type(exc).__name__}"
                time.sleep(0.25)
                continue

            warning = self._find_information_modal()
            if warning is not None:
                expired = self._is_expired_warning(warning)
                invalid = self._is_invalid_credentials_warning(warning)
                self._dismiss_warning(warning)
                if expired:
                    return
                if invalid:
                    raise AssertionError("PDV rejeitou a matricula e/ou senha informada")
                raise AssertionError("PDV exibiu um modal de informacao apos o login")
            if self._find_password_dialog() is None:
                return
            last_error = "TFrmPassWord permaneceu aberto apos o submit"
            time.sleep(0.5)
        raise AssertionError(last_error)

    def _fill_edit(self, edit: Any, value: str) -> None:
        """Focus one edit, type sequentially, and fall back to WM_SETTEXT."""
        try:
            self.root.set_focus()
        except Exception:
            pass
        try:
            edit.set_focus()
        except Exception:
            pass
        try:
            edit.type_keys("^{A}", set_foreground=True, pause=0.05)
            edit.type_keys(value, set_foreground=True, pause=0.05, with_spaces=True)
        except Exception:
            pass
        # Keep the physical-key sequence above, then make the final value
        # deterministic for VCL edits that lose focus on a headless desktop.
        edit.set_edit_text(value)
        if self._text_length(edit) != len(value):
            raise AssertionError("Campo de login nao reteve o texto digitado")

    def _submit(self, dialog: Any) -> None:
        for _ in range(8):
            try:
                dialog.set_focus()
            except Exception:
                pass
            press(dialog, "ENTER")
            time.sleep(0.35)
            if self._find_information_modal() is not None or self._find_password_dialog() is None:
                return
        raise AssertionError("ENTER nao confirmou TFrmPassWord")

    def _dismiss_intercepting_warning(self) -> None:
        warning = self._find_information_modal()
        if warning is not None and self._is_expired_warning(warning):
            self._dismiss_warning(warning)

    def _dismiss_warning(self, dialog: Any) -> None:
        for _ in range(3):
            try:
                if not dialog.exists():
                    self._press_enter_fallback(dialog)
                    return
                buttons = [
                    child for child in dialog.descendants(class_name="TBitBtn")
                    if re.search(r"^&?OK$", child.window_text() or "", re.IGNORECASE)
                ]
            except (InvalidWindowHandle, ElementNotFoundError):
                self._press_enter_fallback(dialog)
                return
            if buttons:
                try:
                    buttons[0].click_input()
                except Exception:
                    try:
                        buttons[0].click()
                    except Exception:
                        pass
            try:
                press(dialog, "ENTER")
            except (InvalidWindowHandle, ElementNotFoundError):
                self._press_enter_fallback(dialog)
            time.sleep(0.25)
            if self._find_information_modal() is None:
                return
        raise AssertionError("Nao foi possivel confirmar o aviso de senha antiga")

    def _press_enter_fallback(self, dialog: Any) -> None:
        for target in (dialog, self.root):
            try:
                press(target, "ENTER")
                return
            except (InvalidWindowHandle, ElementNotFoundError):
                continue

    def _find_password_dialog(self) -> Any | None:
        return self._find_window("TFrmPassWord")

    def _find_information_modal(self) -> Any | None:
        return self._find_window("TFrmDlgInformacao")

    def _find_window(self, class_name: str) -> Any | None:
        if Desktop is None or findwindows is None:
            return None
        try:
            pid = self.root.process_id()
            handles = findwindows.find_windows(process=pid, class_name=class_name)
            if handles:
                return Desktop(backend=self.config.backend).window(handle=handles[0])
        except Exception:
            pass
        return None

    def _is_expired_warning(self, dialog: Any) -> bool:
        return re.search(r"senha\s+de\s+acesso\s+antiga\s+detectada", self._information_text(dialog), re.IGNORECASE) is not None

    def _is_invalid_credentials_warning(self, dialog: Any) -> bool:
        return re.search(
            r"matr[ií]cula\s+e/ou\s+senha\s+de\s+acesso\s+inv[aá]lido",
            self._information_text(dialog),
            re.IGNORECASE,
        ) is not None

    @staticmethod
    def _information_text(dialog: Any) -> str:
        texts = [dialog.window_text() or ""]
        try:
            texts.extend(child.window_text() or "" for child in dialog.descendants())
        except Exception:
            pass
        return " ".join(texts)

    @staticmethod
    def _text_length(edit: Any) -> int:
        try:
            return len(edit.window_text() or "")
        except Exception:
            return -1

    def invalid_login(self) -> Any:
        dialog = self.find_dialog()
        edits = self._edits(dialog)
        if len(edits) < 2:
            raise AssertionError("Login nao expos dois TEdit verificaveis")
        if not self.config.user:
            raise AssertionError("Teste invalido exige PDV_USER real; nenhum usuario ficticio sera criado")
        self._fill_edit(edits[0], self.config.user)
        self._fill_edit(edits[1], "")
        self._submit(dialog)
        warning = self._find_information_modal()
        if warning is not None:
            self._dismiss_warning(warning)
        return dialog
