from __future__ import annotations

import re
import time
import ctypes
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from typing import Any

from core.keyboard import hotkey, press
from pages.base_page import capture_unknown_state

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
        self.last_sale_submitted = False
        self.last_total_read_method: str | None = None

    @staticmethod
    def _observe_dialog(dialog: Any, context_label: str) -> None:
        """Capture the complete mapped-dialog text before interacting with it."""
        try:
            from core.test_results import record_dialog_observation

            record_dialog_observation(dialog, context_label)
        except Exception:
            # Observability must never change the behavior of the test flow.
            pass

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
        self._ensure_sale_mode()
        reference = f"{quantity}*{code}" if quantity else code
        self.enter_reference(reference)
        if not self.has_item(code):
            raise AssertionError(
                f"Produto {code} nao apareceu no TMemo pnlProdutos apos a insercao"
            )

    def remove_product(self, code: str, quantity: str | None = None) -> None:
        self._ensure_sale_mode()
        reference = f"-{quantity}*{code}" if quantity else f"-{code}"
        self.enter_reference(reference)
        if not self.wait_until_cancelled(code):
            try:
                observed = self.items_text().replace("\r", "\\r").replace("\n", "\\n")
            except Exception:
                observed = "<nao foi possivel ler o TMemo>"
            raise AssertionError(
                f"Cancelamento do item {code} nao apareceu no TMemo pnlProdutos; "
                f"conteudo observado: {observed[:300]}"
            )

    def enter_reference(self, reference: str) -> None:
        edit = self.product_edit
        if "*" in reference:
            quantity, code = reference.split("*", 1)
            self._commit_quantity(edit, quantity)
            self._type_reference_part(edit, code)
        else:
            self._type_reference_part(edit, reference)
        press(edit, "ENTER")

    def cancel_quantity_by_item_index(self, quantity: str | int, index: str | int) -> None:
        """Cancel a quantity using ``<negative quantity>*<positive index>``.

        The VCL handler ``TFrmPDV.EditCodigoProdutoKeyPress`` in
        ``PDV.pas`` (lines 1447-1465) consumes ``*`` to commit the quantity
        through ``AlterarQde``; ``PDVController.ParseRef`` then receives the
        remaining positive item index.  The minus sign is therefore part of
        the quantity, not part of the index (for example ``-1*2``).
        """
        quantity_text = str(quantity).strip()
        index_text = str(index).strip()
        if quantity_text.startswith("-"):
            quantity_text = quantity_text[1:]
        if index_text.startswith("-"):
            index_text = index_text[1:]
        if not quantity_text or not index_text:
            raise ValueError("Quantidade e indice devem ser informados")
        self.enter_reference(f"-{quantity_text}*{index_text}")

    def consult_product_stock(
        self,
        code: str,
        expected_stock: dict[str, int | str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Open consultation mode, query ``code`` and read the store grid.

        The SATPDV paints the ``TDBGrid`` cells itself.  The method therefore
        keeps the accessible-control attempt as the first source and records
        a cropped OCR image only when the grid text is not exposed by the
        Win32/UIA backends.  It never considers merely opening the screen a
        successful consultation.
        """
        timeout = min(self.action_timeout, 8.0) if timeout is None else timeout
        self._open_price_consultation(timeout)
        self.enter_reference(str(code))
        time.sleep(0.4)
        self._activate_for_visual_read()
        product_text = self._consultation_product_text()
        grid = self._toggle_stock_grid_until_visible(timeout)
        stock = self._read_stock_grid(grid, expected_stock or {}, timeout=timeout)
        stock["product_code"] = str(code)
        stock["product_text"] = product_text
        return stock

    def _open_price_consultation(self, timeout: float) -> None:
        """Enter F1 mode through the focused product edit, not coordinates."""
        if self._visible_stock_grid() or self._looks_like_consultation_screen():
            return
        # F1 is handled by TFrmPDV.FormKeyDown, but depending on which VCL
        # child currently owns focus, pywinauto may deliver it either to the
        # form or to EditCodigoProduto. Try both routes, and verify the
        # yellow consultation state after each event before continuing.
        senders = ("form", "edit")
        for sender in senders:
            try:
                if sender == "form":
                    self.window.set_focus()
                    self.window.type_keys("{F1}", set_foreground=True)
                else:
                    edit = self.product_edit
                    edit.set_focus()
                    press(edit, "F1")
            except Exception:
                continue
            check_deadline = time.monotonic() + min(2.5, max(0.5, timeout))
            while time.monotonic() < check_deadline:
                if self._looks_like_consultation_screen():
                    # The grid must remain hidden while the product Enter
                    # executes: EditCodigoProdutoKeyDown fills QPro, and only
                    # then does Ctrl+E expose GridLoja.
                    return
                time.sleep(0.2)
        raise capture_unknown_state(self.window, "price_consultation_not_open")

    def _ensure_sale_mode(self, timeout: float | None = None) -> None:
        """Leave a persisted consultation state before a sale interaction."""
        self.leave_consultation_mode(timeout=timeout)

    def leave_consultation_mode(self, timeout: float | None = None) -> bool:
        """Press F1 again when the reused PDV is still in price consultation.

        ``TFrmPDV`` keeps ``PDVController.ModoDeConsulta`` outside the form
        lifetime in some builds.  The supported UI transition back to sale is
        F1, delivered after focusing ``EditCodigoProduto``.  Return ``False``
        when the screen is already in sale mode and raise with evidence when
        the known transition cannot be confirmed.
        """
        timeout = min(self.action_timeout, 4.0) if timeout is None else timeout
        if not self._looks_like_consultation_screen():
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.window.set_focus()
                edit = self.product_edit
                edit.set_focus()
                press(edit, "F1")
            except Exception:
                pass
            time.sleep(0.25)
            if not self._looks_like_consultation_screen():
                return True
        raise capture_unknown_state(self.window, "sale_mode_not_restored_after_consultation")

    def _toggle_stock_grid_until_visible(self, timeout: float) -> Any:
        """Send Ctrl+E to the real TEdit and wait for GridLoja to be visible."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            grid = self._visible_stock_grid()
            if grid is not None:
                return grid
            try:
                edit = self.product_edit
                edit.set_focus()
                hotkey(edit, "CTRL", "E")
            except Exception:
                pass
            time.sleep(0.25)
        raise capture_unknown_state(self.window, "stock_grid_not_visible_after_ctrl_e")

    def _visible_stock_grid(self) -> Any | None:
        try:
            main = self.window.rectangle()
            candidates = []
            for grid in self.window.descendants(class_name="TDBGrid"):
                if not grid.is_visible() or not grid.is_enabled():
                    continue
                rect = grid.rectangle()
                # GridLoja is the left-side stock grid. GridProcura is also a
                # TDBGrid, but is hidden in the normal consultation route.
                if rect.left < main.left + main.width() * 0.55 and rect.top > main.top + main.height() * 0.25:
                    candidates.append((rect.width() * rect.height(), grid))
            return max(candidates, key=lambda item: item[0])[1] if candidates else None
        except Exception:
            return None

    def _looks_like_consultation_screen(self) -> bool:
        """Use the mode-specific visual state without trusting a TLabel HWND."""
        # The source changes the pnlProdutos/pnlProduto background to yellow.
        # This is a stronger signal than the empty memo, which is also the
        # normal initial state of a sale.
        if self._visible_stock_grid() is not None:
            return True
        try:
            memo = self.items_memo
            rect = memo.rectangle()
            from PIL import ImageGrab

            image = ImageGrab.grab(
                bbox=(rect.left + 20, rect.top + 60, rect.right - 20, rect.bottom - 20),
                all_screens=True,
            )
            pixels = list(image.convert("RGB").resize((1, 1)).getdata())
            red, green, blue = pixels[0]
            if red > 200 and green > 200 and blue < 190:
                return True
        except Exception:
            pass
        try:
            header = self._ocr_consultation_header()
            return "modo consulta" in self._ascii(header)
        except Exception:
            return False

    def _ocr_consultation_header(self) -> str:
        """OCR only the top strip where LabModoConsultaDePrecos is painted."""
        import pytesseract

        self._configure_tesseract(pytesseract)
        rect = self.window.rectangle()
        from PIL import ImageGrab

        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.top + max(100, int(rect.height() * 0.24))),
            all_screens=True,
        )
        return pytesseract.image_to_string(image, config="--psm 6")

    def _consultation_product_text(self) -> str:
        """Collect accessible right-panel values and OCR only if necessary."""
        try:
            main = self.window.rectangle()
            controls = []
            for control in self.window.descendants():
                try:
                    rect = control.rectangle()
                    if rect.left >= main.left + main.width() * 0.40 and _is_visible(control):
                        controls.append(self._control_text(control))
                except Exception:
                    continue
            text = " ".join(value for value in controls if value).strip()
        except Exception:
            text = ""
        panel_text = self._ocr_product_panel()
        if panel_text:
            text = f"{text} {panel_text}".strip()
        if self._money_values(text):
            return text
        return text + " " + self._ocr_main_region(right_half=True)

    def _ocr_product_panel(self) -> str:
        """OCR the visible description panel instead of the whole desktop."""
        try:
            import pytesseract
            from PIL import ImageOps

            self._configure_tesseract(pytesseract)
            main = self.window.rectangle()
            candidates = []
            for panel in self.window.descendants(class_name="TPanel"):
                if not _is_visible(panel):
                    continue
                rect = panel.rectangle()
                if rect.left < main.left + main.width() * 0.35:
                    continue
                if rect.width() < main.width() * 0.25 or rect.height() < 80:
                    continue
                if rect.top < main.top + main.height() * 0.25:
                    continue
                candidates.append((rect.width() * rect.height(), rect))
            if not candidates:
                return ""
            _, rect = max(candidates)
            image = self._grab_rect(rect)
            image = ImageOps.grayscale(image).resize((image.width * 2, image.height * 2))
            return pytesseract.image_to_string(image, config="--psm 6")
        except Exception:
            return ""

    def _read_stock_grid(
        self,
        grid: Any,
        expected: dict[str, int | str],
        timeout: float = 4.0,
    ) -> dict[str, Any]:
        """Poll until the painted rows are available, then create evidence."""
        self._activate_for_visual_read(grid)
        deadline = time.monotonic() + max(0.5, timeout)
        source = "grid_texts"
        raw = ""
        parsed: dict[str, int | str] = {}
        best_parsed: dict[str, int | str] = {}
        while True:
            accessible = self._control_texts(grid)
            parsed = self._parse_stock_rows(accessible, expected)
            source = "grid_texts"
            raw = accessible

            if not parsed or (expected and any(name not in parsed for name in expected)):
                uia_text = self._uia_grid_text(grid)
                uia_parsed = self._parse_stock_rows(uia_text, expected)
                if uia_parsed:
                    parsed = uia_parsed
                    raw = uia_text
                    source = "uia_grid"

            if not parsed or (expected and any(name not in parsed for name in expected)):
                ocr_text = self._ocr_grid(grid)
                ocr_parsed = self._parse_stock_rows(ocr_text, expected)
                store_text = self._ocr_grid_store_column(grid)
                quantity_text = self._ocr_grid_quantity_column(grid)
                # A right-aligned minus sign can be interpreted as a trailing
                # zero by OCR on the complete grid. The dedicated Quant.
                # column preserves the row values and is used only when the
                # store rows are present in the same OCR frame.
                frame_text = f"{ocr_text}\n{store_text}"
                present_stores = sorted(
                    (store for store in expected if self._ascii(store) in self._ascii(frame_text)),
                    key=lambda store: self._ascii(frame_text).find(self._ascii(store)),
                )
                quantity_values = self._parse_quantity_column(quantity_text)
                if len(quantity_values) >= len(present_stores) and present_stores:
                    row_quantities = quantity_values[-len(present_stores):]
                    ocr_parsed.update(
                        {store: row_quantities[index] for index, store in enumerate(present_stores)}
                    )
                if ocr_parsed:
                    parsed = ocr_parsed
                    raw = (
                        f"{ocr_text}\n[Loja column OCR]\n{store_text}"
                        f"\n[Quant. column OCR]\n{quantity_text}"
                    )
                    source = "ocr_grid"

            # OCR is sampled repeatedly because the Delphi grid can repaint
            # between frames. Preserve the richest successful frame instead
            # of replacing a complete result with a later partial one.
            if len(parsed) > len(best_parsed):
                best_parsed = dict(parsed)
            elif len(parsed) == len(best_parsed) and parsed:
                for store, value in parsed.items():
                    best_parsed.setdefault(store, value)

            if (not expected or all(name in best_parsed for name in expected)) or time.monotonic() >= deadline:
                break
            time.sleep(0.35)

        parsed = best_parsed

        screenshot = self._save_stock_screenshot(grid)
        evidence_lines = [
            f"Fonte da grade: {source}",
            f"Codigo consultado: {self._last_consulted_code()}",
            f"Screenshot da grade: {screenshot}",
        ]
        for store, wanted in (expected or {}).items():
            found = parsed.get(store)
            evidence_lines.append(
                f"Loja: {store} | Quantidade encontrada: {found if found is not None else '<nao identificada>'} | "
                f"Quantidade esperada: {wanted}"
            )
        return {
            "stores": parsed,
            "raw": raw,
            "source": source,
            "screenshot": str(screenshot),
            "evidence_text": "\n".join(evidence_lines),
        }

    def _uia_grid_text(self, grid: Any) -> str:
        """Read TDBGrid through UI Automation when Win32 has no cell text."""
        if Desktop is None:
            return ""
        try:
            uia_grid = Desktop(backend="uia").window(handle=grid.handle)
            values = [uia_grid.window_text() or ""]
            values.extend(child.window_text() or "" for child in uia_grid.descendants())
            return "\n".join(value for value in values if value)
        except Exception:
            return ""

    def _last_consulted_code(self) -> str:
        try:
            return self.product_edit.window_text() or ""
        except Exception:
            return ""

    @classmethod
    def _parse_stock_rows(cls, text: str, expected: dict[str, int | str]) -> dict[str, int | str]:
        normalized = cls._ascii(text)
        result: dict[str, int | str] = {}
        for store, wanted in expected.items():
            name = cls._ascii(store)
            match = re.search(
                rf"{re.escape(name)}.{{0,140}}?(\d{{2,}}(?:[.,]\d+)?)\s*(?:U|UN|UND)?",
                normalized,
                re.IGNORECASE | re.DOTALL,
            )
            if not match:
                continue
            value = match.group(1).replace(".", "").replace(",", ".")
            try:
                result[store] = int(Decimal(value)) if Decimal(value) == int(Decimal(value)) else value
            except (InvalidOperation, ValueError):
                continue
        return result

    @staticmethod
    def _ascii(value: str) -> str:
        value = unicodedata.normalize("NFKD", value or "")
        return "".join(char for char in value if not unicodedata.combining(char)).casefold()

    def _ocr_grid(self, grid: Any) -> str:
        try:
            from PIL import ImageOps
            import pytesseract

            self._configure_tesseract(pytesseract)
            rect = grid.rectangle()
            image = self._grab_rect(rect)
            # TDBGrid paints a large blank client area below the rows. Keep
            # the header and the first rows in the OCR window so that the
            # blank area does not dilute recognition of the quantities.
            crop_height = min(image.height, max(180, int(image.height * 0.35)))
            image = image.crop((0, 0, image.width, crop_height))
            image = ImageOps.grayscale(image).resize((image.width * 2, image.height * 2))
            return pytesseract.image_to_string(image, config="--psm 6")
        except Exception:
            return ""

    def _ocr_grid_quantity_column(self, grid: Any) -> str:
        """OCR only the numeric Quant. column to preserve signs and rows."""
        try:
            from PIL import ImageOps
            import pytesseract

            self._configure_tesseract(pytesseract)
            image = self._grab_rect(grid.rectangle())
            crop_height = min(image.height, max(180, int(image.height * 0.35)))
            # DFM widths are Loja=300, Quant.=140, Lote/Tam/Cor=200. The
            # indicator and DPI scaling are covered by these relative bounds.
            left = int(image.width * 0.41)
            # Keep the right grid separator and the Lote/Tam/Cor column out
            # of the crop; otherwise OCR can read the separator as a trailing
            # zero (for example, ``-1`` as ``-10``).
            right = int(image.width * 0.62)
            image = image.crop((left, 0, right, crop_height))
            image = ImageOps.grayscale(image).resize((image.width * 4, image.height * 4))
            return pytesseract.image_to_string(
                image,
                config="--psm 6 -c tessedit_char_whitelist=0123456789-.,",
            )
        except Exception:
            return ""

    def _ocr_grid_store_column(self, grid: Any) -> str:
        """OCR only the Loja/Descrição column to preserve row ordering."""
        try:
            from PIL import ImageOps
            import pytesseract

            self._configure_tesseract(pytesseract)
            image = self._grab_rect(grid.rectangle())
            crop_height = min(image.height, max(180, int(image.height * 0.35)))
            left = 0
            right = int(image.width * 0.42)
            image = image.crop((left, 0, right, crop_height))
            image = ImageOps.grayscale(image).resize((image.width * 3, image.height * 3))
            return pytesseract.image_to_string(image, config="--psm 6")
        except Exception:
            return ""

    @staticmethod
    def _parse_quantity_column(text: str) -> list[int | str]:
        values: list[int | str] = []
        for match in re.findall(r"(?<!\d)-?\d+(?:[.,]\d+)?", text or ""):
            try:
                number = Decimal(match.replace(".", "").replace(",", "."))
                values.append(int(number) if number == int(number) else match)
            except (InvalidOperation, ValueError):
                continue
        return values

    def _ocr_main_region(self, right_half: bool = False) -> str:
        try:
            import pytesseract

            self._configure_tesseract(pytesseract)
            rect = self.window.rectangle()
            image = self._grab_rect(rect)
            if right_half:
                image = image.crop((int(image.width * 0.40), 0, image.width, image.height))
            return pytesseract.image_to_string(image, config="--psm 6")
        except Exception:
            return ""

    @staticmethod
    def _configure_tesseract(pytesseract: Any) -> None:
        configured = Path(str(pytesseract.pytesseract.tesseract_cmd))
        if configured.exists():
            return
        for candidate in (
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ):
            if candidate.exists():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return

    @staticmethod
    def _grab_rect(rect: Any) -> Any:
        from PIL import ImageGrab

        return ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom),
            all_screens=True,
        )

    def _save_stock_screenshot(self, grid: Any) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = Path(__file__).resolve().parents[1] / "reports" / f"stock_grid_{stamp}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._activate_for_visual_read(grid)
            self._grab_rect(grid.rectangle()).save(path)
        except Exception as exc:
            path.write_text(f"Screenshot indisponivel: {exc}", encoding="utf-8")
        return path

    def _activate_for_visual_read(self, control: Any | None = None) -> None:
        """Bring the known PDV/grid to the foreground before screen capture."""
        try:
            self.window.restore()
        except Exception:
            pass
        try:
            self.window.set_focus()
        except Exception:
            pass
        if control is not None:
            try:
                control.set_focus()
            except Exception:
                pass

    def enter_quantity(self, quantity: str | int) -> None:
        """Define a quantidade usando a sequência real ``quantidade*``."""
        edit = self.product_edit
        self._commit_quantity(edit, str(quantity))

    @staticmethod
    def _commit_quantity(edit: Any, quantity: str) -> None:
        """Envia ``quantidade*`` e confirma que o handler VCL consumiu ``*``."""
        deadline_seconds = 3.0
        for asterisk_sequence in ("+8", "*"):
            PdvPage._type_reference_part(edit, quantity)
            try:
                edit.type_keys(asterisk_sequence, set_foreground=True, pause=0.05)
            except Exception:
                continue
            deadline = time.monotonic() + deadline_seconds
            while time.monotonic() < deadline:
                try:
                    if not (edit.window_text() or ""):
                        return
                except Exception:
                    pass
                time.sleep(0.05)
        raise AssertionError(
            f"A tecla * nao confirmou a quantidade {quantity}; "
            "o TEdit de produto nao foi limpo pelo handler EditCodigoProdutoKeyPress"
        )

    @staticmethod
    def _type_reference_part(edit: Any, value: str) -> None:
        try:
            edit.set_focus()
            edit.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.05)
            edit.type_keys(value, set_foreground=True, pause=0.05, with_spaces=True)
        except Exception:
            pass
        if (edit.window_text() or "") != value:
            edit.set_edit_text(value)

    def _legacy_enter_quantity_wait(self, quantity: str | int) -> None:
        """Compatibilidade interna para builds que chamavam o polling antigo."""
        edit = self.product_edit
        deadline = time.monotonic() + min(self.action_timeout, 3.0)
        while time.monotonic() < deadline:
            try:
                if not (edit.window_text() or ""):
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise AssertionError(f"A tecla * nao confirmou a quantidade {quantity}")

    def send_shortcut(self, shortcut: str) -> None:
        """Envia um atalho ao TFrmPDV com foco explícito.

        Os atalhos abaixo correspondem ao ``TFrmPDV.FormKeyDown`` de
        ``PDV.pas``: F5 (CPF/CNPJ), F7 (reimpressão fiscal), F10 (vendedor),
        F11/F12 (desconto), Shift+F4 (tabela), Ctrl+F4 (orçamento), Alt+C
        (cupom) e Ctrl+F8 (relatório de fechamento).
        """
        sequences = {
            "F5": "{F5}",
            "F7": "{F7}",
            "F10": "{F10}",
            "F11": "{F11}",
            "F12": "{F12}",
            "ALT+C": "%c",
            "SHIFT+F4": "+{F4}",
            "CTRL+F4": "^{F4}",
            "CTRL+O": "^o",
            "CTRL+F8": "^{F8}",
        }
        try:
            sequence = sequences[shortcut.upper()]
        except KeyError as exc:
            raise ValueError(f"Atalho nao mapeado: {shortcut}") from exc
        self._ensure_sale_mode()
        self.window.restore()
        self.window.set_focus()
        self.window.type_keys(sequence, set_foreground=True, pause=0.05)

    def wait_for_dialog_text(
        self,
        pattern: str,
        timeout: float | None = None,
    ) -> Any | None:
        """Localiza um diálogo conhecido pelo texto renderizado.

        A busca é limitada às janelas do PID do PDV e exige que o texto seja
        reconhecível. Assim, uma janela sem mapeamento não recebe Enter/ESC
        por tentativa: o chamador pode capturar ``UnknownDialogError`` com a
        árvore de controles para mapeamento manual.
        """
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout
        expression = re.compile(pattern, re.IGNORECASE)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for window in self._top_level_windows():
                try:
                    if self._window_class(window) in {
                        "TFrmPDV", "TFrmAbout", "TFrmPDVCaixaFechado", "TApplication"
                    }:
                        continue
                    if not _is_visible_and_enabled(window):
                        continue
                    if expression.search(self._window_text(window)):
                        return window
                except Exception:
                    continue
            time.sleep(0.1)
        return None

    def wait_for_password_dialog(self, timeout: float | None = None) -> Any | None:
        """Localiza a autorização real ``TFrmPassWord`` pelo PID do PDV."""
        return self._wait_for_top_level_class(
            "TFrmPassWord",
            min(self.action_timeout, 5.0) if timeout is None else timeout,
        )

    def wait_for_information_text(
        self,
        pattern: str,
        timeout: float | None = None,
    ) -> Any | None:
        """Localiza somente mensagens ``TFrmDlgInformacao`` reconhecidas."""
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout
        expression = re.compile(pattern, re.IGNORECASE)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for window in self._top_level_windows():
                try:
                    if self._window_class(window) not in {"TFrmDlgInformacao", "TFrmDlg"}:
                        continue
                    if _is_visible_and_enabled(window) and expression.search(self._window_text(window)):
                        return window
                except Exception:
                    continue
            time.sleep(0.1)
        return None

    @staticmethod
    def dialog_edits(dialog: Any) -> list[Any]:
        """Retorna entradas VCL visíveis e habilitadas, em ordem de tela."""
        controls: list[Any] = []
        for class_name in ("TEdit", "TJvValidateEdit", "TMemo", "TComboBox"):
            try:
                controls.extend(
                    control
                    for control in dialog.descendants(class_name=class_name)
                    if _is_visible_and_enabled(control)
                )
            except Exception:
                continue
        try:
            controls.sort(key=lambda control: control.rectangle().top)
        except Exception:
            pass
        return controls

    def fill_known_dialog(self, dialog: Any, value: str) -> Any:
        """Preenche o primeiro campo de um diálogo já reconhecido pelo texto."""
        controls = self.dialog_edits(dialog)
        if not controls:
            raise capture_unknown_state(dialog, "known_dialog_without_input_control")
        control = controls[0]
        try:
            control.set_focus()
            control.type_keys("^{A}", set_foreground=True, pause=0.05)
            control.type_keys(str(value), set_foreground=True, pause=0.05, with_spaces=True)
        except Exception:
            pass
        if hasattr(control, "set_edit_text"):
            control.set_edit_text(str(value))
        return control

    def fill_client_document(self, dialog: Any, document: str) -> Any:
        """Preenche o campo CPF/CNPJ do formulário ``CPF/CNPJ do Cliente``.

        ``PDV.pas`` (``TFrmPDV.FormKeyDown``) encaminha F5 para
        ``InformarCPFOuCNPJParaVendaAtual(False)``; essa rotina abre o
        formulário da unit ``CPFCNPJ``. O DFM dessa unit não está entre os
        fontes disponíveis, então o campo é identificado no runtime como o
        primeiro ``TEdit`` visível, no topo da janela reconhecida, que é o
        campo sob o rótulo ``CPF/CNPJ`` mostrado no formulário.
        """
        controls = self.dialog_edits(dialog)
        if not controls:
            raise capture_unknown_state(dialog, "client_document_without_input_control")
        try:
            controls.sort(key=lambda control: (
                control.rectangle().top,
                control.rectangle().left,
            ))
        except Exception:
            pass
        control = controls[0]
        value = str(document).strip()
        try:
            dialog.set_focus()
        except Exception:
            pass
        try:
            control.set_focus()
            control.click_input()
            # O formulário TFrmCPFCNPJ recebe a entrada somente depois que o
            # TEdit superior foi realmente clicado; repetir o foco após o
            # clique evita que o foco permaneça no botão F10 - OK.
            control.set_focus()
            control.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.05)
            control.type_keys(value, set_foreground=True, pause=0.06)
        except Exception:
            pass
        if hasattr(control, "set_edit_text"):
            control.set_edit_text(value)
        return control

    def confirm_client_dialog(self, dialog: Any) -> None:
        """Confirma o formulário de cliente pelo botão ``F10 - OK``.

        O F10 é enviado ao próprio formulário somente quando o botão não é
        exposto pela árvore Win32. Assim ele não chega ao ``TFrmPDV`` e não
        dispara ``SolicitarVendedor`` acidentalmente.
        """
        self._observe_dialog(dialog, "before_confirm_client_dialog")
        accepted = re.compile(r"(?:f10\s*-\s*)?&?ok", re.IGNORECASE)
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                try:
                    if not _is_visible_and_enabled(button):
                        continue
                    caption = button.window_text() or ""
                    if not accepted.fullmatch(caption.strip()):
                        continue
                    try:
                        button.click_input()
                    except Exception:
                        button.click()
                    return
                except Exception:
                    continue
        try:
            dialog.set_focus()
            dialog.type_keys("{F10}", set_foreground=True, pause=0.05)
        except Exception as exc:
            raise capture_unknown_state(dialog, "client_dialog_without_f10_ok") from exc

    def confirm_known_dialog(self, dialog: Any, *, allow_cancel: bool = False) -> None:
        """Confirma apenas botões com caption conhecido no diálogo mapeado."""
        self._observe_dialog(dialog, "before_confirm_known_dialog")
        accepted = r"^&?(?:OK|Sim|Confirmar|Aplicar|Salvar|Entrar|Acessar)$"
        if allow_cancel:
            accepted = r"^&?(?:OK|Sim|Confirmar|Aplicar|Salvar|Entrar|Acessar|Fechar)$"
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                if not _is_visible_and_enabled(button):
                    continue
                if not re.search(accepted, button.window_text() or "", re.IGNORECASE):
                    continue
                try:
                    button.click_input()
                except Exception:
                    button.click()
                # TFrmDlgInformacao can keep the HWND alive for a repaint
                # cycle. Retry only the same mapped dialog, never an unknown
                # window, and do not swallow our own diagnostic exception.
                time.sleep(0.25)
                try:
                    still_open = bool(dialog.is_visible() and dialog.is_enabled())
                except Exception:
                    still_open = False
                if still_open:
                    try:
                        dialog.set_focus()
                        press(dialog, "ENTER")
                        time.sleep(0.25)
                    except Exception:
                        pass
                try:
                    if dialog.is_visible() and dialog.is_enabled():
                        raise capture_unknown_state(dialog, "known_dialog_confirmation_not_closed")
                except AttributeError:
                    pass
                except Exception:
                    # The VCL information form commonly destroys its HWND
                    # immediately after OK. InvalidWindowHandle here means
                    # successful dismissal, not an unknown modal.
                    return
                return
        try:
            dialog.set_focus()
            press(dialog, "ENTER")
        except Exception as exc:
            raise capture_unknown_state(dialog, "known_dialog_without_confirm_control") from exc

    def select_known_dialog_option(self, dialog: Any, option: str) -> None:
        """Seleciona ``option`` por texto em combo/lista VCL, nunca por posição."""
        wanted = self._normalize_payment_method(str(option))
        for class_name in ("TComboBox", "TListBox", "TJvComboEdit"):
            try:
                controls = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for control in controls:
                try:
                    if not _is_visible_and_enabled(control):
                        continue
                    if class_name == "TJvComboEdit":
                        # TFormTabelaPreco usa TJvComboEdit para a tabela
                        # (confirmado no runtime; não é TComboBox/TListBox).
                        control.set_focus()
                        control.set_edit_text(str(option))
                        return
                    if class_name == "TComboBox":
                        items = list(control.item_texts())
                        index = self._payment_method_index(items, str(option))
                        if index is None:
                            continue
                        try:
                            control.select(items[index])
                        except Exception:
                            control.set_focus()
                            press(control, "HOME")
                            for _ in range(index):
                                press(control, "DOWN")
                        return
                    items = list(control.item_texts())
                    index = self._payment_method_index(items, str(option))
                    if index is None:
                        continue
                    control.set_focus()
                    press(control, "HOME")
                    for _ in range(index):
                        press(control, "DOWN")
                    if not self._payment_selection_is(control, index):
                        raise capture_unknown_state(dialog, "dialog_option_selection_mismatch")
                    return
                except Exception:
                    continue
        raise capture_unknown_state(dialog, f"dialog_option_not_found_{wanted}")

    def dismiss_known_information(self, timeout: float | None = None) -> str:
        """Lê e confirma um TFrmDlgInformacao que já esteja identificado."""
        dialog = self.wait_for_dialog_text(r".", timeout=timeout)
        if dialog is None:
            return ""
        text = self._window_text(dialog)
        if self._window_class(dialog) not in {"TFrmDlgInformacao", "TFrmDlg"}:
            raise capture_unknown_state(dialog, "information_dialog_class_unmapped")
        self._observe_dialog(dialog, "before_dismiss_known_information")
        self.confirm_known_dialog(dialog)
        return text

    def items_text(self) -> str:
        return self.items_memo.window_text()

    def has_item(self, code: str, timeout: float | None = None, interval: float = 0.3) -> bool:
        """Poll the items memo until the product is visible or timeout expires."""
        timeout = max(3.0, self.action_timeout * 2) if timeout is None else timeout
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                # Exige a linha renderizada do item (indice + codigo), evitando
                # falso positivo com numeros do cabecalho ou dos totais.
                if self.item_count(code) > 0:
                    return True
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, interval))

    def wait_until_absent(self, code: str, timeout: float | None = None, interval: float = 0.3) -> bool:
        """Poll the items memo until the product is gone or timeout expires."""
        timeout = max(3.0, self.action_timeout * 2) if timeout is None else timeout
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                if self.item_count(code) == 0:
                    return True
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, interval))

    def item_count(self, code: str) -> int:
        """Count product rows, excluding the quantity/value columns."""
        pattern = re.compile(rf"^\s*\d+\s+0*{re.escape(code)}\b", re.MULTILINE)
        try:
            return len(pattern.findall(self.items_text()))
        except Exception:
            return 0

    def wait_until_item_count(
        self, code: str, expected: int, timeout: float | None = None, interval: float = 0.3
    ) -> bool:
        """Poll until the expected number of product rows is rendered."""
        timeout = max(3.0, self.action_timeout * 2) if timeout is None else timeout
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self.item_count(code) == expected:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, interval))

    def has_cancelled_item(self, index: str) -> bool:
        """Return whether the memo contains a negative row for an item index."""
        row_pattern = re.compile(rf"^\s*0*{re.escape(index)}\s+", re.MULTILINE)
        negative_pattern = re.compile(r"^\s*-\d+(?:[,.]\d+)?\s+X\s+", re.MULTILINE)
        lines = self.items_text().splitlines()
        for position, line in enumerate(lines):
            if row_pattern.search(line):
                following = "\n".join(lines[position + 1:position + 3])
                if negative_pattern.search(following):
                    return True
        return False

    def wait_until_cancelled(
        self, index: str, timeout: float | None = None, interval: float = 0.3
    ) -> bool:
        """Poll until the negative cancellation row is rendered."""
        timeout = max(3.0, self.action_timeout * 2) if timeout is None else timeout
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                if self.has_cancelled_item(index):
                    return True
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, interval))

    def total_labels(self, caption: str) -> list[Any]:
        # VCL may expose the caption through window_text without preserving
        # it as the Win32 title property. Search the rendered label tree.
        try:
            labels = list(self.window.descendants(class_name="TLabel"))
        except Exception:
            labels = []
        if not labels:
            try:
                labels = list(self.window.descendants())
            except Exception:
                labels = []
        wanted = _normalize_ui_text(caption)
        return [
            label
            for label in labels
            if _is_visible(label) and _normalize_ui_text(_control_text(label)) == wanted
        ]

    def value_next_to_caption(self, caption: str) -> Decimal:
        captions = self.total_labels(caption)
        if caption == "Valor Total a Pagar R$":
            panel_value = self._read_total_panel()
            if panel_value is not None:
                return panel_value
            memo_value = self._total_from_items_memo()
            if memo_value is not None:
                self.last_total_read_method = "items_memo"
                return memo_value
        elif not captions:
            memo_value = self._total_from_items_memo()
            if memo_value is not None:
                return memo_value
        if not captions:
            raise AssertionError(f"Caption de total não encontrada: {caption}")
        parent = captions[0].parent()
        labels = [label for label in parent.descendants(class_name="TLabel") if _is_visible(label)]
        values = [label.window_text() for label in labels if label.window_text() != caption]
        if not values:
            raise AssertionError(f"Valor do total não exposto após {caption}")
        return parse_money(values[-1])

    def _read_total_panel(self) -> Decimal | None:
        """Read LabTotalVenda using the least invasive available mechanism.

        TLabel is painted by VCL and has no HWND in this build. The method
        therefore tries the containing panel, its Win32 text, UIA, and only
        then OCR of the panel rectangle. The selected panel is the compact
        bottom panel inside the left-side totalizers, identified relative to
        the main form rather than by a hard-coded screen coordinate.
        """
        panel = self._find_total_panel()
        if panel is None:
            return None

        # 1. Parent text: useful for VCL/custom builds that propagate the
        # painted label value to the panel wrapper.
        values = self._money_values(self._control_texts(panel))
        if values:
            self.last_total_read_method = "panel_texts"
            return values[-1]

        # 2. Direct WM_GETTEXT on the panel HWND.
        try:
            handle = int(panel.handle)
            text = self._wm_gettext(handle)
        except Exception:
            text = ""
        values = self._money_values(text)
        if values:
            self.last_total_read_method = "wm_gettext"
            return values[-1]

        # 3. UI Automation. Some installations expose painted VCL labels
        # through UIA even though the win32 backend does not.
        values = self._uia_money_values(panel)
        if values:
            self.last_total_read_method = "uia"
            return values[-1]

        # 4. Last resort: OCR only the total panel, never the whole desktop.
        value = self._ocr_total_panel(panel)
        if value is not None:
            self.last_total_read_method = "ocr"
        return value

    def _find_total_panel(self) -> Any | None:
        try:
            main_rect = self.window.rectangle()
            main_width = max(1, main_rect.width())
            main_height = max(1, main_rect.height())
        except Exception:
            return None

        candidates: list[tuple[int, Any]] = []
        try:
            panels = self.window.descendants(class_name="TPanel")
        except Exception:
            return None
        for panel in panels:
            try:
                if not panel.is_visible():
                    continue
                rect = panel.rectangle()
                width = rect.width()
                height = rect.height()
                rel_left = rect.left - main_rect.left
                rel_top = rect.top - main_rect.top
                # DFM: pnlValorTotalAPagar is a compact panel at the bottom
                # of pnlTotalizadores. This excludes the full-width footer
                # and unrelated right-side value panels.
                if not (0 <= rel_left < main_width * 0.5):
                    continue
                if not (main_height * 0.75 < rel_top < main_height):
                    continue
                if not (main_width * 0.15 <= width <= main_width * 0.5):
                    continue
                if not (40 <= height <= 130):
                    continue
                candidates.append((rel_top, panel))
            except Exception:
                continue
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _control_texts(control: Any) -> str:
        texts: list[str] = []
        for target in (control,):
            try:
                texts.extend(str(value or "") for value in target.texts())
            except Exception:
                try:
                    texts.append(str(target.window_text() or ""))
                except Exception:
                    pass
        try:
            for child in control.descendants():
                try:
                    texts.extend(str(value or "") for value in child.texts())
                except Exception:
                    try:
                        texts.append(str(child.window_text() or ""))
                    except Exception:
                        pass
        except Exception:
            pass
        return "\n".join(texts)

    @staticmethod
    def _wm_gettext(handle: int) -> str:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(max(256, length + 1))
        user32.GetWindowTextW(handle, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _money_values(text: str) -> list[Decimal]:
        matches = re.findall(
            r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3})*|\d+)[,.]\d{2}(?!\d)",
            text or "",
        )
        values: list[Decimal] = []
        for match in matches:
            try:
                values.append(parse_money(match))
            except AssertionError:
                continue
        return values

    def _uia_money_values(self, panel: Any) -> list[Decimal]:
        if Desktop is None:
            return []
        try:
            uia_panel = Desktop(backend="uia").window(handle=panel.handle)
            texts: list[str] = [uia_panel.window_text() or ""]
            texts.extend(child.window_text() or "" for child in uia_panel.descendants())
            return self._money_values("\n".join(texts))
        except Exception:
            return []

    def _ocr_total_panel(self, panel: Any) -> Decimal | None:
        try:
            from PIL import ImageGrab
            import pytesseract

            configured_tesseract = Path(str(pytesseract.pytesseract.tesseract_cmd))
            if not configured_tesseract.exists():
                for candidate in (
                    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
                ):
                    if candidate.exists():
                        pytesseract.pytesseract.tesseract_cmd = str(candidate)
                        break

            rect = panel.rectangle()
            image = ImageGrab.grab(
                bbox=(rect.left, rect.top, rect.right, rect.bottom),
                all_screens=True,
            )
            text = pytesseract.image_to_string(
                image,
                config="--psm 6 -c tessedit_char_whitelist=0123456789,.-",
            )
            values = self._money_values(text)
            return values[-1] if values else None
        except Exception:
            # OCR is optional. wait_until_total() will retain its evidence
            # capture if no supported OCR engine is installed.
            return None

    def _total_from_items_memo(self) -> Decimal | None:
        """Read the fallback total rendered in pnlProdutos."""
        try:
            text = self.items_text()
        except Exception:
            return None
        match = re.search(
            r"(?im)^\s*total\s*(?:r\$)?\s*[:=]?\s*([0-9][0-9.]*,[0-9]{2}|[0-9]+\.[0-9]{2})\s*$",
            text,
        )
        if not match:
            return None
        try:
            return parse_money(match.group(1))
        except AssertionError:
            return None

    def subtotal(self) -> Decimal:
        return self.value_next_to_caption("Subtotal R$")

    def total(self) -> Decimal:
        return self.value_next_to_caption("Valor Total a Pagar R$")

    def wait_until_total(self, timeout: float | None = None, interval: float = 0.2) -> Decimal:
        """Aguarda o repaint dos totalizadores e retorna o total da venda."""
        timeout = max(3.0, self.action_timeout * 2) if timeout is None else timeout
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.total()
            except Exception as exc:
                last_error = exc
            time.sleep(max(0.01, interval))
        raise capture_unknown_state(self.window, "sale_total_unresolved") from last_error

    def discount(self) -> Decimal:
        try:
            return self.value_next_to_caption("Desconto R$")
        except AssertionError:
            # LabTotalDesconto is a painted TLabel and is not exposed as a
            # child HWND in this build. The real TFrmPDV fallback is the
            # rendered row in TMemo pnlProdutos, e.g. ``Desconto: R$ 5,00``
            # or ``Desconto: 10,00% R$ 0,10``.
            text = self.items_text()
            matches = re.findall(
                r"desconto\s*:\s*(?:[0-9.,]+%\s*)?R\$\s*([0-9][0-9.,]*)",
                text,
                flags=re.IGNORECASE,
            )
            if matches:
                self.last_total_read_method = "items_memo_discount"
                return parse_money(matches[-1])
            raise

    def finalize_sale(
        self,
        payment_method: str = "Dinheiro",
        amount: str | None = None,
        expected_product: str | None = None,
        expected_total: Decimal | str | None = None,
        show_receipt_again: bool = False,
        installments: str | int = 1,
        timeout: float | None = None,
        validate_pre_payment_summary: bool = True,
        validate_receipt: bool = False,
    ) -> None:
        """Finaliza uma venda pela tela real de pagamentos do SATPDV.

        A forma de pagamento e selecionada por teclado porque o TListBox VCL
        pode receber apenas o destaque visual quando clicado com o mouse.
        Para o valor integral, o segundo ENTER confirma o valor ja exibido.
        """
        self.last_sale_submitted = False
        timeout = min(self.action_timeout, 10.0) if timeout is None else timeout
        # Validate while the live TFrmPDV memo still contains the item. This
        # build clears that memo before opening its PrintPreview window.
        summary_already_validated = expected_product is not None or expected_total is not None
        if summary_already_validated and validate_pre_payment_summary:
            self._validate_sale_summary(expected_product, expected_total)
        press(self.window, "F3")
        payment = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if payment is None:
            raise capture_unknown_state(self.window, "payment_form_not_open")

        payment_list = self._payment_method_list(payment)
        if payment_list is None:
            raise capture_unknown_state(payment, "payment_method_list_missing")

        items = payment_list.item_texts()
        initial_method = "Cartao ou TEF" if self._is_card_branch(payment_method) else payment_method
        index = self._payment_method_index(items, initial_method)
        if index is None:
            raise AssertionError(
                f"Forma de pagamento {initial_method!r} nao encontrada; opcoes: {items!r}"
            )

        payment_list.set_focus()
        press(payment_list, "HOME")
        for _ in range(index):
            press(payment_list, "DOWN")
        if not self._payment_selection_is(payment_list, index):
            raise capture_unknown_state(payment, "payment_method_selection_mismatch")

        # Primeiro ENTER abre o campo de valor da forma selecionada.
        press(payment_list, "ENTER")
        time.sleep(0.25)
        amount_control = self._focused_payment_control(payment)
        if amount is not None:
            if amount_control is None:
                raise capture_unknown_state(payment, "payment_amount_field_missing")
            amount_control.set_edit_text(str(amount))

        if expected_total is not None:
            if amount_control is None:
                raise capture_unknown_state(payment, "payment_amount_field_missing")
            try:
                amount_text = amount_control.window_text() or ""
                observed_amount = parse_money(amount_text)
            except (AssertionError, ValueError):
                raise capture_unknown_state(payment, "payment_amount_unreadable")
            expected_amount = Decimal(str(expected_total)).quantize(Decimal("0.01"))
            if observed_amount != expected_amount:
                raise AssertionError(
                    f"Valor enviado para pagamento divergente; "
                    f"esperado={expected_amount}, encontrado={observed_amount}"
                )

        # Segundo ENTER confirma o valor integral ou o valor informado.
        press(amount_control or payment, "ENTER")
        if self._is_card_branch(payment_method):
            self._select_card_type(payment, payment_method, installments, timeout)

        # A payment disappearing means that the sale has already been sent to
        # the PDV. From this point forward, receipt validation must never
        # trigger cancel_sale as a cleanup action.
        sale_submitted = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._wait_for_top_level_class("TFrmInserirPgto", 0.1) is None:
                sale_submitted = True
                self.last_sale_submitted = True
                receipt = self._find_receipt_window()
                if receipt is not None:
                    if validate_receipt:
                        self._validate_and_close_receipt(
                            receipt,
                            expected_product,
                            expected_total,
                            summary_already_validated=summary_already_validated,
                        )
                    else:
                        # A leitura detalhada do TppViewer/OCR está fora do
                        # escopo desta rodada; ainda fechamos a janela real e
                        # validamos PDV_READY abaixo.
                        self._close_receipt(receipt)
                self._handle_receipt_repeat_prompt(
                    show_again=show_receipt_again,
                    expected_product=expected_product,
                    expected_total=expected_total,
                    summary_already_validated=summary_already_validated,
                    timeout=timeout,
                )
                main = self._wait_for_top_level_class("TFrmPDV", 0.1)
                if main is not None and _is_visible_and_enabled(main):
                    self.window = main
                    if not summary_already_validated:
                        self._validate_sale_summary(expected_product, expected_total)
                    return
            time.sleep(0.1)

        raise capture_unknown_state(self.window, "payment_completion_unresolved")

    def _handle_receipt_repeat_prompt(
        self,
        show_again: bool,
        expected_product: str | None,
        expected_total: Decimal | str | None,
        timeout: float,
        summary_already_validated: bool = False,
    ) -> bool:
        """Responde ao modal de venda em finalizacao sem usar Enter ambiguo."""
        prompt = None
        for window in self._top_level_windows():
            try:
                text = self._window_text(window)
                if re.search(r"existe\s+uma\s+venda\s+em\s+processo\s+de\s+finaliza", text, re.IGNORECASE):
                    prompt = window
                    break
            except Exception:
                continue
        if prompt is None:
            return False

        desired = "sim" if show_again else "nao"
        for class_name in ("TBitBtn", "TButton"):
            try:
                buttons = prompt.descendants(class_name=class_name)
            except Exception:
                buttons = []
            for button in buttons:
                try:
                    caption = self._normalize_payment_method(button.window_text() or "")
                    if _is_visible_and_enabled(button) and caption in {"sim", "nao"}:
                        if caption != desired:
                            continue
                        button.click_input()
                        if show_again:
                            receipt = self._wait_for_receipt(timeout)
                            if receipt is not None:
                                self._validate_and_close_receipt(
                                    receipt,
                                    expected_product,
                                    expected_total,
                                    summary_already_validated=summary_already_validated,
                                )
                        return True
                except Exception:
                    continue
        raise capture_unknown_state(prompt, "receipt_repeat_prompt_controls")

    def _wait_for_receipt(self, timeout: float) -> Any | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = self._find_receipt_window()
            if receipt is not None:
                return receipt
            time.sleep(0.1)
        return None

    def _find_receipt_window(self) -> Any | None:
        """Localiza o comprovante/modal posterior ao fechamento do pagamento."""
        for window in self._top_level_windows():
            try:
                if not _is_visible_and_enabled(window):
                    continue
                class_name = window.class_name()
                if class_name in {"TFrmPDV", "TFrmAbout", "TFrmInserirPgto"}:
                    continue
                if class_name == "TFrmDlgInformacao":
                    continue
                return window
            except Exception:
                continue
        return None

    def _validate_and_close_receipt(
        self,
        receipt: Any,
        expected_product: str | None,
        expected_total: Decimal | str | None,
        summary_already_validated: bool = False,
    ) -> None:
        text = self._window_text(receipt)
        normalized = re.sub(r"\s+", " ", text).strip()
        try:
            self._validate_sale_text(normalized, expected_product, expected_total, "comprovante")
        except AssertionError:
            # The known FastReport/PrintPreview window exposes only its
            # chrome (for example, "Outline") through Win32. The rendered
            # receipt data remains available in the already completed
            # TFrmPDV summary, so validate that evidence before closing the
            # preview instead of treating an unreadable preview as a bad sale.
            if receipt.class_name() != "TppPrintPreview":
                raise
            if not summary_already_validated:
                self._validate_sale_summary(expected_product, expected_total)
        self._close_receipt(receipt)

    def _close_receipt(self, receipt: Any) -> None:
        """Fecha TppPrintPreview sem inferir conteúdo gráfico do comprovante."""
        closed = False
        for class_name in ("TBitBtn", "TButton"):
            try:
                buttons = receipt.descendants(class_name=class_name)
            except Exception:
                buttons = []
            for button in buttons:
                try:
                    if (
                        _is_visible_and_enabled(button)
                        and re.search(r"close|fechar|OK", button.window_text() or "", re.IGNORECASE)
                    ):
                        button.click_input()
                        closed = True
                        break
                except Exception:
                    continue
            if closed:
                break
        if not closed:
            try:
                receipt.set_focus()
                receipt.type_keys("{ESC}", set_foreground=True)
                closed = True
            except Exception:
                pass
        if not closed:
            raise capture_unknown_state(receipt, "receipt_close_control_missing")

    def _validate_sale_summary(
        self,
        expected_product: str | None,
        expected_total: Decimal | str | None,
    ) -> None:
        if expected_product is None and expected_total is None:
            return
        text = re.sub(r"\s+", " ", self.items_text()).strip()
        self._validate_sale_text(text, expected_product, expected_total, "resumo da venda")

    @staticmethod
    def _validate_sale_text(
        text: str,
        expected_product: str | None,
        expected_total: Decimal | str | None,
        source: str,
    ) -> None:
        if expected_product is not None:
            if re.search(rf"(?<!\d)0*{re.escape(str(expected_product))}(?!\d)", text) is None:
                raise AssertionError(f"Produto {expected_product} nao apareceu no {source}")
        if expected_total is not None:
            try:
                value = Decimal(str(expected_total)).quantize(Decimal("0.01"))
                candidates = {f"{value:.2f}", f"{value:.2f}".replace(".", ",")}
            except (InvalidOperation, ValueError):
                candidates = {str(expected_total)}
            if not any(candidate in text for candidate in candidates):
                raise AssertionError(f"Total esperado {expected_total} nao apareceu no {source}")

    @staticmethod
    def _window_text(window: Any) -> str:
        texts: list[str] = []
        try:
            texts.append(window.window_text() or "")
            texts.extend(child.window_text() or "" for child in window.descendants())
        except Exception:
            pass
        return " ".join(texts)

    @staticmethod
    def _normalize_payment_method(value: str) -> str:
        value = value.casefold()
        replacements = str.maketrans("áàãâäéèêëíìîïóòõôöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
        return re.sub(r"[^a-z0-9]+", " ", value.translate(replacements)).strip()

    @classmethod
    def _payment_method_index(cls, items: list[str], desired: str) -> int | None:
        wanted = cls._normalize_payment_method(desired)
        wanted_tokens = set(wanted.split())
        for index, item in enumerate(items):
            normalized = cls._normalize_payment_method(item)
            if wanted == normalized or wanted in normalized:
                return index
            if wanted_tokens and wanted_tokens.issubset(set(normalized.split())):
                return index
        return None

    @staticmethod
    def _payment_method_list(payment: Any) -> Any | None:
        try:
            lists = [
                control
                for control in payment.descendants(class_name="TListBox")
                if control.is_visible() and control.is_enabled()
            ]
            return lists[0] if lists else None
        except Exception:
            return None

    def open_payment_amount_field(
        self, payment_method: str = "Dinheiro", timeout: float | None = None
    ) -> tuple[Any, Any]:
        """Open ``TFrmInserirPgto`` and return its real amount TEdit.

        The first payment selection is a ``TListBox`` in ``TFrmInserirPgto``;
        navigation with HOME/DOWN/ENTER updates the VCL logical selection.
        The second ENTER is deliberately left to the caller so boundary and
        input-validation scenarios can inspect the field before submission.
        """
        timeout = min(self.action_timeout, 10.0) if timeout is None else timeout
        self._ensure_sale_mode()
        self.window.set_focus()
        press(self.window, "F3")
        payment = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if payment is None:
            raise capture_unknown_state(self.window, "payment_form_not_open")
        payment_list = self._payment_method_list(payment)
        if payment_list is None:
            raise capture_unknown_state(payment, "payment_method_list_missing")
        items = payment_list.item_texts()
        index = self._payment_method_index(items, payment_method)
        if index is None:
            raise AssertionError(
                f"Forma de pagamento {payment_method!r} nao encontrada; opcoes: {items!r}"
            )
        payment_list.set_focus()
        press(payment_list, "HOME")
        for _ in range(index):
            press(payment_list, "DOWN")
        if not self._payment_selection_is(payment_list, index):
            raise capture_unknown_state(payment, "payment_method_selection_mismatch")
        press(payment_list, "ENTER")
        time.sleep(0.25)
        amount_control = self._focused_payment_control(payment)
        if amount_control is None:
            raise capture_unknown_state(payment, "payment_amount_field_missing")
        return payment, amount_control

    def open_card_installments(
        self, payment_method: str = "Mastercard Credito", timeout: float | None = None
    ) -> tuple[Any, Any]:
        """Open the installment field of the mapped card payment branch."""
        payment, amount_control = self.open_payment_amount_field("Cartao ou TEF", timeout)
        # The first ENTER confirms the amount field. The second list is still
        # the VCL TListBox in TFrmInserirPgto and must be selected by text.
        press(amount_control, "ENTER")
        time.sleep(0.25)
        current = self._wait_for_top_level_class("TFrmInserirPgto", timeout or self.action_timeout)
        if current is None:
            raise capture_unknown_state(payment, "card_payment_form_not_open")
        card_list = self._payment_method_list(current)
        if card_list is None:
            raise capture_unknown_state(current, "card_type_list_missing")
        options = card_list.item_texts()
        index = self._payment_method_index(options, payment_method)
        if index is None:
            raise AssertionError(
                f"Tipo de cartao {payment_method!r} nao encontrado; opcoes: {options!r}"
            )
        card_list.set_focus()
        press(card_list, "HOME")
        for _ in range(index):
            press(card_list, "DOWN")
        if not self._payment_selection_is(card_list, index):
            raise capture_unknown_state(current, "card_type_selection_mismatch")
        press(card_list, "ENTER")
        time.sleep(0.25)
        installments = self._focused_payment_control(current)
        if installments is None:
            raise capture_unknown_state(current, "card_installments_field_missing")
        return current, installments

    def close_payment_dialog(self) -> None:
        """Close the known ``TFrmInserirPgto`` without touching unknown modals."""
        payment = self._wait_for_top_level_class("TFrmInserirPgto", 0.5)
        if payment is None:
            return
        try:
            payment.set_focus()
            press(payment, "ESC")
            time.sleep(0.2)
        except Exception:
            pass
        try:
            if payment.is_visible():
                payment.close()
        except Exception:
            pass

    def search_product_by_description(self, term: str, timeout: float | None = None) -> str:
        """Query the real ``TDlgProd`` product list by description.

        In the homologation ``SATTESTES`` build, ``GridProcura`` remains
        hidden by the ``{$IFNDEF SATTESTES}`` guard in ``PDV.pas``.  The actual
        route is Shift+F1 -> ``TDlgProd`` ("Lista de Produtos"): its
        ``TComboBox`` is set to ``3 - Descrição``, the search ``TEdit`` is
        filled, and the mapped ``TBitBtn`` ``F2 - Consultar`` loads the
        result ``TDBGrid``.
        """
        term = str(term).strip()
        if not term:
            raise ValueError("O termo de descricao nao pode ser vazio")
        timeout = min(self.action_timeout, 6.0) if timeout is None else timeout
        self._ensure_sale_mode()
        edit = self.product_edit
        try:
            edit.set_focus()
            edit.type_keys("+{F1}", set_foreground=True, pause=0.05)
        except Exception:
            press(edit, "SHIFT+F1")
        deadline = time.monotonic() + timeout
        picker = None
        while time.monotonic() < deadline:
            for dialog in self._top_level_windows():
                try:
                    if self._window_class(dialog) == "TDlgProd" and _is_visible_and_enabled(dialog):
                        picker = dialog
                        break
                except Exception:
                    continue
            if picker is not None:
                break
            time.sleep(0.1)
        if picker is None:
            raise capture_unknown_state(self.window, "VEN-37_product_picker_not_open")

        # The runtime inventory maps this selector to TComboBox caption
        # ``3 - Descrição`` and the following search input to a TEdit without
        # a caption. Use the control geometry relative to TDlgProd only to
        # distinguish it from the unrelated filter edits.
        description_combo = None
        for combo in picker.descendants(class_name="TComboBox"):
            try:
                if _is_visible_and_enabled(combo) and "descri" in self._ascii(combo.window_text()):
                    description_combo = combo
                    break
            except Exception:
                continue
        if description_combo is None:
            raise capture_unknown_state(picker, "VEN-37_description_filter_missing")
        try:
            description_combo.select("3 - Descrição")
        except Exception:
            description_combo.set_focus()
            press(description_combo, "HOME")
            for _ in range(3):
                press(description_combo, "DOWN")

        picker_rect = picker.rectangle()
        search_edits = []
        for candidate in picker.descendants(class_name="TEdit"):
            try:
                rect = candidate.rectangle()
                rel_top = rect.top - picker_rect.top
                if (
                    _is_visible_and_enabled(candidate)
                    and rel_top > picker_rect.height() * 0.32
                    and rel_top < picker_rect.height() * 0.50
                    and rect.width() > picker_rect.width() * 0.12
                ):
                    search_edits.append(candidate)
            except Exception:
                continue
        if not search_edits:
            raise capture_unknown_state(picker, "VEN-37_description_search_edit_missing")
        search_edit = min(search_edits, key=lambda control: control.rectangle().top)
        self._type_reference_part(search_edit, term)

        query_button = None
        for button in picker.descendants(class_name="TBitBtn"):
            try:
                if _is_visible_and_enabled(button) and self._ascii(button.window_text()).startswith("f2"):
                    query_button = button
                    break
            except Exception:
                continue
        if query_button is None:
            raise capture_unknown_state(picker, "VEN-37_description_query_button_missing")
        query_button.click_input()

        last_text = ""
        while time.monotonic() < deadline:
            grids = []
            try:
                grids = [
                    grid for grid in picker.descendants(class_name="TDBGrid")
                    if _is_visible(grid) and grid.rectangle().width() > picker_rect.width() * 0.5
                ]
            except Exception:
                pass
            for grid in grids:
                try:
                    candidates = (
                        self._control_texts(grid),
                        self._uia_grid_text(grid),
                        self._ocr_product_list_grid(grid),
                    )
                    for text in candidates:
                        if not text:
                            continue
                        last_text = text
                        if self._ascii(term) in self._ascii(text):
                            self._close_product_picker(picker)
                            return text
                except Exception:
                    continue
            # TDBGrid can report Enabled=False while its VCL canvas is still
            # painted and readable. In that case use the largest grid's
            # rectangle as the same, narrowly-scoped OCR source.
            if not grids:
                try:
                    candidates = [
                        grid for grid in picker.descendants(class_name="TDBGrid")
                        if _is_visible(grid)
                    ]
                    if candidates:
                        grid = max(candidates, key=lambda item: item.rectangle().width())
                        text = self._ocr_product_list_grid(grid)
                        last_text = text
                        if self._ascii(term) in self._ascii(text):
                            self._close_product_picker(picker)
                            return text
                except Exception:
                    pass
            time.sleep(0.2)
        raise capture_unknown_state(picker, "VEN-37_description_result_not_found")

    def _ocr_product_list_grid(self, grid: Any) -> str:
        """OCR the populated TDlgProd result grid at a readable scale."""
        try:
            from PIL import ImageOps
            import pytesseract

            self._configure_tesseract(pytesseract)
            image = ImageOps.grayscale(self._grab_rect(grid.rectangle()))
            crop_height = min(image.height, max(220, int(image.height * 0.90)))
            image = image.crop((0, 0, image.width, crop_height))
            image = image.resize((image.width * 4, image.height * 4))
            return pytesseract.image_to_string(image, config="--psm 6")
        except Exception:
            return ""

    def _close_product_picker(self, picker: Any) -> None:
        """Close the known TDlgProd through its explicit Cancelar button."""
        for button in picker.descendants(class_name="TBitBtn"):
            try:
                if _is_visible_and_enabled(button) and self._ascii(button.window_text()).strip("& ") == "cancelar":
                    button.click_input()
                    return
            except Exception:
                continue

    @staticmethod
    def _payment_selection_is(payment_list: Any, expected: int) -> bool:
        try:
            return tuple(payment_list.selected_indices()) == (expected,)
        except Exception:
            try:
                return payment_list.get_item_focus() == expected
            except Exception:
                return False

    @staticmethod
    def _focused_payment_control(payment: Any) -> Any | None:
        try:
            focused = payment.get_focus()
            if focused.class_name() in {"TJvValidateEdit", "TEdit"}:
                return focused
        except Exception:
            pass
        return None

    @classmethod
    def _is_card_branch(cls, payment_method: str) -> bool:
        normalized = cls._normalize_payment_method(payment_method)
        return (
            normalized in {"cartao", "tef", "cartao ou tef"}
            or any(token in normalized for token in ("mastercard", "visa", "inter debito"))
        )

    def _select_card_type(
        self,
        payment: Any,
        payment_method: str,
        installments: str | int,
        timeout: float,
    ) -> None:
        """Seleciona explicitamente a segunda lista da aba Cartão.

        O ramo inicial é ``Cartão ou TEF``; depois do valor confirmado, o
        formulário troca para a aba Cartão e expõe as bandeiras/operações.
        Nunca escolhe uma opção por posição quando o chamador não informou o
        tipo, pois isso poderia disparar uma transação TEF diferente da pedida.
        """
        current = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if current is None:
            # A forma pode ter concluído diretamente em uma configuração
            # especial. Não há uma seleção adicional para confirmar nesse caso.
            return

        card_list = self._payment_method_list(current)
        if card_list is None:
            raise capture_unknown_state(current, "card_type_list_missing")
        options = card_list.item_texts()
        desired = payment_method
        if self._normalize_payment_method(payment_method) in {"cartao", "cartao ou tef"}:
            raise AssertionError(
                "Cartao ou TEF abriu a selecao adicional; informe uma opcao explicita. "
                f"Opcoes disponiveis: {options!r}"
            )
        index = self._payment_method_index(options, desired)
        if index is None:
            raise AssertionError(
                f"Tipo de cartao {desired!r} nao encontrado; opcoes: {options!r}"
            )

        card_list.set_focus()
        press(card_list, "HOME")
        for _ in range(index):
            press(card_list, "DOWN")
        if not self._payment_selection_is(card_list, index):
            raise capture_unknown_state(current, "card_type_selection_mismatch")
        press(card_list, "ENTER")
        time.sleep(0.25)
        installment_control = self._focused_payment_control(current)
        if installment_control is None:
            raise capture_unknown_state(current, "card_installments_field_missing")
        installment_control.set_edit_text(str(installments))
        if (installment_control.window_text() or "").strip() != str(installments).strip():
            raise AssertionError("Quantidade de parcelas nao foi retida no campo")
        # O ENTER no campo de parcelas confirma a forma, dispara a etapa de
        # autorizacao configurada e pode abrir o comprovante ou o TEF externo.
        press(installment_control, "ENTER")
        self._wait_for_card_selection_or_completion(current, timeout)

    def _wait_for_card_selection_or_completion(self, payment: Any, timeout: float) -> None:
        """Aguarda a tela de pagamento sair ou uma etapa TEF reconhecível."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self._wait_for_top_level_class("TFrmInserirPgto", 0.1)
            if current is None:
                return
            # Após ENTER a lista pode permanecer visível enquanto o TEF
            # externo inicializa. Não há outro controle interno mapeado que
            # possa ser escolhido com segurança neste ponto.
            time.sleep(0.1)
        raise capture_unknown_state(payment, "card_payment_completion_unresolved")

    def _wait_for_top_level_class(self, class_name: str, timeout: float) -> Any | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            for window in self._top_level_windows():
                try:
                    if window.class_name() == class_name and _is_visible_and_enabled(window):
                        return window
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    def cancel_sale(self, reason: str = "Teste automatizado: cancelamento de venda") -> None:
        """Cancela a venda e confirma o motivo quando o PDV o solicita."""
        if len(reason.strip()) < 15:
            raise ValueError("O motivo do cancelamento deve ter pelo menos 15 caracteres")
        self._dismiss_product_not_found()
        # ESC pode abrir diretamente TFrmDlg ``Cancelamento de Pedido``. Nesse
        # caso o TFrmPDV fica desabilitado e um novo F6 não é capturado; use o
        # formulário que já está aberto e conclua-o com motivo + OK.
        dialog = next(
            (
                window
                for window in self._top_level_windows()
                if self._is_cancel_reason_dialog(window)
            ),
            None,
        )
        if dialog is None:
            press(self.window, "F6")
            dialog = self._wait_for_cancel_reason()
        if dialog is not None:
            self._observe_dialog(dialog, "before_confirm_cancel_reason")
            self._fill_cancel_reason(dialog, reason)
            self._confirm_cancel_reason(dialog)

    def _dismiss_product_not_found(self) -> bool:
        """Fecha somente o aviso conhecido que informa produto inexistente."""
        for window in self._top_level_windows():
            try:
                if self._window_class(window) != "TFrmPDVProdutoNaoEncontrado":
                    continue
                if not _is_visible_and_enabled(window):
                    continue
                self._observe_dialog(window, "before_dismiss_missing_product")
                window.set_focus()
                press(window, "F2")
                time.sleep(0.25)
                if any(
                    self._window_class(candidate) == "TFrmPDVProdutoNaoEncontrado"
                    for candidate in self._top_level_windows()
                ):
                    press(window, "SPACE")
                return True
            except Exception:
                continue
        return False

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
            return (
                "motivo de cancelamento" in normalized
                or "digite o motivo" in normalized
                or "cancelamento de pedido" in normalized
                or "cancelar pedido" in normalized
            )
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

    def open_login_after_pause(self, timeout: float | None = None) -> Any:
        """Click the paused cashier screen until the real password form opens."""
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout
        pause_window = self._wait_for_top_level_class("TFrmPDVPausa", timeout)
        if pause_window is None:
            raise AssertionError("TFrmPDVPausa nao foi localizada para reabrir o login")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                pause_window.restore()
            except Exception:
                pass
            try:
                pause_window.set_focus()
            except Exception:
                pass
            try:
                pause_window.click_input()
            except Exception:
                try:
                    pause_window.click()
                except Exception:
                    pass

            password = self._wait_for_top_level_class("TFrmPassWord", 0.75)
            if password is not None:
                return password
            time.sleep(0.15)

        raise AssertionError(
            "Clique em TFrmPDVPausa nao expos TFrmPassWord dentro do prazo"
        )

    def open_drawer_authorization(self, timeout: float | None = None) -> Any | None:
        """Send F9 and return the real authorization form when it is exposed.

        ``LabelAbrirGavetaClick`` in ``PDV.pas`` forwards to ``FormKeyDown``;
        the ``VK_F9`` branch then calls ``AbrirGaveta`` and
        ``PDVUtilsController.AcionarGaveta`` (lines 1059-1063 and 3748-3752,
        3844-3853).  The method verifies only the authorization UI; physical
        drawer movement still requires the configured printer/hardware.
        """
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout
        if not _is_visible_and_enabled(self.window):
            current = self._wait_for_top_level_class("TFrmPDV", min(timeout, 1.0))
            if current is None:
                return None
            self.window = current
        try:
            self.window.set_focus()
            self.window.type_keys("{F9}", set_foreground=True)
        except Exception:
            press(self.window, "F9")
        return self._wait_for_top_level_class("TFrmPassWord", timeout)

    def wait_until_window_class(self, class_name: str, timeout: float | None = None) -> Any | None:
        """Wait for a visible/enabled top-level VCL form by its exact class."""
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout
        return self._wait_for_top_level_class(class_name, timeout)

    def has_window_class(self, class_name: str) -> bool:
        deadline = time.monotonic() + min(self.action_timeout, 3.0)
        while time.monotonic() < deadline:
            if any(self._window_class(window) == class_name for window in self._top_level_windows()):
                return True
            time.sleep(0.1)
        return False

    def modal(self) -> Any | None:
        for window in self._top_level_windows():
            if self._window_class(window) not in {"TFrmPDV", "TFrmAbout", "TApplication"}:
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

    def escape_main(self) -> None:
        """Send ESC specifically to TFrmPDV, ignoring a known residual form.

        A successful login can leave TFrmPassWord visible without focus and
        without blocking the PDV.  ``escape()`` intentionally targets a
        visible modal for general dialog dismissal; the close-cashier route
        must instead reach ``TFrmPDV.FormKeyDown`` (PDV.pas, lines 3693-3696).
        """
        try:
            self.window.set_focus()
        except Exception:
            pass
        press(self.window, "ESC")

    def dismiss_product_not_found(self) -> bool:
        """Close the known TFrmPDVProdutoNaoEncontrado dialog via F2/Space."""
        return self._dismiss_product_not_found()

    def confirm_close_cashier(self, timeout: float | None = None) -> bool:
        """Confirm the recognized close-cashier question with the Sim button.

        ``TFrmPDV.FormKeyDown`` routes ESC to ``FecharPDV`` and the real build
        exposes ``TFrmDlgInformacao`` with ``Tem certeza de que deseja Fechar
        o Caixa?`` plus ``Não``/``Sim`` buttons. This handler acts only after
        recognizing that exact question; it never sends a blind confirmation.
        """
        timeout = min(self.action_timeout, 4.0) if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for dialog in self._top_level_windows():
                try:
                    if self._window_class(dialog) not in {"TFrmDlgInformacao", "TFrmDlg"}:
                        continue
                    normalized = self._ascii(self._window_text(dialog))
                    if "fechar o caixa" not in normalized or "tem certeza" not in normalized:
                        continue
                    for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
                        for button in dialog.descendants(class_name=class_name):
                            caption = self._ascii(button.window_text() or "").strip("& ")
                            if caption != "sim" or not _is_visible_and_enabled(button):
                                continue
                            button.click_input()
                            return True
                except Exception:
                    continue
            time.sleep(0.1)
        return False

    def _top_level_windows(self) -> list[Any]:
        if Desktop is None or findwindows is None:
            return []
        try:
            pid = self.window.process_id()
            handles = findwindows.find_windows(process=pid)
            windows = [Desktop(backend="win32").window(handle=handle) for handle in handles]
            for dialog in windows:
                if self._is_application_error(dialog):
                    self._dismiss_application_error(dialog)
            return windows
        except Exception:
            return []

    @staticmethod
    def _is_application_error(dialog: Any) -> bool:
        try:
            text = PdvPage._window_text(dialog)
            return re.search(
                r"satpdv\s+encontrou\s+um\s+problema|"
                r"n[aã]o\s+[eé]\s+poss[ií]vel\s+focar\s+uma\s+janela\s+desativada",
                text,
                re.IGNORECASE,
            ) is not None
        except Exception:
            return False

    @staticmethod
    def _dismiss_application_error(dialog: Any) -> bool:
        """Fecha o relatório conhecido somente pelo botão ``Não Enviar``."""
        try:
            from core.test_results import record_dialog_observation

            record_dialog_observation(dialog, "before_dismiss_application_error")
        except Exception:
            pass
        for class_name in ("Button", "TButton", "TBitBtn"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                try:
                    caption = PdvPage._ascii(button.window_text() or "").strip("& ")
                    if caption != "nao enviar" or not _is_visible_and_enabled(button):
                        continue
                    try:
                        button.click_input()
                    except Exception:
                        button.click()
                    return True
                except Exception:
                    continue
        return False

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


def _is_visible_and_enabled(control: Any) -> bool:
    try:
        return bool(control.is_visible() and control.is_enabled())
    except Exception:
        return False


def _control_text(control: Any) -> str:
    try:
        return str(control.window_text() or "")
    except Exception:
        return ""


def _normalize_ui_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


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
