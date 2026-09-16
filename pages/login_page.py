from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

from core.config import TestConfig
from core.keyboard import press
from pages.base_page import capture_unknown_state

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
        # TFrmPassWord can be destroyed immediately after submit. Keep the
        # process id before that happens so later polling can still discover
        # the delayed warning and the new TFrmPDV/TFrmPassWord wrappers.
        try:
            self._process_id = root.process_id()
        except Exception:
            self._process_id = None

    @staticmethod
    def _observe_dialog(dialog: Any, context_label: str) -> None:
        """Capture complete dialog text before a known login interaction."""
        try:
            from core.test_results import record_dialog_observation

            record_dialog_observation(dialog, context_label)
        except Exception:
            # Logging is non-blocking and must not alter authentication behavior.
            pass

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

    def is_present(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
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
            time.sleep(0.1)
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

    @staticmethod
    def _window_snapshot(window: Any) -> tuple[Any, ...] | None:
        """Return a repaint/focus-stability signature without reading secrets."""
        try:
            handle = getattr(window, "handle", None)
            if callable(handle):
                handle = handle()
            rect = window.rectangle()
            return (
                handle,
                window.class_name(),
                rect.left,
                rect.top,
                rect.right,
                rect.bottom,
                bool(window.is_visible()),
                bool(window.is_enabled()),
            )
        except (InvalidWindowHandle, ElementNotFoundError):
            return None
        except Exception:
            return None

    def _wait_for_stable_window(
        self,
        window: Any,
        *,
        timeout: float = 1.2,
        checks: int = 3,
        interval: float = 0.1,
    ) -> bool:
        """Wait until one VCL wrapper keeps the same handle/geometry/state."""
        deadline = time.monotonic() + timeout
        previous: tuple[Any, ...] | None = None
        stable_count = 0
        while time.monotonic() < deadline:
            current = self._window_snapshot(window)
            if current is None:
                previous = None
                stable_count = 0
            elif current == previous:
                stable_count += 1
                if stable_count >= checks:
                    return True
            else:
                previous = current
                stable_count = 1
            time.sleep(interval)
        return False

    def _wait_for_stable_form(
        self,
        class_name: str,
        *,
        timeout: float = 2.5,
        checks: int = 3,
        interval: float = 0.1,
    ) -> Any | None:
        """Rediscover a top-level form by PID and return it after stabilization."""
        deadline = time.monotonic() + timeout
        previous: tuple[Any, ...] | None = None
        stable_count = 0
        last_candidate = None
        while time.monotonic() < deadline:
            candidate = self._find_window(class_name)
            snapshot = self._window_snapshot(candidate) if candidate is not None else None
            if snapshot is None or not self._is_active(candidate):
                previous = None
                stable_count = 0
            elif snapshot == previous:
                stable_count += 1
                last_candidate = candidate
                if stable_count >= checks:
                    return candidate
            else:
                previous = snapshot
                stable_count = 1
                last_candidate = candidate
            time.sleep(interval)
        return last_candidate if stable_count >= checks else None

    def _wait_for_information_modal(self, timeout: float = 2.5) -> Any | None:
        """Wait for a stable information modal, including delayed VCL text."""
        deadline = time.monotonic() + timeout
        last_stable = None
        while time.monotonic() < deadline:
            dialog = self._find_information_modal()
            if dialog is not None and self._wait_for_stable_window(dialog, timeout=0.8):
                last_stable = dialog
                if self._information_text(dialog).strip():
                    return dialog
            time.sleep(0.1)
        return last_stable

    def _wait_for_submit_transition(self, timeout: float = 2.5) -> bool:
        """Wait for the post-submit modal/main/password state to settle."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            information = self._find_information_modal()
            if information is not None and self._wait_for_stable_window(information, timeout=0.8):
                return True
            if self._find_password_dialog() is None:
                return True
            main = self._find_window("TFrmPDV")
            if main is not None and self._is_main_interactable(main):
                return True
            time.sleep(0.1)
        return False

    def login(
        self,
        attempts: int = 3,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        """Fill and submit the real VCL login with explicit focus and retries."""
        login_user = self.config.user if user is None else user
        login_password = self.config.password if password is None else password
        last_error = "Login nao foi confirmado"
        for _ in range(attempts):
            dialog = self._wait_for_stable_form("TFrmPassWord") or self.find_dialog()
            self._wait_for_stable_window(dialog, timeout=0.8)
            self._dismiss_intercepting_warning()
            edits = self._edits(dialog)
            if len(edits) < 2:
                last_error = "Login nao expos dois TEdit verificaveis"
                time.sleep(0.25)
                continue
            try:
                self._fill_edit(edits[0], login_user)
                self._fill_edit(edits[1], login_password)
                self._submit(dialog)
            except Exception as exc:
                last_error = f"Falha ao preencher/enviar login: {type(exc).__name__}"
                time.sleep(0.25)
                continue

            warning = self._wait_for_information_modal(timeout=2.5)
            if warning is not None:
                warning_text = self._information_text(warning)
                if not warning_text:
                    time.sleep(0.2)
                    continue
                expired = self._is_expired_warning(warning)
                invalid = self._is_invalid_credentials_warning(warning)
                recovery = self._is_recovery_prompt(warning)
                if recovery:
                    self._click_no(warning)
                    if self._wait_until_authenticated(timeout=8.0):
                        return
                    if self._find_password_dialog() is None:
                        return
                    last_error = "Recuperacao de venda recusada, mas TFrmPassWord ainda ativo"
                    time.sleep(0.25)
                    continue
                if not expired and not invalid:
                    raise capture_unknown_state(warning, "login_post_auth_modal")
                self._dismiss_warning(warning)
                if expired:
                    if self._close_password_if_main_ready():
                        return
                    if self._wait_until_authenticated(timeout=8.0):
                        return
                    # Some SATPDV builds keep TFrmPassWord visible after the
                    # legacy-password warning and require a second submit.
                    if self._find_password_dialog() is None:
                        return
                    last_error = "Aviso de senha antiga dispensado; TFrmPassWord ainda ativo"
                    time.sleep(0.35)
                    continue
                if invalid:
                    raise AssertionError("PDV rejeitou a matricula e/ou senha informada")
                raise AssertionError("PDV exibiu um modal de informacao apos o login")
            if self._wait_until_authenticated(timeout=5.0):
                self._dismiss_residual_password()
                return
            if self._find_password_dialog() is None:
                return
            last_error = "TFrmPassWord permaneceu aberto apos o submit"
            time.sleep(0.5)
        raise capture_unknown_state(self.root, "login_state_unresolved")

    def blank_password_login(self) -> str:
        """Submit the real operator with a blank password (INI-01)."""
        dialog = self._wait_for_stable_form("TFrmPassWord") or self.find_dialog()
        self._wait_for_stable_window(dialog, timeout=0.8)
        edits = self._edits(dialog)
        if len(edits) < 2:
            raise AssertionError("INI-01: TFrmPassWord nao expos os dois TEdit reais")
        if not self.config.user:
            raise AssertionError("INI-01 exige PDV_USER real; nenhum usuario ficticio sera criado")
        self._fill_edit(edits[0], self.config.user)
        self._fill_edit(edits[1], "")
        self._submit(dialog)
        warning = self._wait_for_information_modal(timeout=3.0)
        if warning is None:
            raise AssertionError("INI-01: nenhum modal de credencial invalida foi exibido")
        text = self._information_text(warning)
        if not self._is_invalid_credentials_warning(warning):
            raise capture_unknown_state(warning, "ini01_blank_password_modal")
        self._dismiss_warning(warning)
        return text

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
            self._wait_for_stable_window(dialog, timeout=0.8)
            self._observe_dialog(dialog, "before_submit_login")
            try:
                dialog.set_focus()
            except Exception:
                pass
            if self._click_submit_button(dialog):
                time.sleep(0.35)
                if self._wait_for_submit_transition(timeout=2.5):
                    return
            press(dialog, "ENTER")
            time.sleep(0.35)
            if self._wait_for_submit_transition(timeout=2.5):
                return
        raise AssertionError("ENTER nao confirmou TFrmPassWord")

    def _close_password_if_main_ready(self) -> bool:
        main = self._find_window("TFrmPDV")
        password = self._find_password_dialog()
        if main is None or password is None:
            return password is None
        try:
            if not main.is_visible() or not main.is_enabled():
                return False
            password.set_focus()
            press(password, "ESC")
        except (InvalidWindowHandle, ElementNotFoundError):
            return True
        except Exception:
            return False
        time.sleep(0.35)
        current = self._find_password_dialog()
        if current is None:
            return True
        try:
            if not current.is_visible() or not current.is_enabled():
                return True
            # The successful authentication path may leave a non-modal VCL
            # password form alive. Main PDV is already ready, so closing this
            # known residual form is safe and prevents the next test from
            # seeing a false login state.
            current.close()
            time.sleep(0.2)
            return self._find_password_dialog() is None
        except Exception:
            return True

    def _wait_until_authenticated(self, timeout: float) -> bool:
        """Wait for a functional TFrmPDV, tolerating a residual password form.

        A successful SATPDV login can leave TFrmPassWord painted on screen
        while TFrmPDV is already enabled and accepting focus. That form is
        not treated as a blocker by itself; only a different active/visible
        top-level dialog prevents authentication from being accepted.
        """
        deadline = time.monotonic() + timeout
        ready_since: float | None = None
        while time.monotonic() < deadline:
            warning = self._find_information_modal()
            if warning is not None:
                if not self._wait_for_stable_window(warning, timeout=0.8):
                    time.sleep(0.1)
                    continue
                if self._is_expired_warning(warning):
                    self._dismiss_warning(warning)
                    ready_since = None
                    time.sleep(0.15)
                    continue
                return False
            main = self._find_window("TFrmPDV")
            # Do not accept a visible TFrmPDV when another modal is active.
            # TFrmPassWord is explicitly excluded because this build can
            # leave it visible after authentication without disabling PDV.
            if self._find_blocking_dialog() is not None:
                return False
            if main is not None and self._is_main_interactable(main):
                stable_main = self._wait_for_stable_form("TFrmPDV", timeout=0.6)
                if stable_main is None or not self._is_main_interactable(stable_main):
                    ready_since = None
                    time.sleep(0.1)
                    continue
                if ready_since is None:
                    ready_since = time.monotonic()
                if time.monotonic() - ready_since >= 0.8:
                    return True
            else:
                ready_since = None

            password = self._find_password_dialog()
            if password is None:
                time.sleep(0.1)
                continue
            # TFrmPDV is not ready yet. Only in this branch is it safe to
            # retry the known password form; a residual form is never
            # interacted with once the main window is functional.
            ready_since = None
            if main is not None and self._is_active(main):
                if self._click_submit_button(password):
                    time.sleep(0.35)
                    if self._find_password_dialog() is None:
                        continue
                try:
                    password.set_focus()
                    press(password, "ESC")
                    time.sleep(0.25)
                    if self._find_password_dialog() is None:
                        return True
                except (InvalidWindowHandle, ElementNotFoundError):
                    return True
                except Exception:
                    pass
            time.sleep(0.15)
        main = self._find_window("TFrmPDV")
        return main is not None and self._is_main_interactable(main) and self._find_blocking_dialog() is None

    def _dismiss_residual_password(self) -> None:
        """Best-effort cleanup after success; never affects login outcome."""
        try:
            main = self._find_window("TFrmPDV")
            password = self._find_password_dialog()
            if main is None or password is None or not self._is_main_interactable(main):
                return
            # Closing is deliberately best-effort. Some VCL builds keep the
            # wrapper alive and reject Close; the authenticated state remains
            # valid either way.
            try:
                password.close()
            except Exception:
                pass
        except Exception:
            return

    @staticmethod
    def _click_submit_button(dialog: Any) -> bool:
        """Click the real VCL confirmation button before using ENTER as fallback."""
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except (InvalidWindowHandle, ElementNotFoundError):
                return False
            for button in buttons:
                try:
                    caption = button.window_text() or ""
                    if not re.search(r"^&?(?:OK|Entrar|Acessar|Confirmar)$", caption, re.IGNORECASE):
                        continue
                    if not button.is_visible() or not button.is_enabled():
                        continue
                    try:
                        button.click_input()
                    except Exception:
                        button.click()
                    return True
                except (InvalidWindowHandle, ElementNotFoundError):
                    continue
                except Exception:
                    continue
        return False

    def _dismiss_intercepting_warning(self) -> None:
        warning = self._wait_for_information_modal(timeout=1.0)
        if warning is not None and self._is_expired_warning(warning):
            self._dismiss_warning(warning)

    @staticmethod
    def _is_recovery_prompt(dialog: Any) -> bool:
        return re.search(
            r"recuperar\s+(?:(?:uma|a)\s+)?venda|"
            r"venda\s+(?:aberta|pendente)|"
            r"venda\s+em\s+processo\s+de\s+finaliza",
            LoginPage._information_text(dialog),
            re.IGNORECASE,
        ) is not None

    @staticmethod
    def _click_no(dialog: Any) -> None:
        try:
            from core.test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_dismiss_login_recovery")
        except Exception:
            pass
        def closed() -> bool:
            try:
                return not dialog.exists() or not dialog.is_visible()
            except (InvalidWindowHandle, ElementNotFoundError):
                return True

        def wait_closed(timeout: float = 1.5) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if closed():
                    return True
                time.sleep(0.1)
            return closed()

        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except (InvalidWindowHandle, ElementNotFoundError):
                continue
            for button in buttons:
                try:
                    caption = unicodedata.normalize(
                        "NFKD", button.window_text() or ""
                    ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                    if caption != "nao" or not button.is_visible() or not button.is_enabled():
                        continue
                    try:
                        button.click_input()
                    except Exception:
                        try:
                            button.click()
                        except Exception:
                            button.set_focus()
                            press(button, "ENTER")
                    if wait_closed():
                        return
                    # click_input/click can report success while the VCL
                    # handler is still pending. Retry the same known No
                    # button and require the modal to disappear.
                    try:
                        button.set_focus()
                        press(button, "ENTER")
                    except (InvalidWindowHandle, ElementNotFoundError):
                        if closed():
                            return
                        continue
                    if wait_closed():
                        return
                    try:
                        # O fluxo normal não recupera a venda. Se o botão
                        # Não não for processado, ESC é o cancelamento seguro
                        # do mesmo modal conhecido.
                        dialog.set_focus()
                        press(dialog, "ESC")
                    except (InvalidWindowHandle, ElementNotFoundError):
                        if closed():
                            return
                        continue
                    if wait_closed():
                        return
                except (InvalidWindowHandle, ElementNotFoundError):
                    continue
        raise AssertionError(
            "Modal de recuperacao nao foi fechado pelo botao Nao; "
            "o clique nao foi considerado concluido sem desaparecer a janela"
        )

    def _dismiss_warning(self, dialog: Any) -> None:
        self._observe_dialog(dialog, "before_dismiss_login_warning")
        for _ in range(3):
            self._wait_for_stable_window(dialog, timeout=0.8)
            try:
                # ``DialogWrapper`` from the configured pywinauto runtime may
                # not expose ``exists()``. Use it when available and fall back
                # to the VCL wrapper's visibility check; both paths preserve
                # the invalid-handle fallback below.
                exists_method = getattr(dialog, "exists", None)
                exists = (
                    bool(exists_method())
                    if callable(exists_method)
                    else bool(dialog.is_visible())
                )
                if not exists:
                    self._press_enter_fallback(dialog)
                    return
                buttons = []
                for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
                    buttons.extend(
                        child for child in dialog.descendants(class_name=class_name)
                        if re.search(r"^&?OK$", child.window_text() or "", re.IGNORECASE)
                    )
            except (InvalidWindowHandle, ElementNotFoundError):
                self._press_enter_fallback(dialog)
                return
            if buttons:
                try:
                    button = buttons[0]
                    # VCL TBitBtn handlers are more reliable when the
                    # focused control receives Enter first; retain physical
                    # click and BM_CLICK-style click as fallbacks for builds
                    # where the default button is not assigned.
                    button.set_focus()
                    press(button, "ENTER")
                except Exception:
                    pass
                time.sleep(0.2)
                if not self._is_active(dialog):
                    return
                try:
                    button.click_input()
                except Exception:
                    try:
                        button.click()
                    except Exception:
                        pass
            try:
                press(dialog, "ENTER")
            except Exception:
                self._press_enter_fallback(dialog)
            time.sleep(0.25)
            if not self._is_active(dialog):
                return
        raise AssertionError("Nao foi possivel confirmar o aviso de senha antiga")

    def _press_enter_fallback(self, dialog: Any) -> None:
        for target in (dialog, self.root):
            try:
                # Envio direto ao HWND não depende de desktop interativo nem
                # de SetForegroundWindow; isso permite concluir o aviso de
                # senha em execuções desacopladas da área de trabalho.
                target.send_keystrokes("{ENTER}")
                return
            except Exception:
                try:
                    press(target, "ENTER")
                    return
                except Exception:
                    continue

    def _find_password_dialog(self) -> Any | None:
        dialog = self._find_window("TFrmPassWord")
        return dialog if dialog is not None and self._is_active(dialog) else None

    def _find_information_modal(self) -> Any | None:
        for dialog in self._find_windows("TFrmDlgInformacao"):
            if self._is_active(dialog):
                return dialog
        return None

    def _find_blocking_dialog(self) -> Any | None:
        """Return an active top-level dialog other than known login residue."""
        if Desktop is None or findwindows is None:
            return None
        try:
            pid = self._process_id
            if pid is None:
                pid = self.root.process_id()
            for handle in findwindows.find_windows(process=pid):
                candidate = Desktop(backend=self.config.backend).window(handle=handle)
                class_name = candidate.class_name()
                if class_name in {"TFrmPDV", "TFrmPassWord", "TFrmAbout", "TApplication"}:
                    continue
                if self._is_active(candidate):
                    return candidate
        except (InvalidWindowHandle, ElementNotFoundError):
            return None
        except Exception:
            return None
        return None

    def _find_windows(self, class_name: str) -> list[Any]:
        if Desktop is None or findwindows is None:
            return []
        pid = self._process_id
        try:
            if pid is None:
                pid = self.root.process_id()
            handles = findwindows.find_windows(process=pid, class_name=class_name)
            windows = [Desktop(backend=self.config.backend).window(handle=handle) for handle in handles]
            if windows:
                return windows
        except Exception:
            pass
        # A VCL modal can be recreated after the original password wrapper
        # is destroyed. In that transition find_windows(process=...) can
        # briefly return no handles; use the desktop tree and retain the PID
        # guard when available.
        try:
            windows = []
            for candidate in Desktop(backend=self.config.backend).windows():
                try:
                    if candidate.class_name() != class_name:
                        continue
                    if pid is not None and candidate.process_id() != pid:
                        continue
                    windows.append(candidate)
                except Exception:
                    continue
            return windows
        except Exception:
            return []

    def _find_window(self, class_name: str) -> Any | None:
        if Desktop is None or findwindows is None:
            return None
        pid = self._process_id
        try:
            if pid is None:
                pid = self.root.process_id()
            handles = findwindows.find_windows(process=pid, class_name=class_name)
            if handles:
                return Desktop(backend=self.config.backend).window(handle=handles[0])
        except Exception:
            pass
        try:
            for candidate in Desktop(backend=self.config.backend).windows():
                try:
                    if candidate.class_name() == class_name and (
                        pid is None or candidate.process_id() == pid
                    ):
                        return candidate
                except Exception:
                    continue
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
        texts = []
        try:
            texts.append(dialog.window_text() or "")
        except (InvalidWindowHandle, ElementNotFoundError):
            return ""
        try:
            for child in dialog.descendants():
                try:
                    texts.append(child.window_text() or "")
                except (InvalidWindowHandle, ElementNotFoundError):
                    continue
        except (InvalidWindowHandle, ElementNotFoundError):
            pass
        return " ".join(texts)

    @staticmethod
    def _text_length(edit: Any) -> int:
        try:
            return len(edit.window_text() or "")
        except Exception:
            return -1

    @staticmethod
    def _is_active(window: Any) -> bool:
        try:
            return bool(window.is_visible() and window.is_enabled())
        except Exception:
            return False

    @classmethod
    def _is_main_interactable(cls, window: Any) -> bool:
        """Check visibility/enabled state and prove that focus reaches PDV."""
        try:
            if not window.is_visible() or not window.is_enabled():
                return False
        except Exception:
            return False
        try:
            if window.is_active():
                return True
        except Exception:
            pass
        try:
            # This is a non-destructive readiness probe. A real blocking VCL
            # modal prevents the owner from becoming active; a non-modal
            # residual TFrmPassWord does not.
            window.set_focus()
            return bool(window.is_active())
        except (InvalidWindowHandle, ElementNotFoundError):
            return False
        except Exception:
            return False

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
