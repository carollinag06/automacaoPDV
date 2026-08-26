from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from core.keyboard import hotkey, press

try:
    from pywinauto import Desktop, findwindows
except ImportError:  # Allows static checks without Windows dependencies.
    Desktop = None  # type: ignore[assignment]
    findwindows = None  # type: ignore[assignment]


class PdvPage:
    """Page Object for the controls proven by PDV.dfm/PDV.pas."""

    def __init__(self, window: Any, action_timeout: float = 10) -> None:
        self.window = window
        self.action_timeout = action_timeout

    def _visible(self, class_name: str) -> list[Any]:
        controls = []
        for control in self.window.descendants(class_name=class_name):
            try:
                if control.is_visible() and control.is_enabled():
                    controls.append(control)
            except Exception:
                continue
        return controls

    @property
    def product_edit(self) -> Any:
        edits = self._visible("TEdit")
        if len(edits) != 1:
            raise AssertionError(f"Esperado um TEdit de produto no FrmPDV; encontrados {len(edits)}")
        return edits[0]

    @property
    def items_memo(self) -> Any:
        memos = self._visible("TMemo")
        if len(memos) != 1:
            raise AssertionError(f"Esperado um TMemo de itens; encontrados {len(memos)}")
        return memos[0]

    def insert_product(self, code: str, quantity: str | None = None) -> None:
        reference = f"{quantity}*{code}" if quantity else code
        self.enter_reference(reference)

    def remove_product(self, code: str, quantity: str | None = None) -> None:
        reference = f"-{quantity}*{code}" if quantity else f"-{code}"
        self.enter_reference(reference)

    def enter_reference(self, reference: str) -> None:
        edit = self.product_edit
        edit.set_edit_text(reference)
        press(edit, "ENTER")

    def items_text(self) -> str:
        return self.items_memo.window_text()

    def has_item(self, code: str) -> bool:
        return re.search(rf"(?<!\d){re.escape(code)}(?!\d)", self.items_text()) is not None

    def total_labels(self, caption: str) -> list[Any]:
        labels = self.window.descendants(title=caption, class_name="TLabel")
        return [label for label in labels if _is_visible(label)]

    def value_next_to_caption(self, caption: str) -> Decimal:
        captions = self.total_labels(caption)
        if not captions:
            raise AssertionError(f"Caption de total não encontrada: {caption}")
        parent = captions[0].parent()
        labels = [label for label in parent.descendants(class_name="TLabel") if _is_visible(label)]
        values = [label.window_text() for label in labels if label.window_text() != caption]
        if not values:
            raise AssertionError(f"Valor do total não exposto após {caption}")
        return parse_money(values[-1])

    def subtotal(self) -> Decimal:
        return self.value_next_to_caption("Subtotal R$")

    def total(self) -> Decimal:
        return self.value_next_to_caption("Valor Total a Pagar R$")

    def discount(self) -> Decimal:
        return self.value_next_to_caption("Desconto R$")

    def cancel_sale(self, reason: str = "Teste automatizado: cancelamento de venda") -> None:
        """Cancela a venda e confirma o motivo quando o PDV o solicita."""
        press(self.window, "F6")
        dialog = self._wait_for_cancel_reason()
        if dialog is not None:
            self._fill_cancel_reason(dialog, reason)
            self._confirm_cancel_reason(dialog)

    def _wait_for_cancel_reason(self) -> Any | None:
        deadline = time.monotonic() + min(self.action_timeout, 5.0)
        while time.monotonic() < deadline:
            for window in self._top_level_windows():
                if self._is_cancel_reason_dialog(window):
                    return window
            time.sleep(0.1)
        return None

    @staticmethod
    def _is_cancel_reason_dialog(window: Any) -> bool:
        try:
            text = " ".join(
                [window.window_text()]
                + [child.window_text() for child in window.descendants()]
            )
            normalized = re.sub(r"\s+", " ", text).lower()
            return "motivo de cancelamento" in normalized or "digite o motivo" in normalized
        except Exception:
            return False

    @staticmethod
    def _fill_cancel_reason(dialog: Any, reason: str) -> None:
        edits = []
        combos = []
        for child in dialog.descendants():
            try:
                if not child.is_visible() or not child.is_enabled():
                    continue
                class_name = child.class_name()
                if class_name == "TEdit":
                    edits.append(child)
                elif class_name == "TComboBox":
                    combos.append(child)
            except Exception:
                continue

        if edits:
            edits[0].set_edit_text(reason)
            return

        if combos:
            combo = combos[0]
            try:
                combo.select("Teste de Software")
            except Exception:
                combo.set_focus()
                combo.type_keys(reason, set_foreground=True)
            return

        raise AssertionError("Dialogo de motivo de cancelamento nao expos TEdit ou TComboBox")

    @staticmethod
    def _confirm_cancel_reason(dialog: Any) -> None:
        for class_name in ("TBitBtn", "TButton"):
            for button in dialog.descendants(class_name=class_name):
                try:
                    if button.is_visible() and button.is_enabled() and re.search(
                        r"^&?OK$|confirmar|cancelar venda", button.window_text(), re.IGNORECASE
                    ):
                        button.click_input()
                        return
                except Exception:
                    continue
        press(dialog, "ENTER")

    def pause(self) -> None:
        hotkey(self.window, "ALT", "P")

    def has_window_class(self, class_name: str) -> bool:
        deadline = time.monotonic() + min(self.action_timeout, 3.0)
        while time.monotonic() < deadline:
            if any(self._window_class(window) == class_name for window in self._top_level_windows()):
                return True
            time.sleep(0.1)
        return False

    def modal(self) -> Any | None:
        for window in self._top_level_windows():
            if self._window_class(window) not in {"TFrmPDV", "TFrmAbout"}:
                return window
        return None

    def active_modal_text(self) -> str:
        deadline = time.monotonic() + min(self.action_timeout, 3.0)
        while time.monotonic() < deadline:
            dialog = self.modal()
            if dialog is not None:
                try:
                    texts = [dialog.window_text() or ""]
                    texts.extend(child.window_text() or "" for child in dialog.descendants())
                    text = " ".join(texts).strip()
                    if text:
                        return text
                except Exception:
                    pass
            time.sleep(0.1)
        return ""

    def help(self) -> None:
        hotkey(self.window, "CTRL", "F1")

    def escape(self) -> None:
        press(self.modal() or self.window, "ESC")

    def _top_level_windows(self) -> list[Any]:
        if Desktop is None or findwindows is None:
            return []
        try:
            pid = self.window.process_id()
            handles = findwindows.find_windows(process=pid)
            return [Desktop(backend="win32").window(handle=handle) for handle in handles]
        except Exception:
            return []

    @staticmethod
    def _window_class(window: Any) -> str:
        try:
            return window.class_name()
        except Exception:
            return ""


def _is_visible(control: Any) -> bool:
    try:
        return control.is_visible()
    except Exception:
        return False


def parse_money(value: str) -> Decimal:
    cleaned = re.sub(r"[^0-9,.-]", "", value.strip())
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise AssertionError(f"Valor monetário não reconhecido: {value!r}") from exc
