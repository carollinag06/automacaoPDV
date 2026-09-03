from __future__ import annotations

import ctypes
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import TestConfig
from .keyboard import press
from pages.base_page import capture_unknown_state

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


class PdvApplicationError(RuntimeError):
    """Fatal error dialog reported by SATPDV itself."""


@dataclass
class PdvApplication:
    config: TestConfig
    process: Any | None = None
    window: Any | None = None

    @staticmethod
    def _observe_dialog(dialog: Any, context_label: str) -> None:
        """Capture a dialog before a known reset/cleanup interaction."""
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, context_label)
        except Exception:
            # Observation is intentionally non-blocking and cannot affect reset.
            pass

    def start(self) -> Any:
        if Application is None or Desktop is None:
            raise AutomationUnavailable("pywinauto não está instalado")
        if not self.config.exe_path.exists():
            raise FileNotFoundError(self.config.exe_path)
        for launch_attempt in range(2):
            self.process = Application(backend=self.config.backend).start(
                str(self.config.exe_path),
                work_dir=str(self.config.exe_path.parent),
            )
            try:
                self.window = self._wait_for_start_form(self.config.start_timeout)
                self.maximize_window(self.window, self.config.window_mode)
                return self.window
            except WindowNotFound:
                if launch_attempt == 1:
                    raise
                self.close()
                time.sleep(1.0)
        raise WindowNotFound("SATPDV nao abriu uma tela inicial")

    def attach(self) -> Any:
        if Desktop is None:
            raise AutomationUnavailable("pywinauto não está instalado")
        self.window = self.wait_for_window(self.config.window_title_regex, self.config.action_timeout)
        self.maximize_window(self.window, self.config.window_mode)
        return self.window

    @staticmethod
    def maximize_window(window: Any | None, mode: str = "native") -> None:
        """Posiciona a janela VCL em um único monitor, sem tela branca extra.

        O SATPDV usa uma janela Delphi/VCL ``bsNone`` em algumas telas. Nessa
        configuração, ``WindowSpecification.maximize()`` pode não alterar o
        retângulo real. O modo ``native`` preserva os 800x600 definidos no
        DFM e centraliza a janela; ``fullscreen`` ocupa o monitor inteiro.
        """
        if window is None:
            return
        try:
            window.restore()
        except Exception:
            pass
        # A API Win32 é necessária para as formas sem moldura do SATPDV,
        # que ignoram o estado maximizado exposto pelo wrapper VCL.
        try:
            handle = getattr(window, "handle", None)
            if callable(handle):
                handle = handle()
            handle = int(handle)
            user32 = ctypes.windll.user32
            # Não use SM_*VIRTUALSCREEN: em uma estação com dois monitores
            # isso estende a janela pelos dois e pode deixar uma tela coberta
            # pelo canvas branco da VCL. O formulário deve ocupar somente o
            # monitor em que já está sendo exibido.
            class _Rect(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class _MonitorInfo(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("rcMonitor", _Rect),
                    ("rcWork", _Rect),
                    ("dwFlags", ctypes.c_uint),
                ]

            monitor = user32.MonitorFromWindow(handle, 2)  # MONITOR_DEFAULTTONEAREST
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                left = int(info.rcMonitor.left)
                top = int(info.rcMonitor.top)
                width = int(info.rcMonitor.right - info.rcMonitor.left)
                height = int(info.rcMonitor.bottom - info.rcMonitor.top)
            else:
                left = top = 0
                width = int(user32.GetSystemMetrics(0))
                height = int(user32.GetSystemMetrics(1))
            if width <= 0 or height <= 0:
                raise OSError("dimensoes do monitor invalidas")
            if str(mode).strip().lower() == "fullscreen":
                target_width, target_height = width, height
                target_left, target_top = left, top
            else:
                # TFrmPDV declara ClientWidth=800 e ClientHeight=600 sem
                # âncoras para redistribuir os controles internos.
                target_width = min(800, width)
                target_height = min(600, height)
                target_left = left + max(0, (width - target_width) // 2)
                target_top = top + max(0, (height - target_height) // 2)
            user32.ShowWindow(handle, 9)  # SW_RESTORE
            flags = 0x0004 | 0x0040  # SWP_NOZORDER | SWP_SHOWWINDOW
            if not user32.SetWindowPos(
                handle, 0, target_left, target_top,
                target_width, target_height, flags
            ):
                raise OSError("SetWindowPos retornou FALSE")
            user32.SetForegroundWindow(handle)
        except Exception:
            # Falhas da API não impedem uma execução headless/estática; ainda
            # tentamos deixar a janela focada pelo backend configurado.
            try:
                window.set_focus()
            except Exception:
                pass

    def reveal_login_dialog(self) -> Any | None:
        """Click the real closed-cashier screen to open the PDV password form."""
        if Desktop is None:
            raise AutomationUnavailable("pywinauto nao esta instalado")

        self.raise_if_application_error()
        dialog = self._wait_for_stable_form("TFrmPassWord", timeout=1.5)
        if dialog is not None:
            return dialog

        closed_cashier = self._wait_for_cashier_ready(self.config.start_timeout)
        if closed_cashier is None:
            return None

        for _ in range(3):
            self.raise_if_application_error()
            # The VCL closed-cashier form can be recreated or have its handle
            # state changed while the persistent splash is still present.
            # Rediscover it before every physical interaction.
            current_cashier = self._wait_for_stable_form(
                "TFrmPDVCaixaFechado", timeout=min(1.5, self.config.action_timeout)
            ) or closed_cashier
            self._activate_cashier(current_cashier)
            dialog = self._wait_for_stable_form(
                "TFrmPassWord", timeout=min(3.0, self.config.action_timeout)
            )
            if dialog is not None:
                return dialog
            current_cashier = self._wait_for_stable_form(
                "TFrmPDVCaixaFechado", timeout=min(1.0, self.config.action_timeout)
            ) or current_cashier
            self._send_cashier_key(current_cashier, "ENTER")
            dialog = self._wait_for_stable_form(
                "TFrmPassWord", timeout=min(2.0, self.config.action_timeout)
            )
            if dialog is not None:
                return dialog
            current_cashier = self._wait_for_stable_form(
                "TFrmPDVCaixaFechado", timeout=min(1.0, self.config.action_timeout)
            ) or current_cashier
            self._send_cashier_key(current_cashier, "SPACE")
            dialog = self._wait_for_stable_form(
                "TFrmPassWord", timeout=min(2.0, self.config.action_timeout)
            )
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
                    self._observe_dialog(dialog, "before_dismiss_recovery_prompt")
                    if self._click_no(dialog):
                        return True
            time.sleep(0.1)
        return False

    def recover_pending_sale(self, timeout: float = 4.0) -> bool:
        """Confirm the recognized pending-sale recovery prompt.

        This is intentionally separate from ``dismiss_recovery_prompt`` so a
        test that validates persistence can opt into recovery explicitly.  It
        only clicks a visible/enabled button whose caption is ``Sim``; no
        coordinate or blind Enter is used.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for dialog in self._windows():
                if not self._is_recovery_prompt(dialog):
                    continue
                try:
                    if not dialog.is_visible() or not dialog.is_enabled():
                        continue
                except Exception:
                    continue
                self._observe_dialog(dialog, "before_confirm_recovery_prompt")
                for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
                    try:
                        buttons = dialog.descendants(class_name=class_name)
                    except Exception:
                        continue
                    for button in buttons:
                        try:
                            caption = unicodedata.normalize(
                                "NFKD", button.window_text() or ""
                            ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                            if caption != "sim" or not button.is_visible() or not button.is_enabled():
                                continue
                            try:
                                button.click_input()
                            except Exception:
                                button.click()
                            return True
                        except Exception:
                            continue
            time.sleep(0.1)
        return False

    @staticmethod
    def _is_recovery_prompt(dialog: Any) -> bool:
        try:
            texts = [dialog.window_text() or ""]
            texts.extend(child.window_text() or "" for child in dialog.descendants())
            message = " ".join(texts)
            return re.search(
                r"recuperar\s+(?:(?:uma|a)\s+)?venda|"
                r"venda\s+(?:aberta|pendente)|"
                r"venda\s+em\s+processo\s+de\s+finaliza",
                message,
                re.IGNORECASE,
            ) is not None
        except Exception:
            return False

    @staticmethod
    def _click_no(dialog: Any) -> bool:
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            for button in dialog.descendants(class_name=class_name):
                try:
                    caption = unicodedata.normalize(
                        "NFKD", button.window_text() or ""
                    ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                    if button.is_visible() and button.is_enabled() and caption == "nao":
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
            self.raise_if_application_error()
            cashier = self._find_form("TFrmPDVCaixaFechado")
            if cashier is not None:
                try:
                    if cashier.is_visible() and cashier.is_enabled():
                        stable = self._wait_for_stable_form(
                            "TFrmPDVCaixaFechado", timeout=min(1.0, max(0.1, deadline - time.monotonic()))
                        )
                        if stable is not None:
                            return stable
                except Exception:
                    pass
            time.sleep(0.25)
        return None

    @staticmethod
    def _window_snapshot(window: Any) -> tuple[Any, ...] | None:
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
        except Exception:
            return None

    def _wait_for_stable_form(
        self,
        class_name: str,
        *,
        timeout: float = 1.5,
        checks: int = 3,
        interval: float = 0.1,
    ) -> Any | None:
        """Rediscover a process-owned VCL form until geometry/state stabilizes."""
        deadline = time.monotonic() + timeout
        previous: tuple[Any, ...] | None = None
        stable_count = 0
        last_candidate = None
        while time.monotonic() < deadline:
            candidate = self._find_form(class_name, timeout=0.05)
            snapshot = self._window_snapshot(candidate) if candidate is not None else None
            if snapshot is None or not self._is_active_window(candidate):
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

    def _activate_cashier(self, cashier: Any) -> None:
        try:
            cashier.restore()
        except Exception:
            pass
        self.maximize_window(cashier, self.config.window_mode)
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
            self.raise_if_application_error()
            main = self._find_form("TFrmPDV", timeout=0.1)
            if main is not None:
                return main
            closed_cashier = self._find_form("TFrmPDVCaixaFechado", timeout=0.1)
            if closed_cashier is not None:
                return closed_cashier
            time.sleep(0.1)
        raise WindowNotFound("SATPDV nao abriu TFrmPDV nem TFrmPDVCaixaFechado")

    def raise_if_application_error(self) -> None:
        for window in self._windows():
            if self._is_application_error(window):
                if self._dismiss_application_error(window):
                    continue
                message = self._window_contents(window)
                raise PdvApplicationError(
                    "SATPDV encontrou um problema; erro exibido pela aplicacao: "
                    + message
                )

    def _dismiss_application_error(self, dialog: Any) -> bool:
        """Confirma somente ``Não Enviar`` no relatório nativo do SATPDV.

        Esse diálogo é uma janela nativa ``#32770`` criada pelo SATPDV após
        um erro de foco. Não usamos Enter, ESC ou coordenadas, porque isso
        poderia acionar outra ação enquanto o ``TFrmPDV`` ainda está
        desabilitado.
        """
        self._observe_dialog(dialog, "before_dismiss_application_error")
        for class_name in ("Button", "TButton", "TBitBtn"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                try:
                    caption = unicodedata.normalize(
                        "NFKD", button.window_text() or ""
                    ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                    if caption != "nao enviar":
                        continue
                    if not button.is_visible() or not button.is_enabled():
                        continue
                    try:
                        button.click_input()
                    except Exception:
                        button.click()
                    time.sleep(0.2)
                    return True
                except Exception:
                    continue
        return False

    @staticmethod
    def _is_application_error(window: Any) -> bool:
        message = PdvApplication._window_contents(window)
        return re.search(
            r"satpdv\s+encontrou\s+um\s+problema|"
            r"n[aã]o\s+[eé]\s+poss[ií]vel\s+fazer\s+uma\s+janela\s+modal\s+vis[ií]vel|"
            r"n[aã]o\s+[eé]\s+poss[ií]vel\s+focar\s+uma\s+janela\s+desativada",
            message,
            re.IGNORECASE,
        ) is not None

    @staticmethod
    def _window_contents(window: Any) -> str:
        try:
            texts = [window.window_text() or ""]
            texts.extend(child.window_text() or "" for child in window.descendants())
            return " ".join(dict.fromkeys(texts)).strip()
        except Exception:
            return ""

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
        extended_deadline = time.monotonic() + max(timeout, 90.0)
        extended_for_splash = False
        password = None
        password_active = False
        while True:
            if time.monotonic() >= deadline:
                # TFrmAbout is a persistent splash in this build. Do not wait
                # for its destruction, but allow delayed recovery/finalization
                # prompts to be handled while it is still present.
                about = self._find_form("TFrmAbout")
                delayed_main = self._find_form("TFrmPDV")
                delayed_password = self._find_form("TFrmPassWord")
                # Authentication can finish after the splash disappears in
                # this build. Keep a bounded grace period whenever either
                # real form is already present, not only while TFrmAbout is
                # present.
                if (
                    extended_for_splash
                    or (about is None and delayed_main is None and delayed_password is None)
                ):
                    break
                deadline = extended_deadline
                extended_for_splash = True
            self.raise_if_application_error()
            main = self._find_form("TFrmPDV")
            password = self._find_form("TFrmPassWord")
            password_active = password is not None and self._is_active_window(password)
            if self.dismiss_recovery_prompt(allow_recovery=allow_recovery, timeout=0.5):
                time.sleep(0.1)
                continue
            information = self._find_form("TFrmDlgInformacao")
            if information is not None and self._is_expired_password_warning(information):
                self._wait_for_stable_form("TFrmDlgInformacao", timeout=0.8)
                self._dismiss_information(information)
                time.sleep(0.2)
                continue
            if main is not None and self._is_active_window(main) and password_active:
                if self._dismiss_residual_password(password):
                    time.sleep(0.2)
                    continue
            if information is not None and password_active:
                raise WindowNotFound("PDV exibiu TFrmDlgInformacao apos o login; PDV_READY nao foi alcancado")
            if main is not None and not password_active:
                try:
                    if main.is_visible() and main.is_enabled():
                        self.maximize_window(main, self.config.window_mode)
                        self.window = main
                        return main
                except Exception:
                    pass
            time.sleep(0.1)
        if password is not None and password_active:
            raise WindowNotFound("PDV_READY nao foi observado: TFrmPassWord ainda esta ativo")
        raise WindowNotFound("PDV_READY nao foi observado: TFrmPDV nao ficou visivel e habilitado")

    @staticmethod
    def _is_active_window(window: Any) -> bool:
        try:
            return bool(window.is_visible() and window.is_enabled())
        except Exception:
            return False

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
        self._observe_dialog(dialog, "before_dismiss_information")
        try:
            press(dialog, "ENTER")
            time.sleep(0.2)
            if not self._is_active_window(dialog):
                return
        except Exception:
            pass
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                try:
                    if re.search(r"^&?OK$", button.window_text() or "", re.IGNORECASE):
                        try:
                            button.set_focus()
                            press(button, "ENTER")
                        except Exception:
                            try:
                                button.click_input()
                            except Exception:
                                button.click()
                        return
                except Exception:
                    continue

    @staticmethod
    def _dismiss_residual_password(dialog: Any) -> bool:
        """Close a known post-auth password form only through its OK control."""
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_dismiss_residual_password")
        except Exception:
            pass
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                try:
                    caption = unicodedata.normalize(
                        "NFKD", button.window_text() or ""
                    ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                    if caption != "ok" or not button.is_visible() or not button.is_enabled():
                        continue
                    try:
                        button.click_input()
                    except Exception:
                        button.click()
                    # In this build the password form can remain as a
                    # non-modal VCL window after the successful auth event.
                    # It is safe to close it here because TFrmPDV is already
                    # visible and enabled and the OK control was identified.
                    time.sleep(0.15)
                    try:
                        if dialog.is_visible() and dialog.is_enabled():
                            press(dialog, "ESC")
                    except Exception:
                        pass
                    try:
                        if dialog.is_visible() and dialog.is_enabled():
                            dialog.close()
                    except Exception:
                        pass
                    return True
                except Exception:
                    continue
        raise capture_unknown_state(dialog, "post_auth_password_modal")

    def child_windows(self) -> list[Any]:
        if self.window is None:
            return []
        try:
            return self.window.descendants()
        except Exception:
            return []

    def close(self, reset: bool = True) -> None:
        if reset:
            self.reset_for_next_test()
        window = self._find_form("TFrmPDV") or self._find_form("TFrmPDVCaixaFechado") or self.window
        process = self.process
        if window is not None:
            try:
                window.close()
            except Exception:
                try:
                    window.send_keystrokes("{ESC}")
                except Exception:
                    pass
        if process is not None:
            try:
                process.wait_for_process_exit(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait_for_process_exit(timeout=2)
                except Exception:
                    pass
        self.window = None
        self.process = None

    def reset_for_next_test(self, timeout: float = 2.0) -> None:
        """Return a reused process to an authenticated, sale-ready state.

        This is intentionally called from the function-scoped fixture teardown,
        not silently at the beginning of the next test. A residual unknown
        dialog still remains untouched by the fixture's ``UnknownDialogError``
        path so its evidence can be inspected manually.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            residuals = []
            for window in self._windows():
                try:
                    class_name = window.class_name()
                    if class_name in {
                        "TFrmDlgInformacao",
                        "TFrmDlg",
                        "TFrmPassWord",
                        "TFrmInserirPgto",
                        "TFrmPDVProdutoNaoEncontrado",
                        "TFrmPDVDlg",
                        "TppPrintPreview",
                        "TFrmPDVAjuda",
                        "TFrmPDVPausa",
                        "TDlgProd",
                    } or self._is_recovery_prompt(window) or self._is_application_error(window):
                        residuals.append(window)
                except Exception:
                    continue
            if not residuals:
                # Mesmo sem modal residual, ainda precisamos validar o estado
                # da venda e limpar um item deixado pelo teste anterior.
                break
            for dialog in residuals:
                try:
                    if self._is_recovery_prompt(dialog):
                        self._click_no(dialog)
                    elif self._is_application_error(dialog):
                        if not self._dismiss_application_error(dialog):
                            raise PdvApplicationError(
                                "Modal de erro do SATPDV sem botao 'Nao Enviar': "
                                + self._window_contents(dialog)
                            )
                    elif self._is_close_cashier_prompt(dialog):
                        self._click_no(dialog)
                    elif dialog.class_name() == "TFrmDlgInformacao":
                        self._dismiss_information(dialog)
                    elif dialog.class_name() == "TFrmDlg":
                        self._complete_cancel_dialog(dialog)
                    elif dialog.class_name() == "TFrmInserirPgto":
                        self._close_payment_dialog(dialog)
                    elif dialog.class_name() == "TFrmPDVProdutoNaoEncontrado":
                        self._dismiss_missing_product(dialog)
                    elif dialog.class_name() == "TFrmPDVDlg":
                        if self._is_known_discount_dialog(dialog):
                            self._cancel_known_discount_dialog(dialog)
                        # A TFrmPDVDlg with another purpose is intentionally
                        # preserved for evidence; never press ESC blindly.
                        else:
                            continue
                    elif dialog.class_name() == "TppPrintPreview":
                        self._close_print_preview(dialog)
                    elif dialog.class_name() == "TDlgProd":
                        self._close_product_dialog(dialog)
                    else:
                        press(dialog, "ESC")
                except Exception:
                    continue
            time.sleep(0.15)

        main = self._find_form("TFrmPDV")
        if main is not None and self._is_active_window(main):
            # PdvPage owns the proven F1 transition and the real cancel-reason
            # dialog. It is imported lazily to avoid a module cycle at import.
            from pages.pdv_page import PdvPage

            page = PdvPage(main, self.config.action_timeout)
            page.leave_consultation_mode(timeout=min(2.0, max(0.5, timeout)))
            if re.search(r"(?m)^\s*\d{1,3}\s+\d+\b", page.items_text()):
                page.cancel_sale("Teste automatizado: reset entre testes")
            self.window = main
            return

        closed_cashier = self._find_form("TFrmPDVCaixaFechado")
        if closed_cashier is not None and self._is_active_window(closed_cashier):
            from pages.login_page import LoginPage

            login_dialog = self.reveal_login_dialog()
            if login_dialog is None:
                raise WindowNotFound(
                    "Reset entre testes nao conseguiu reabrir TFrmPassWord a partir de TFrmPDVCaixaFechado"
                )
            LoginPage(login_dialog, self.config).login()
            self.wait_until_ready(self.config.start_timeout)

    @staticmethod
    def _is_close_cashier_prompt(dialog: Any) -> bool:
        try:
            texts = [dialog.window_text() or ""]
            texts.extend(child.window_text() or "" for child in dialog.descendants())
            message = " ".join(texts)
            return re.search(
                r"tem\s+certeza.*fechar\s+o\s+caixa", message, re.IGNORECASE | re.DOTALL
            ) is not None
        except Exception:
            return False

    @staticmethod
    def _close_payment_dialog(dialog: Any) -> None:
        """Close only the mapped payment form left by an interrupted test.

        ``TFrmInserirPgto`` is a known SATPDV form (PDV.pas creates it at the
        payment flow).  ESC is attempted first; if this VCL build keeps the
        modeless form alive, ``close`` is used on that exact window so the
        shared-process reset cannot leak a disabled TFrmPDV into the next
        test. No button is selected by coordinates.
        """
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_reset_close_payment_dialog")
        except Exception:
            pass
        try:
            dialog.set_focus()
            press(dialog, "ESC")
            time.sleep(0.2)
            if not dialog.is_visible():
                return
        except Exception:
            pass
        try:
            dialog.close()
        except Exception:
            try:
                dialog.send_keystrokes("{ESC}")
            except Exception:
                pass

    @staticmethod
    def _dismiss_missing_product(dialog: Any) -> None:
        """Use the documented F2/Space controls of the known warning form."""
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_reset_dismiss_missing_product")
        except Exception:
            pass
        try:
            dialog.set_focus()
            press(dialog, "F2")
            time.sleep(0.2)
            if dialog.is_visible():
                press(dialog, "SPACE")
        except Exception:
            pass

    @staticmethod
    def _close_print_preview(dialog: Any) -> None:
        """Close the mapped FastReport preview left by an interrupted sale."""
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_reset_close_print_preview")
        except Exception:
            pass
        try:
            dialog.set_focus()
            press(dialog, "ESC")
            time.sleep(0.25)
            if not dialog.is_visible():
                return
        except Exception:
            pass
        try:
            dialog.close()
        except Exception:
            try:
                dialog.send_keystrokes("{ESC}")
            except Exception:
                pass

    @staticmethod
    def _close_product_dialog(dialog: Any) -> None:
        """Close the mapped ``TDlgProd`` list without selecting a product."""
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_reset_close_product_dialog")
        except Exception:
            pass
        for button in dialog.descendants(class_name="TBitBtn"):
            try:
                caption = unicodedata.normalize(
                    "NFKD", button.window_text() or ""
                ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                if caption == "cancelar" and button.is_visible() and button.is_enabled():
                    button.click_input()
                    return
            except Exception:
                continue
        try:
            dialog.close()
        except Exception:
            pass

    @staticmethod
    def _complete_cancel_dialog(dialog: Any) -> None:
        """Preenche e confirma o motivo quando o reset encontra cancelamento."""
        reason = "Teste automatizado: cancelamento de venda"
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_reset_complete_cancel_dialog")
        except Exception:
            pass
        try:
            for edit in dialog.descendants(class_name="TEdit"):
                if edit.is_visible() and edit.is_enabled():
                    edit.set_edit_text(reason)
                    break
        except Exception:
            pass

    @staticmethod
    def _is_known_discount_dialog(dialog: Any) -> bool:
        """Identify the mapped TFrmPDVDlg discount input before cancelling it."""
        try:
            texts = [dialog.window_text() or ""]
            texts.extend(child.window_text() or "" for child in dialog.descendants())
            return re.search(r"desconto|cupom", " ".join(texts), re.IGNORECASE) is not None
        except Exception:
            return False

    @staticmethod
    def _cancel_known_discount_dialog(dialog: Any) -> None:
        """Cancel a known discount/coupon input left by a failed test."""
        try:
            from .test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_reset_cancel_discount_dialog")
        except Exception:
            pass
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                try:
                    caption = unicodedata.normalize(
                        "NFKD", button.window_text() or ""
                    ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                    if caption == "cancelar" and button.is_visible() and button.is_enabled():
                        button.click_input()
                        return
                except Exception:
                    continue
        try:
            dialog.set_focus()
            press(dialog, "ESC")
        except Exception:
            pass
        for class_name in ("TBitBtn", "TButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                buttons = []
            for button in buttons:
                try:
                    caption = unicodedata.normalize(
                        "NFKD", button.window_text() or ""
                    ).encode("ascii", "ignore").decode("ascii").strip("& ").lower()
                    if button.is_visible() and button.is_enabled() and caption == "ok":
                        button.click_input()
                        return
                except Exception:
                    continue
        try:
            press(dialog, "ENTER")
        except Exception:
            pass

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
