from __future__ import annotations

import re
import time
import ctypes
import os
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
        if quantity and self._dismiss_quantity_limit_warning():
            raise AssertionError(
                f"Quantidade {quantity} rejeitada pelo SATPDV: "
                "Qtde inserida maior que quantidade máxima permitida!"
            )
        if not self.has_item(code):
            raise AssertionError(
                f"Produto {code} nao apareceu no TMemo pnlProdutos apos a insercao"
            )

    def assert_quantity_limit_rejected(
        self, product_code: str, quantity: int | str, timeout: float | None = None
    ) -> str:
        """Validate PAR-01's quantity-limit dialog without treating rejection as failure.

        ``PDV.pas`` handles ``<quantity>*<product>`` in
        ``TFrmPDV.EditCodigoProdutoKeyPress`` and displays
        ``Qtde inserida maior que quantidade máxima permitida!``. This method
        captures the complete recognized dialog before pressing its documented
        F2/Space confirmation, then confirms the product was not rendered in
        ``TMemo pnlProdutos``.
        """
        try:
            quantity_value = int(str(quantity).strip())
        except ValueError as exc:
            raise ValueError("PAR-01 exige quantidade inteira positiva") from exc
        if quantity_value <= 0:
            raise ValueError("PAR-01 exige quantidade inteira positiva")
        if self.has_item(str(product_code), timeout=0.2):
            raise AssertionError(
                f"PAR-01 exige venda limpa: produto {product_code} já estava no TMemo pnlProdutos"
            )

        self._ensure_sale_mode()
        self.enter_reference(f"{quantity_value}*{product_code}")
        timeout = min(self.action_timeout, 4.0) if timeout is None else max(0.5, timeout)
        deadline = time.monotonic() + timeout
        expected_fragment = "qtde inserida maior que quantidade maxima permitida"
        last_text = ""
        while time.monotonic() < deadline:
            for dialog in self._top_level_windows():
                try:
                    last_text = self._window_text(dialog)
                    normalized = self._ascii(last_text).casefold()
                    class_name = self._window_class(dialog)
                    # In this homologation build the red caption is painted
                    # by TFrmQtdMax and is not exposed by window_text(); the
                    # accessible tree still exposes the quantity and its
                    # documented F2/Space instruction. The class is therefore
                    # part of the identification, not a blind button action.
                    is_quantity_limit = expected_fragment in normalized or (
                        class_name == "TFrmQtdMax"
                        and str(quantity_value) in normalized
                        and "pressione f2" in normalized
                        and "espaco" in normalized
                    )
                    if not is_quantity_limit:
                        continue
                    if not _is_visible_and_enabled(dialog):
                        continue
                    self._observe_dialog(dialog, "before_confirm_quantity_limit_warning")
                    self._confirm_quantity_limit_dialog(dialog)
                    try:
                        still_open = bool(dialog.is_visible() and dialog.is_enabled())
                    except Exception:
                        still_open = False
                    close_deadline = time.monotonic() + 1.5
                    while time.monotonic() < close_deadline:
                        try:
                            if not dialog.exists() or not dialog.is_visible():
                                break
                        except Exception:
                            break
                        time.sleep(0.1)
                    if not self.wait_until_absent(str(product_code), timeout=1.5):
                        raise AssertionError(
                            f"PAR-01: produto {product_code} apareceu após rejeição da quantidade"
                        )
                    return last_text
                except AssertionError:
                    raise
                except Exception:
                    continue
            time.sleep(0.1)
        raise AssertionError(
            "PAR-01: modal de limite de quantidade não foi identificado; "
            f"último texto observado: {last_text[:300]!r}"
        )

    @staticmethod
    def _confirm_quantity_limit_dialog(dialog: Any) -> None:
        """Use the TFrmQtdMax instructions: F2 or Space to continue."""
        try:
            dialog.click_input()
        except Exception:
            try:
                dialog.set_focus()
            except Exception:
                pass
        try:
            dialog.send_keystrokes("{F2}")
        except Exception:
            press(dialog, "F2")
        time.sleep(0.25)
        try:
            if dialog.is_visible() and dialog.is_enabled():
                dialog.send_keystrokes(" ")
        except Exception:
            try:
                press(dialog, "SPACE")
            except Exception:
                pass

    def assert_inverted_product_layout(self, expected_inverted: bool = True) -> dict[str, dict[str, int]]:
        """Validate PAR-02 using the runtime geometry of the named VCL panels.

        ``TFrmPDV.AjustarPosicionamentoPainel`` moves ``pnlProdutos`` and
        ``pnlTotalizadores`` to the left of ``pnlLogo`` when
        ``PDVInverterListaDeProdutos='S'``. Controls are located by Delphi
        name/class when exposed, never by fixed screen coordinates.
        """
        names = {
            "pnlProdutos": ("TMemo",),
            "pnlTotalizadores": ("TPanel",),
            "pnlLogo": ("TImage",),
            "pnlProduto": ("TPanel",),
            "GridProcura": ("TDBGrid",),
        }
        controls = {name: self._find_named_control(name, classes) for name, classes in names.items()}
        missing = [name for name, control in controls.items() if control is None]
        if missing:
            raise AssertionError(
                "PAR-02: controles Delphi não localizados: " + ", ".join(missing)
            )
        main = self.window.rectangle()
        positions: dict[str, dict[str, int]] = {}
        for name, control in controls.items():
            rect = control.rectangle()
            positions[name] = {
                "left": int(rect.left - main.left),
                "right": int(rect.right - main.left),
                "top": int(rect.top - main.top),
                "bottom": int(rect.bottom - main.top),
            }
        products = positions["pnlProdutos"]
        totals = positions["pnlTotalizadores"]
        logo = positions["pnlLogo"]
        product_panel = positions["pnlProduto"]
        search_grid = positions["GridProcura"]
        if expected_inverted:
            checks = {
                "produtos_antes_do_logo": products["left"] < logo["left"],
                "totais_antes_do_logo": totals["left"] < logo["left"],
                "produto_antes_do_logo": product_panel["left"] >= logo["left"],
                "grid_procura_com_o_logo": search_grid["left"] >= logo["left"],
            }
        else:
            checks = {
                "produtos_depois_do_logo": products["left"] > logo["left"],
                "totais_depois_do_logo": totals["left"] > logo["left"],
            }
        invalid = [name for name, passed in checks.items() if not passed]
        if invalid:
            raise AssertionError(
                "PAR-02: relação geométrica não corresponde à configuração esperada "
                f"({', '.join(invalid)}); posições={positions}"
            )
        return positions

    def _find_named_control(self, name: str, classes: tuple[str, ...]) -> Any | None:
        """Find a VCL control by Delphi name exposed by Win32/UIA metadata."""
        candidates = []
        try:
            candidates.extend(self.window.descendants())
        except Exception:
            return None
        wanted = name.casefold()
        for control in candidates:
            try:
                if control.class_name() not in classes:
                    continue
                metadata = [
                    str(getattr(control.element_info, "name", "") or ""),
                    str(getattr(control.element_info, "automation_id", "") or ""),
                    str(control.window_text() or ""),
                ]
                if any(value.casefold() == wanted for value in metadata):
                    return control
            except Exception:
                continue
        return None

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
        try:
            # O foco físico já foi estabelecido pelo click_input() do helper;
            # set_foreground=False evita um novo SetForegroundWindow.
            edit.type_keys("{ENTER}", set_foreground=False)
        except Exception:
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

    def _ocr_status_message(self) -> str:
        """Read the rendered ``EditMsg: TLabel`` status without a HWND.

        ``TFrmPDV.ExibirMsg`` assigns the message to ``EditMsg.Caption`` and
        calls ``Refresh`` (``PDV.pas``, ``TFrmPDV.ExibirMsg``).  VCL labels do
        not expose a child HWND in the Win32 tree, so this deliberately crops
        only the left part of the header where that label is painted.  It is
        a fallback for status messages, not a replacement for dialog text.
        """
        try:
            import pytesseract
            from PIL import ImageOps

            self._configure_tesseract(pytesseract)
            rect = self.window.rectangle()
            image = self._grab_rect(rect)
            image = image.crop(
                (0, 0, int(image.width * 0.55), max(160, int(image.height * 0.24)))
            )
            image = ImageOps.grayscale(image).resize((image.width * 2, image.height * 2))
            return pytesseract.image_to_string(image, config="--psm 6")
        except Exception:
            return ""

    def wait_for_status_message(
        self,
        pattern: str,
        timeout: float = 4.0,
        interval: float = 0.35,
    ) -> str:
        """Poll the rendered TLabel status until ``pattern`` is recognized."""
        compiled = re.compile(pattern, re.IGNORECASE)
        deadline = time.monotonic() + max(0.0, timeout)
        last_text = ""
        while True:
            last_text = self._ocr_status_message()
            if compiled.search(self._ascii(last_text)):
                return last_text
            if time.monotonic() >= deadline:
                return last_text
            time.sleep(max(0.05, interval))

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
        """Envia ``quantidade*`` sem confirmar parcialmente a quantidade.

        O ``EditCodigoProdutoKeyPress`` do Delphi só interpreta ``*`` como
        separador da quantidade.  Por isso os dígitos precisam permanecer no
        mesmo TEdit até o readback confirmar o valor completo; ``Enter`` não é
        enviado entre eles.  A limpeza HOME/DELETE/BACKSPACE também evita que
        um valor residual (por exemplo ``1``) seja reaproveitado pelo teste.
        """
        quantity = str(quantity)
        if not quantity.isdigit() or not quantity:
            raise AssertionError(f"Quantidade inválida para o TEdit: {quantity!r}")

        for asterisk_sequence in ("{SHIFT down}8{SHIFT up}", "+8", "*"):
            PdvPage._clear_quantity_edit(edit)
            try:
                # Uma única chamada envia os dois dígitos sequencialmente,
                # sem confirmação prematura entre '5' e '1'.
                # O clique físico mantém o foco; set_foreground=False evita
                # repetir SetForegroundWindow, mas preserva o KeyPress/WM_CHAR
                # que o handler Delphi precisa para interpretar '51' e '*'.
                edit.type_keys(quantity, set_foreground=False, pause=0.12)
            except Exception as exc:
                raise AssertionError(
                    f"Falha ao digitar quantidade {quantity!r} no TEdit de produto"
                ) from exc

            typed_back = (edit.window_text() or "").strip()
            if typed_back != quantity:
                raise AssertionError(
                    "Quantidade não foi digitada corretamente: "
                    f"campo contém {typed_back!r}, esperado {quantity!r}"
                )

            try:
                edit.type_keys(asterisk_sequence, set_foreground=False, pause=0.08)
            except Exception:
                continue
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    # O TFrmQtdMax pode deixar o TEdit subjacente com '51'
                    # enquanto bloqueia o TFrmPDV. Nesse caso o campo não
                    # ficará vazio, mas o evento foi consumido corretamente.
                    if PdvPage._quantity_limit_modal_visible(edit):
                        return
                    if not (edit.window_text() or "").strip():
                        return
                except Exception:
                    pass
                time.sleep(0.05)

        raise AssertionError(
            f"A tecla * nao confirmou a quantidade {quantity}; "
            "o TEdit de produto nao foi limpo pelo handler EditCodigoProdutoKeyPress"
        )

    @staticmethod
    def _clear_quantity_edit(edit: Any) -> None:
        """Limpa o TEdit com teclas reais e confirma que ficou vazio."""
        focused = False
        try:
            edit.click_input()
            focused = True
        except Exception:
            pass
        if not focused:
            # SetForegroundWindow pode falhar momentaneamente no Windows
            # enquanto a VCL termina o repaint. Retry curto antes de desistir;
            # não substituímos a entrada por set_edit_text.
            for _ in range(3):
                try:
                    edit.set_focus()
                    focused = True
                    break
                except Exception:
                    time.sleep(0.15)
        if not focused:
            raise AssertionError(
                "Não foi possível colocar foco no TEdit de produto antes de limpar a quantidade"
            )
        current = (edit.window_text() or "").strip()
        # HOME + DELETE remove cada caractere sem depender de seleção nativa.
        for _ in range(max(len(current), 1) + 2):
            edit.type_keys("{HOME}{DELETE}", set_foreground=False, pause=0.05)
            if not (edit.window_text() or "").strip():
                break
        if (edit.window_text() or "").strip():
            edit.type_keys("^{A}{BACKSPACE}", set_foreground=False, pause=0.05)
        if (edit.window_text() or "").strip():
            # Último recurso somente depois da sequência física e do readback;
            # não permite prosseguir silenciosamente com um valor residual.
            edit.set_edit_text("")
        if (edit.window_text() or "").strip():
            raise AssertionError(
                "Não foi possível limpar o TEdit de produto antes de digitar a quantidade: "
                f"campo contém {(edit.window_text() or '')!r}"
            )

    @staticmethod
    def _quantity_limit_modal_visible(edit: Any) -> bool:
        """Detect the TFrmQtdMax belonging to the same SATPDV process."""
        if Desktop is None or findwindows is None:
            return False
        try:
            pid = edit.process_id()
            handles = findwindows.find_windows(process=pid)
            for handle in handles:
                dialog = Desktop(backend="win32").window(handle=handle)
                if (
                    PdvPage._window_class(dialog) == "TFrmQtdMax"
                    and dialog.is_visible()
                    and dialog.is_enabled()
                ):
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _type_reference_part(edit: Any, value: str) -> None:
        try:
            edit.click_input()
            edit.type_keys("^{A}{BACKSPACE}", set_foreground=False, pause=0.05)
            edit.type_keys(value, set_foreground=False, pause=0.12, with_spaces=True)
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
        ``PDV.pas``: Ctrl+F2 (suprimento), Ctrl+F3 (sangria), F5 (CPF/CNPJ),
        F7 (reimpressão fiscal), F10 (vendedor), F11/F12 (desconto),
        Shift+F4 (tabela), Ctrl+F4 (orçamento), Ctrl+F8 (relatório de
        fechamento) e Ctrl+F9 (observação do pedido).
        """
        sequences = {
            "CTRL+F2": "^{F2}",
            "CTRL+F3": "^{F3}",
            "F3": "{F3}",
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
            "CTRL+F9": "^{F9}",
        }
        try:
            sequence = sequences[shortcut.upper()]
        except KeyError as exc:
            raise ValueError(f"Atalho nao mapeado: {shortcut}") from exc
        self._ensure_sale_mode()
        # A recuperação de venda pode aparecer alguns segundos após o login.
        # É um modal conhecido; rejeitá-lo antes do atalho evita que o evento
        # seja consumido pela confirmação em vez do formulário do PDV.
        self.dismiss_pending_recovery(timeout=1.0)
        self.window.restore()
        self.window.set_focus()
        self.window.type_keys(sequence, set_foreground=True, pause=0.05)

    def set_order_observation(
        self,
        observation: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Preenche a observação real do pedido pela dialog ``InputMemo``.

        ``TFrmPDV.InserirObs`` em ``PDV.pas`` (Ctrl+F9) chama ``InputMemo``
        somente quando ``PDVController.EmVenda`` está ativo e grava o retorno
        em ``QOEOBS``. O formulário é criado dinamicamente; por isso a busca
        é feita pelo texto funcional da dialog e o campo é aceito apenas se
        for um editor VCL visível/habilitado (normalmente ``TMemo``), nunca
        por posição ou coordenada.
        """
        expected = str(observation).strip()
        if not expected:
            raise ValueError("A observacao do pedido nao pode ser vazia")
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout

        self.send_shortcut("CTRL+F9")
        dialog = self.wait_for_dialog_text(
            r"observa[cç][aã]o|digite\s+a\s+observa",
            timeout=timeout,
        )
        if dialog is None:
            raise capture_unknown_state(self.window, "PRD-03_observation_dialog_not_open")

        self._observe_dialog(dialog, "before_fill_order_observation_PRD-03")
        editors: list[Any] = []
        # InputMemo is a dynamically-created VCL form. Keep the class filter
        # narrow so the product TEdit in TFrmPDV can never receive the text.
        for class_name in ("TMemo", "TJvMemo", "TEdit", "TJvEdit", "TJvValidateEdit"):
            try:
                editors.extend(
                    control
                    for control in dialog.descendants(class_name=class_name)
                    if _is_visible_and_enabled(control)
                )
            except Exception:
                continue
        if not editors:
            raise capture_unknown_state(dialog, "PRD-03_observation_input_not_exposed")
        try:
            editors.sort(key=lambda control: (control.rectangle().top, control.rectangle().left))
        except Exception:
            pass

        editor = editors[0]
        try:
            editor.set_focus()
            editor.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.05)
            editor.type_keys(expected, set_foreground=True, pause=0.05, with_spaces=True)
        except Exception as exc:
            raise AssertionError(
                "PRD-03: nao foi possivel digitar a observacao no editor do InputMemo"
            ) from exc

        observed = self._read_input_value(editor).strip()
        # ``TDlgMemo`` pode aplicar a propriedade VCL de capitalização de
        # palavras durante o repaint (ex.: ``PRD-03`` -> ``Prd-03``). A
        # validação precisa confirmar o conteúdo, sem transformar essa
        # normalização visual em falso negativo.
        if observed.casefold() != expected.casefold():
            raise AssertionError(
                f"PRD-03: observacao nao foi digitada corretamente: "
                f"campo contem {observed!r}, esperado {expected!r}"
            )

        dialog_text = self._window_text(dialog)
        self.confirm_known_dialog(dialog)
        return {
            "dialog_class": self._window_class(dialog),
            "dialog_text": dialog_text,
            "observed": observed,
        }

    def open_fiscal_menu(self, timeout: float | None = None) -> Any:
        """Abre ``FrmMenuFiscal`` pelo F8 e retorna somente a janela reconhecida.

        ``TFrmPDV.FormKeyDown``/``ExibirTelaDeMenuFiscal`` em ``PDV.pas``
        delega a tela a ``FrmMenuFiscal.ShowMenuFiscal``. Como os controles
        dessa unidade não estão no DFM fornecido, o reconhecimento exige
        texto funcional de menu/fiscal antes de o chamador interagir.
        """
        timeout = min(self.action_timeout, 5.0) if timeout is None else timeout
        self._ensure_sale_mode()
        self.window.restore()
        self.window.set_focus()
        self.window.type_keys("{F8}", set_foreground=True, pause=0.05)
        dialog = self.wait_for_dialog_text(r"menu\s*fiscal|fiscal", timeout=timeout)
        if dialog is None:
            raise capture_unknown_state(self.window, "MFI-01_fiscal_menu_not_open")
        self._observe_dialog(dialog, "before_interact_fiscal_menu_MFI-01")
        return dialog

    def dismiss_pending_recovery(self, timeout: float = 1.0) -> bool:
        """Clique em ``Não`` somente no prompt conhecido de recuperação."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            for dialog in self._top_level_windows():
                try:
                    text = self._ascii(self._window_text(dialog))
                    if "existe uma venda aberta no pdv" not in text or "deseja recuperar" not in text:
                        continue
                    self._observe_dialog(dialog, "before_dismiss_recovery_before_shortcut")
                    for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
                        for button in dialog.descendants(class_name=class_name):
                            caption = self._ascii(button.window_text() or "").strip("& ").casefold()
                            if caption != "nao" or not _is_visible_and_enabled(button):
                                continue
                            button.click_input()
                            return True
                except Exception:
                    continue
            time.sleep(0.1)
        return False

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
        for class_name in (
            "TEdit",
            "TJvEdit",
            "TJvValidateEdit",
            "TMemo",
            "TComboBox",
        ):
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

    @staticmethod
    def _read_input_value(control: Any) -> str:
        """Lê novamente o valor renderizado por um campo VCL."""
        try:
            value = str(control.window_text() or "")
        except Exception:
            value = ""
        if value:
            return value
        try:
            values = [str(value or "") for value in control.texts()]
            return " ".join(value for value in values if value).strip()
        except Exception:
            return value

    def _capture_input_evidence(
        self,
        control: Any,
        container: Any | None = None,
        expected_digits: str | None = None,
    ) -> bool:
        """Salva recorte da tela real do campo e detecta texto renderizado."""
        try:
            root = Path(os.environ.get("PDV_REPORT_ROOT", "reports"))
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            rect = control.rectangle()
            if container is not None:
                # Alguns forms VCL desenham o fundo/área útil no TPanel pai e
                # deixam o HWND do editor apenas no lado direito. Ampliar só
                # horizontalmente na mesma linha captura a faixa visual do
                # CPF sem incluir rótulos ou os demais campos.
                try:
                    parent_rect = container.rectangle()
                    rect = type(rect)(
                        max(parent_rect.left, rect.left - 130),
                        rect.top - 4,
                        min(parent_rect.right, rect.right + 130),
                        # O form VCL desenha a área branca de edição abaixo
                        # do HWND reportado; manter essa faixa no recorte
                        # evita concluir pela ausência de texto só porque o
                        # retângulo Win32 termina na linha do rótulo.
                        min(parent_rect.bottom, rect.bottom + 30),
                    )
                except Exception:
                    pass
            # mss trabalha com as coordenadas físicas do desktop virtual e é
            # mais confiável que ImageGrab em estações com dois monitores/DPI
            # diferente. O recorte é da tela real, não do buffer Win32.
            try:
                from mss import mss
                from PIL import Image

                with mss() as screen:
                    shot = screen.grab({
                        "left": rect.left,
                        "top": rect.top,
                        "width": max(1, rect.width()),
                        "height": max(1, rect.height()),
                    })
                image = Image.frombytes("RGB", shot.size, shot.rgb)
            except Exception:
                image = self._grab_rect(rect)
            image.save(root / f"client_cpf_field_{stamp}.png")
            # Quando há um valor esperado, OCR confirma o conteúdo textual no
            # recorte físico. Isso evita aceitar somente o valor interno
            # devolvido por window_text() ou alguns pixels de rótulo/caret.
            # Quando a área ampliada inclui o rótulo acima do campo, excluir a
            # faixa superior evita que a própria palavra ``CPF/CNPJ`` vire um
            # falso indício de que o valor foi renderizado.
            detection = image.crop((0, image.height // 3, image.width, image.height))
            if expected_digits:
                try:
                    import pytesseract

                    self._configure_tesseract(pytesseract)
                    ocr_text = pytesseract.image_to_string(
                        # O CPF pode ficar selecionado em azul e começa na
                        # borda superior da caixa; excluir a primeira faixa
                        # também excluiria parte dos próprios dígitos. O
                        # recorte já é limitado ao campo/linha e o match exige
                        # a sequência inteira esperada.
                        image.resize((image.width * 2, image.height * 2)),
                        config="--psm 6",
                        lang="eng",
                    )
                    ocr_digits = re.sub(r"\D", "", ocr_text)
                    return expected_digits in ocr_digits
                except Exception:
                    # Sem OCR disponível, não transformar pixels em prova de
                    # que o CPF correto foi desenhado; o chamador deve falhar
                    # de forma explícita e preservar a captura.
                    return False
            gray = detection.convert("L")
            return sum(pixel < 180 for pixel in gray.getdata()) >= 2
        except Exception:
            # A leitura textual continua sendo a asserção obrigatória; a
            # captura é evidência complementar para inspeção visual.
            return False

    def _capture_client_dialog_evidence(self, dialog: Any, phase: str) -> Path | None:
        """Salva a tela inteira do ``TFrmCPFCNPJ`` para auditoria visual.

        O formulário usa controles VCL customizados e o retângulo exposto pelo
        HWND filho pode não coincidir com toda a área pintada pelo controle.
        Guardar o formulário completo permite verificar a posição real do
        texto sem transformar um ``window_text()`` interno em falso positivo.
        """
        try:
            root = Path(os.environ.get("PDV_REPORT_ROOT", "reports"))
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            rect = dialog.rectangle()
            image = self._grab_rect(rect)
            path = root / f"client_cpf_dialog_{phase}_{stamp}.png"
            image.save(path)
            return path
        except Exception:
            return None

    def _capture_full_screen_evidence(self, phase: str) -> Path | None:
        """Salva a tela inteira, incluindo as duas áreas de trabalho.

        A captura do campo e do formulário não basta para diagnosticar foco,
        janelas sobrepostas ou um monitor que ficou branco. Este artefato é
        complementar e não substitui a leitura de volta dos controles.
        """
        try:
            root = Path(os.environ.get("PDV_REPORT_ROOT", "reports"))
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            try:
                from mss import mss
                from PIL import Image

                with mss() as screen:
                    monitor = screen.monitors[0]
                    shot = screen.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
            except Exception:
                from PIL import ImageGrab

                image = ImageGrab.grab(all_screens=True)
            path = root / f"client_screen_{phase}_{stamp}.png"
            image.save(path)
            return path
        except Exception:
            return None

    def fill_known_dialog(self, dialog: Any, value: str) -> Any:
        """Preenche e verifica o primeiro campo de um diálogo conhecido.

        O preenchimento usa interação física no controle. ``set_edit_text`` não
        é usado como atalho, pois em controles VCL pode alterar somente o valor
        interno e fazer o teste acreditar que a entrada foi exibida quando não
        houve repaint na tela.
        """
        controls = self.dialog_edits(dialog)
        if not controls:
            raise capture_unknown_state(dialog, "known_dialog_without_input_control")
        control = controls[0]
        expected = str(value).strip()
        observed = ""
        for attempt in range(3):
            try:
                if attempt == 1:
                    import pyautogui

                    rect = control.rectangle()
                    pyautogui.click(
                        x=rect.left + max(1, rect.width() // 2),
                        y=rect.top + max(1, rect.height() // 2),
                    )
                    pyautogui.press("home")
                    pyautogui.press("delete", presses=32, interval=0.02)
                    pyautogui.press("backspace", presses=32, interval=0.02)
                    pyautogui.write(expected, interval=0.10)
                else:
                    control.click_input()
                    time.sleep(0.15)
                    # TJvValidateEdit/TJvEdit do not consistently honor
                    # Ctrl+A in this VCL build; clear from HOME physically.
                    control.type_keys("{HOME}", set_foreground=True, pause=0.05)
                    control.type_keys(
                        "{DELETE}" * 32,
                        set_foreground=True,
                        pause=0.02,
                    )
                    control.type_keys(
                        "{BACKSPACE}" * 32,
                        set_foreground=True,
                        pause=0.02,
                    )
                    control.type_keys(
                        expected,
                        set_foreground=True,
                        pause=0.05,
                        with_spaces=True,
                    )
            except Exception:
                pass
            time.sleep(0.2)
            observed = self._read_input_value(control)
            if not expected or expected.casefold() in observed.casefold():
                return control
        raise AssertionError(
            f"Campo do dialogo nao foi preenchido corretamente: "
            f"campo contem {observed!r}, esperado {expected!r}; "
            "a entrada foi enviada por clique/teclas, sem set_edit_text"
        )

    def fill_client_document(self, dialog: Any, document: str) -> Any:
        """Preenche o campo CPF/CNPJ do formulário ``CPF/CNPJ do Cliente``.

        ``PDV.pas`` (``TFrmPDV.FormKeyDown``) encaminha F5 para
        ``InformarCPFOuCNPJParaVendaAtual(False)``; essa rotina abre o
        formulário da unit ``CPFCNPJ``. No runtime desta build, o campo sob o
        rótulo ``CPF/CNPJ`` é o editor numérico superior ``TJvValidateEdit``;
        o ``TMaskEdit`` com máscara ``_____-___`` é o CEP abaixo e não deve
        receber o documento.
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
        # O runtime expõe dois controles no campo CPF/CNPJ: ``TJvEdit`` contém
        # o documento bruto e ``TJvValidateEdit`` é apenas a apresentação
        # numérica formatada. O ``TMaskEdit`` com máscara ``_____-___`` é o
        # CEP abaixo e não deve ser escolhido.
        mask_controls = []
        try:
            mask_controls = [
                candidate
                for candidate in dialog.descendants(class_name="TMaskEdit")
                if _is_visible_and_enabled(candidate)
            ]
            mask_controls.sort(key=lambda candidate: (
                candidate.rectangle().top,
                candidate.rectangle().left,
            ))
        except Exception:
            mask_controls = []
        document_controls = []
        for candidate in controls:
            try:
                if candidate.class_name() in {"TJvEdit", "TJvValidateEdit"}:
                    document_controls.append(candidate)
            except Exception:
                continue
        top_candidates = mask_controls + document_controls
        if top_candidates:
            control = min(top_candidates, key=lambda candidate: (
                candidate.rectangle().top,
                candidate.rectangle().left,
            ))
        else:
            control = controls[0]
        candidate_diagnostics = []
        all_input_controls = list(mask_controls) + [
            candidate for candidate in controls if candidate not in mask_controls
        ]
        for candidate in all_input_controls:
            try:
                candidate_diagnostics.append(
                    {
                        "class": candidate.class_name(),
                        "rect": repr(candidate.rectangle()),
                        "window_text": self._read_input_value(candidate),
                        "texts": [str(item or "") for item in candidate.texts()],
                    }
                )
            except Exception as exc:
                candidate_diagnostics.append({"class": type(candidate).__name__, "error": str(exc)})
        try:
            control_class = control.class_name()
            control_rect = control.rectangle()
        except Exception:
            control_class = type(control).__name__
            control_rect = "<indisponivel>"
        value = str(document).strip()
        try:
            dialog.set_focus()
        except Exception:
            pass
        clicked = False
        for _ in range(3):
            try:
                # TFrmCPFCNPJ só encaminha a digitação ao campo superior após
                # clique físico nele. O HWND descoberto para TJvValidateEdit
                # ocupa apenas uma faixa estreita; o centro da caixa visual é
                # calculado no próprio TFrmCPFCNPJ para não clicar no rótulo ou
                # em outro controle.
                field_rect = control.rectangle()
                dialog_rect = dialog.rectangle()
                dialog.click_input(
                    coords=(
                        min(100, max(20, dialog_rect.width() // 2)),
                        max(5, field_rect.top - dialog_rect.top + 10),
                    )
                )
                clicked = True
                break
            except Exception:
                time.sleep(0.1)
        if not clicked:
            raise capture_unknown_state(dialog, "client_document_field_focus_failed")
        expected_digits = re.sub(r"\D", "", value)
        observed = ""
        rendered = False
        dialog_evidence_paths: list[str] = []
        for attempt in range(3):
            # Aguarda o repaint/foco da TFrmCPFCNPJ antes de cada tentativa.
            time.sleep(0.3)
            try:
                if attempt == 1:
                    # Alternativa ainda física para VCL: click_input pode
                    # devolver sucesso mesmo quando o backend não mantém o
                    # foco. pyautogui clica no mesmo HWND e envia teclas pelo
                    # desktop virtual, sem alterar o valor por API.
                    import pyautogui

                    rect = control.rectangle()
                    pyautogui.click(
                        # O TJvEdit é o campo bruto de CPF/CNPJ nesta build;
                        # clicar no centro do próprio retângulo evita enviar
                        # apenas o primeiro dígito ao formulário pai.
                        x=rect.left + max(1, rect.width() // 2),
                        y=rect.top + max(1, rect.height() // 2),
                    )
                    pyautogui.press("home")
                    pyautogui.press("delete", presses=32, interval=0.02)
                    pyautogui.press("backspace", presses=32, interval=0.02)
                    pyautogui.write(expected_digits, interval=0.10)
                elif attempt == 2:
                    # Envio ao formulário após clique físico no centro da caixa
                    # visual. Em alguns VCL customizados o foco lógico fica no
                    # formulário, embora o HWND filho reporte o valor interno.
                    dialog.type_keys("{HOME}", set_foreground=True, pause=0.05)
                    dialog.type_keys("{DELETE}" * 32, set_foreground=True, pause=0.02)
                    dialog.type_keys("{BACKSPACE}" * 32, set_foreground=True, pause=0.02)
                    # A TJvEdit nesta build valida o CPF durante a sequência
                    # de WM_CHAR. Com uma pausa entre caracteres, a rotina
                    # abre o alerta de documento incompleto após os primeiros
                    # dígitos e o restante passa a ser enviado para o modal.
                    # Enviar a sequência sem pausa mantém os 11 dígitos no
                    # campo; o readback visual abaixo continua obrigatório.
                    dialog.type_keys(value, set_foreground=True, pause=0.0)
                else:
                    # O TJvValidateEdit desta build não consome Ctrl+A de
                    # forma confiável. HOME + DELETE/BACKSPACE limpa o campo
                    # sem depender da seleção lógica do componente VCL.
                    control.type_keys("{HOME}", set_foreground=True, pause=0.05)
                    control.type_keys("{DELETE}" * 32, set_foreground=True, pause=0.02)
                    control.type_keys("{BACKSPACE}" * 32, set_foreground=True, pause=0.02)
                    # O controle é o TJvEdit do CPF/CNPJ. A sequência precisa
                    # ser atômica para não disparar a validação parcial do
                    # formulário entre um dígito e outro.
                    control.type_keys(value, set_foreground=True, pause=0.0)
            except Exception:
                # Não usar setter de texto como fallback: isso é precisamente
                # o caminho que mascarou a ausência de caracteres na tela.
                pass
            # O TMaskEdit/TJvValidateEdit só confirma o repaint em algumas
            # compilações VCL quando perde o foco; TAB ocorre depois da escrita
            # no campo correto e antes da leitura/captura, nunca como atalho de
            # confirmação do formulário.
            try:
                control.type_keys("{TAB}", set_foreground=True, pause=0.05)
            except Exception:
                pass
            time.sleep(0.3)
            observed = self._read_input_value(control)
            # TJvValidateEdit é numérico nesta build e pode devolver o CPF como
            # ``22.249.252.041,00``. Comparar a parte inteira formatada evita
            # rejeitar um CPF visualmente correto por causa do sufixo decimal,
            # sem aceitar números diferentes ou apenas substring arbitrária.
            observed_integer = re.sub(r"[,\.]\d{2}$", "", observed.strip())
            observed_digits = re.sub(r"\D", "", observed_integer)
            readback_ok = observed_digits == expected_digits
            rendered = self._capture_input_evidence(control, dialog, expected_digits)
            dialog_evidence = self._capture_client_dialog_evidence(dialog, f"attempt_{attempt + 1}")
            if dialog_evidence is not None:
                dialog_evidence_paths.append(str(dialog_evidence))
            full_screen_evidence = self._capture_full_screen_evidence(
                f"attempt_{attempt + 1}"
            )
            if full_screen_evidence is not None:
                dialog_evidence_paths.append(str(full_screen_evidence))
            if readback_ok and rendered:
                self._observe_dialog(dialog, "after_fill_client_document_verified")
                return control
            if attempt < 2:
                # Nova tentativa somente no campo reconhecido, sem alterar
                # valor por API nem enviar teclas às cegas para outra janela.
                try:
                    control.click_input()
                except Exception:
                    pass
        raise AssertionError(
            f"CPF nao foi digitado corretamente: campo contem {observed!r}, "
            f"esperado {value!r}; caracteres renderizados na captura={rendered}; "
            f"controle={control_class!r}, retangulo={control_rect!r}; "
            f"candidatos={candidate_diagnostics!r}; "
            f"evidencias_visuais={dialog_evidence_paths!r}; "
            "a leitura interna nao e aceita como prova de exibicao"
        )

    def confirm_client_dialog(self, dialog: Any) -> None:
        """Confirma o formulário de cliente pelo botão ``F10 - OK``.

        O F10 é enviado ao próprio formulário somente quando o botão não é
        exposto pela árvore Win32. Assim ele não chega ao ``TFrmPDV`` e não
        dispara ``SolicitarVendedor`` acidentalmente.
        """
        self._observe_dialog(dialog, "before_confirm_client_dialog")
        accepted = re.compile(r"(?:f10\s*-\s*)?&?ok", re.IGNORECASE)

        def client_form_exists() -> bool:
            # O VCL pode destruir o HWND recebido no início do fluxo e
            # recriar TFrmCPFCNPJ durante o processamento do F10. Portanto,
            # verificar apenas ``dialog.exists()`` pode declarar sucesso
            # enquanto outra instância do mesmo formulário continua visível.
            for candidate in self._top_level_windows():
                try:
                    if (
                        self._window_class(candidate) == "TFrmCPFCNPJ"
                        and candidate.is_visible()
                    ):
                        return True
                except Exception:
                    continue
            return False

        def closed() -> bool:
            if client_form_exists():
                return False
            try:
                return not dialog.exists() or not dialog.is_visible()
            except Exception:
                # A destroyed VCL HWND is the expected result of F10.
                return True

        def wait_closed(timeout: float = 2.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if closed():
                    return True
                time.sleep(0.1)
            return closed()

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
                except Exception:
                    continue
                # O botão pode receber o clique antes de o handler VCL
                # processar a consulta feita por Enter. Se o formulário ainda
                # estiver visível/habilitado, repetir F10 no próprio
                # TFrmCPFCNPJ; nunca enviar essa tecla ao TFrmPDV por baixo.
                time.sleep(0.35)
                if not closed():
                    try:
                        if dialog.is_enabled():
                            dialog.set_focus()
                            dialog.type_keys("{F10}", set_foreground=True, pause=0.08)
                    except Exception:
                        pass
                if not closed():
                    # O formulário documenta ``F10 - OK`` e, nesta build,
                    # o evento pode ser tratado pelo próprio TBitBtn em vez
                    # do form. Enviar F10 ao controle identificado mantém o
                    # destino seguro, sem atingir o TFrmPDV por baixo.
                    try:
                        button.set_focus()
                        button.type_keys("{F10}", set_foreground=True, pause=0.08)
                    except Exception:
                        pass
                if not closed():
                    # Último recurso ainda determinístico: clique físico no
                    # retângulo do TBitBtn já localizado pelo runtime. Não há
                    # coordenada fixa nem interação com janela desconhecida.
                    try:
                        import pyautogui

                        rect = button.rectangle()
                        pyautogui.click(
                            x=(rect.left + rect.right) // 2,
                            y=(rect.top + rect.bottom) // 2,
                        )
                        pyautogui.press("f10")
                    except Exception:
                        pass
                self._observe_dialog(dialog, "after_confirm_client_dialog_attempt")
                self._capture_full_screen_evidence("after_confirm_client_dialog_attempt")
                if wait_closed():
                    return
                # Esta exceção fica fora do bloco que captura falhas de
                # interação do botão; não pode ser mascarada como sucesso.
                raise AssertionError(
                    "TFrmCPFCNPJ permaneceu visivel apos clique/F10; "
                    "TFrmPDV nao foi liberado para a venda"
                )
        try:
            dialog.set_focus()
            dialog.type_keys("{F10}", set_foreground=True, pause=0.05)
        except Exception as exc:
            raise capture_unknown_state(dialog, "client_dialog_without_f10_ok") from exc
        if wait_closed():
            return
        raise AssertionError(
            "TFrmCPFCNPJ permaneceu visivel apos F10; TFrmPDV nao foi liberado"
        )

    def lookup_client_document(self, dialog: Any, document_control: Any) -> str:
        """Consulta o CPF com Enter e devolve o nome preenchido pelo PDV.

        ``TFrmCPFCNPJ`` usa ``TJvEdit`` para o documento bruto. A confirmação
        da consulta é uma etapa distinta do F10: Enter dispara a busca do
        cadastro e o botão ``F10 - OK`` posteriormente vincula o cliente à
        venda. O nome fica no primeiro ``TEdit`` abaixo do documento (campo
        ``NOME``); a rotina lê esse controle após aguardar o repaint.
        """
        self._observe_dialog(dialog, "before_lookup_client_document")
        try:
            document_control.click_input()
            document_control.type_keys("{ENTER}", set_foreground=True, pause=0.08)
        except Exception as exc:
            raise AssertionError(
                "Nao foi possivel consultar o CPF com Enter no TJvEdit"
            ) from exc
        time.sleep(0.5)
        name_controls: list[Any] = []
        try:
            document_top = document_control.rectangle().top
            name_controls = [
                candidate
                for candidate in dialog.descendants(class_name="TEdit")
                if _is_visible_and_enabled(candidate)
                and candidate.rectangle().top > document_top
            ]
            name_controls.sort(key=lambda candidate: (
                candidate.rectangle().top,
                candidate.rectangle().left,
            ))
        except Exception:
            name_controls = []
        name = self._read_input_value(name_controls[0]) if name_controls else ""
        self._observe_dialog(dialog, "after_lookup_client_document")
        return name.strip()

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
                    still_open = bool(dialog.is_visible() and dialog.is_enabled())
                except Exception:
                    # The VCL form may destroy its HWND immediately after OK.
                    return
                if still_open:
                    raise capture_unknown_state(
                        dialog, "known_dialog_confirmation_not_closed"
                    )
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

        payment_list = self._payment_method_list(payment, payment_method)
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

    def finalize_voucher_sale(
        self,
        *,
        voucher_code: str | None = None,
        expected_total: Decimal | str | None = None,
        proof_stem: str,
        timeout: float | None = None,
    ) -> tuple[Path, str]:
        """Finalize the mapped ``Vale Troca > Voucher`` branch.

        The route is deliberately separate from ``finalize_sale`` because the
        voucher branch can expose a second list and, after confirmation, a
        native save dialog containing the printed voucher.  Only the known
        payment form and a recognized native save dialog are interacted with;
        an unrecognized window is captured and raised for manual mapping.

        Controls documented by the runtime/DFM: ``TFrmInserirPgto`` with a
        ``TListBox`` for payment methods and the voucher code editor; the
        saved proof is handled by the Windows/VCL file-save dialog.
        """
        timeout = min(self.action_timeout, 15.0) if timeout is None else timeout
        self.last_sale_submitted = False
        if expected_total is not None:
            self._validate_sale_summary(None, expected_total)

        self._ensure_sale_mode()
        self.window.set_focus()
        press(self.window, "F3")
        payment = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if payment is None:
            raise capture_unknown_state(self.window, "voucher_payment_form_not_open")
        self._observe_dialog(payment, "before_select_voucher_payment")
        self._select_payment_option(payment, "Vale Troca", "voucher_payment_method_missing")
        time.sleep(0.3)

        current = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if current is None:
            raise capture_unknown_state(self.window, "voucher_payment_branch_not_open")

        # The documented branch is Vale Troca > Voucher. Some builds expose
        # Voucher as a second TListBox; if it is not exposed, preserve the
        # actual form for diagnosis instead of choosing by position.
        voucher_list = self._payment_method_list(current)
        if voucher_list is not None:
            options = voucher_list.item_texts()
            if self._payment_method_index(options, "Voucher") is not None:
                self._select_payment_option(current, "Voucher", "voucher_option_missing")
                time.sleep(0.3)
                current = self._wait_for_top_level_class("TFrmInserirPgto", timeout) or current

        # In the generation branch the SATPDV can open the native save dialog
        # immediately after selecting Voucher, while TFrmInserirPgto remains
        # visible but disabled.  Detect that documented transition before
        # sending ENTER to the payment form.
        save_dialog_already_open = self._find_voucher_save_dialog(0.8)

        if voucher_code is not None:
            if save_dialog_already_open is not None:
                raise capture_unknown_state(
                    save_dialog_already_open, "voucher_save_dialog_before_barcode"
                )
            code_control = self._focused_payment_control(current)
            if code_control is None:
                raise capture_unknown_state(current, "voucher_barcode_field_missing")
            expected_code = str(voucher_code).strip()
            code_control.click_input()
            code_control.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.03)
            code_control.type_keys(expected_code, set_foreground=True, pause=0.03)
            observed = (code_control.window_text() or "").strip()
            if expected_code not in observed and not observed.endswith(expected_code):
                raise AssertionError(
                    f"Campo Cód. Barras nao recebeu o voucher: "
                    f"esperado={expected_code!r}, observado={observed!r}"
                )
            press(code_control, "ENTER")
        else:
            # Generation does not provide a barcode to type. The confirmation
            # command is sent only to the mapped payment form after the
            # Vale Troca > Voucher branch was identified.
            if save_dialog_already_open is None:
                current.set_focus()
                if not _is_visible_and_enabled(current):
                    raise capture_unknown_state(current, "voucher_payment_form_disabled_before_submit")
                press(current, "ENTER")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._wait_for_top_level_class("TFrmInserirPgto", 0.1) is None:
                self.last_sale_submitted = True
                break
            time.sleep(0.1)
        else:
            raise capture_unknown_state(current, "voucher_payment_completion_unresolved")

        proof_path = self._save_voucher_proof(proof_stem, timeout)
        proof_text = self._read_saved_proof(proof_path)
        code = self._extract_voucher_code(proof_text, proof_path)
        receipt = self._find_receipt_window()
        if receipt is not None:
            self._close_receipt(receipt)
        main = None
        # Closing TppPrintPreview can re-enable TFrmPDV asynchronously. The
        # proof is already saved at this point; allow the VCL transition a
        # bounded grace period instead of treating a temporarily disabled PDV
        # as an unmapped voucher result.
        deadline = time.monotonic() + max(timeout, 30.0)
        while time.monotonic() < deadline:
            if self._find_receipt_window() is not None:
                time.sleep(0.2)
                continue
            candidate = self._wait_for_top_level_class("TFrmPDV", 0.2)
            if candidate is not None and _is_visible_and_enabled(candidate):
                main = candidate
                break
            time.sleep(0.1)
        if main is None:
            raise capture_unknown_state(self.window, "voucher_post_proof_pdv_not_ready")
        self.window = main
        return proof_path, code

    def open_voucher_payment_form(
        self, voucher_code: str | None = None, timeout: float | None = None
    ) -> Any:
        """Open ``Vale Troca > Voucher`` and optionally fill its barcode."""
        timeout = min(self.action_timeout, 15.0) if timeout is None else timeout
        self._ensure_sale_mode()
        self.window.set_focus()
        press(self.window, "F3")
        payment = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if payment is None:
            raise capture_unknown_state(self.window, "voucher_payment_form_not_open")
        self._select_payment_option(payment, "Vale Troca", "voucher_payment_method_missing")
        time.sleep(0.3)
        current = self._wait_for_top_level_class("TFrmInserirPgto", timeout)
        if current is None:
            raise capture_unknown_state(self.window, "voucher_payment_branch_not_open")
        voucher_list = self._payment_method_list(current)
        if voucher_list is not None:
            options = voucher_list.item_texts()
            if self._payment_method_index(options, "Voucher") is not None:
                self._select_payment_option(current, "Voucher", "voucher_option_missing")
                time.sleep(0.3)
                current = self._wait_for_top_level_class("TFrmInserirPgto", timeout) or current
        if voucher_code is not None:
            code_control = self._focused_payment_control(current)
            if code_control is None:
                raise capture_unknown_state(current, "voucher_barcode_field_missing")
            expected_code = str(voucher_code).strip()
            code_control.click_input()
            code_control.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.03)
            code_control.type_keys(expected_code, set_foreground=True, pause=0.03)
            observed = (code_control.window_text() or "").strip()
            if expected_code not in observed and not observed.endswith(expected_code):
                raise AssertionError(
                    f"Campo Cód. Barras nao recebeu o voucher: "
                    f"esperado={expected_code!r}, observado={observed!r}"
                )
        return current

    def submit_voucher_payment(
        self,
        payment: Any,
        timeout: float | None = None,
        remaining_payment_method: str | None = None,
        expected_remaining: Decimal | str | None = None,
    ) -> None:
        """Submit a voucher and optionally settle a remaining balance.

        ``TFrmInserirPgto`` remains open after a partial voucher payment. In
        that documented state, select the second payment method with the
        logical ``TListBox`` selection, verify the displayed remaining amount,
        and confirm it with ENTER before waiting for ``PDV_READY``.
        """
        timeout = min(self.action_timeout, 15.0) if timeout is None else timeout
        control = self._focused_payment_control(payment)
        press(control or payment, "ENTER")
        if remaining_payment_method is not None:
            deadline = time.monotonic() + timeout
            current = None
            while time.monotonic() < deadline:
                candidate = self._wait_for_top_level_class("TFrmInserirPgto", 0.2)
                if candidate is not None and _is_visible_and_enabled(candidate):
                    current = candidate
                    break
                time.sleep(0.1)
            if current is None:
                raise capture_unknown_state(payment, "voucher_remaining_payment_form_missing")
            self._select_payment_option(
                current,
                remaining_payment_method,
                "voucher_remaining_payment_method_missing",
            )
            time.sleep(0.25)
            remaining_control = self._focused_payment_control(current)
            if remaining_control is None:
                raise capture_unknown_state(current, "voucher_remaining_amount_field_missing")
            if expected_remaining is not None:
                try:
                    observed = parse_money(remaining_control.window_text() or "")
                except (AssertionError, ValueError):
                    raise capture_unknown_state(current, "voucher_remaining_amount_unreadable")
                expected = Decimal(str(expected_remaining)).quantize(Decimal("0.01"))
                if observed != expected:
                    raise AssertionError(
                        f"Saldo residual divergente; esperado={expected}, encontrado={observed}"
                    )
            press(remaining_control, "ENTER")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._wait_for_top_level_class("TFrmInserirPgto", 0.1) is None:
                self.last_sale_submitted = True
                receipt = self._find_receipt_window()
                if receipt is not None:
                    self._close_receipt(receipt)
                self._handle_receipt_repeat_prompt(
                    show_again=False,
                    expected_product=None,
                    expected_total=None,
                    timeout=timeout,
                )
                main = self._wait_for_top_level_class("TFrmPDV", 0.5)
                if main is not None and _is_visible_and_enabled(main):
                    self.window = main
                    return
            time.sleep(0.1)
        raise capture_unknown_state(payment, "voucher_payment_completion_unresolved")

    def observe_voucher_payment_without_settling(
        self, payment: Any, timeout: float | None = None
    ) -> dict[str, str]:
        """Submit the voucher once and return the raw post-submit UI state.

        Diagnostic-only helper for VEN-15. It never selects a second payment
        method, closes a dialog, or settles a residual balance.
        """
        timeout = min(self.action_timeout, 15.0) if timeout is None else timeout
        control = self._focused_payment_control(payment)
        press(control or payment, "ENTER")
        # VCL may populate the residual balance on a later repaint.
        time.sleep(0.6)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self._wait_for_top_level_class("TFrmInserirPgto", 0.2)
            if current is not None:
                text = self._window_text(current)
                self._observe_dialog(current, "VEN-15_raw_after_voucher_submit")
                return {
                    "state": "PAYMENT_DIALOG_REMAINS",
                    "class_name": self._window_class(current),
                    "text": text,
                }

            receipt = self._find_receipt_window()
            if receipt is not None:
                text = self._window_text(receipt)
                self._observe_dialog(receipt, "VEN-15_raw_receipt_after_voucher_submit")
                return {
                    "state": "RECEIPT_VISIBLE",
                    "class_name": self._window_class(receipt),
                    "text": text,
                }

            main = self._wait_for_top_level_class("TFrmPDV", 0.2)
            if main is not None:
                return {
                    "state": "PDV_READY",
                    "class_name": self._window_class(main),
                    "text": self._window_text(main),
                }
            time.sleep(0.1)

        try:
            candidate = next(
                (window for window in self._top_level_windows() if _is_visible_and_enabled(window)),
                None,
            )
            if candidate is not None:
                text = self._window_text(candidate)
                self._observe_dialog(candidate, "VEN-15_raw_unresolved_state")
                return {
                    "state": "UNRESOLVED",
                    "class_name": self._window_class(candidate),
                    "text": text,
                }
        except Exception:
            pass
        return {"state": "UNRESOLVED", "class_name": "", "text": ""}

    def _select_payment_option(self, payment: Any, option: str, context: str) -> None:
        payment_list = self._payment_method_list(payment, option)
        if payment_list is None:
            raise capture_unknown_state(payment, f"{context}_list_missing")
        items = payment_list.item_texts()
        index = self._payment_method_index(items, option)
        if index is None:
            raise capture_unknown_state(payment, context)
        payment_list.set_focus()
        press(payment_list, "HOME")
        for _ in range(index):
            press(payment_list, "DOWN")
        if not self._payment_selection_is(payment_list, index):
            raise capture_unknown_state(payment, f"{context}_selection_mismatch")
        press(payment_list, "ENTER")

    def _save_voucher_proof(self, proof_stem: str, timeout: float) -> Path:
        """Save the voucher using only a recognized native save dialog."""
        dialog = self._find_voucher_save_dialog(timeout)

        if dialog is None:
            raise capture_unknown_state(self.window, "voucher_save_dialog_not_recognized")

        self._observe_dialog(dialog, "before_save_voucher_proof")
        report_root = Path(os.environ.get("PDV_REPORT_ROOT", "reports"))
        output_dir = (report_root / "vouchers").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        # The native dialog is filtered as Documento PDF (*.pdf); use an
        # absolute PDF target so the Windows file dialog does not interpret a
        # relative .txt name under Downloads or open a generic OK error modal.
        requested = output_dir / f"{proof_stem}.pdf"
        suffix = 2
        while requested.exists():
            requested = output_dir / f"{proof_stem}_{suffix}.pdf"
            suffix += 1

        edits: list[Any] = []
        for class_name in ("Edit", "TEdit"):
            try:
                edits.extend(
                    c for c in dialog.descendants(class_name=class_name)
                    if _is_visible_and_enabled(c)
                )
            except Exception:
                continue
        if not edits:
            raise capture_unknown_state(dialog, "voucher_save_filename_field_missing")
        filename = edits[-1]
        filename.click_input()
        filename.type_keys("^{A}{BACKSPACE}", set_foreground=True, pause=0.03)
        filename.type_keys(str(requested), set_foreground=True, pause=0.03)
        observed = (filename.window_text() or "").strip()
        if requested.name not in observed and str(requested) not in observed:
            raise AssertionError(
                f"Dialogo de salvamento nao reteve o path previsivel: "
                f"esperado={requested}, observado={observed!r}"
            )

        previous_mtime = requested.stat().st_mtime if requested.exists() else None
        saved = False
        for class_name in ("Button", "TButton", "TBitBtn"):
            try:
                buttons = dialog.descendants(class_name=class_name)
            except Exception:
                continue
            for button in buttons:
                caption = (
                    self._ascii(button.window_text() or "")
                    .replace("&", "")
                    .strip()
                    .casefold()
                )
                if caption not in {"salvar", "save"} or not _is_visible_and_enabled(button):
                    continue
                button.click_input()
                saved = True
                break
            if saved:
                break
        if not saved:
            raise capture_unknown_state(dialog, "voucher_save_button_missing")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._confirm_voucher_overwrite_if_present()
            candidates = [requested]
            candidates.extend(sorted(output_dir.glob(f"{proof_stem}.*")))
            for path in candidates:
                if path.exists() and path.is_file() and path.stat().st_size > 0:
                    if path == requested and previous_mtime is not None:
                        if path.stat().st_mtime <= previous_mtime:
                            continue
                    return path
            time.sleep(0.1)
        raise AssertionError(f"Comprovante do voucher nao foi salvo em {output_dir}")

    def _confirm_voucher_overwrite_if_present(self) -> bool:
        """Confirm only the recognized native overwrite prompt for this proof."""
        for dialog in self._top_level_windows():
            try:
                title = self._ascii(dialog.window_text() or "")
                full_text = self._ascii(self._window_text(dialog))
                if not re.search(r"confirmar\s+salvar|arquivo\s+ja\s+existe", f"{title} {full_text}", re.I):
                    continue
                self._observe_dialog(dialog, "before_confirm_voucher_overwrite")
                for class_name in ("Button", "TButton", "TBitBtn"):
                    for button in dialog.descendants(class_name=class_name):
                        caption = (
                            self._ascii(button.window_text() or "")
                            .replace("&", "")
                            .strip()
                            .casefold()
                        )
                        if caption == "sim" and _is_visible_and_enabled(button):
                            button.click_input()
                            return True
            except Exception:
                continue
        return False

    def _find_voucher_save_dialog(self, timeout: float = 0.0) -> Any | None:
        """Return the recognized native voucher save dialog, if present."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            for candidate in self._top_level_windows():
                try:
                    text = self._ascii(self._window_text(candidate))
                    cls = self._window_class(candidate)
                    if cls in {"#32770", "TSaveDialog", "TFileSaveDialog"} and re.search(
                        r"salvar|save|comprovante|voucher|vale", text, re.IGNORECASE
                    ):
                        return candidate
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    @staticmethod
    def _read_saved_proof(path: Path) -> str:
        if path.suffix.casefold() == ".pdf":
            extracted = ""
            try:
                from pypdf import PdfReader

                extracted = "\n".join(
                    page.extract_text() or "" for page in PdfReader(str(path)).pages
                )
            except Exception:
                extracted = ""
            if extracted.strip():
                return extracted

            # ReportBuilder can produce a graphics-only PDF. Render the
            # first page and OCR the saved proof as a last-resort evidence
            # path; the rendered PNG remains beside the PDF for inspection.
            try:
                import shutil
                import subprocess

                import pytesseract
                from PIL import Image

                PdvPage._configure_tesseract(pytesseract)
                converter = shutil.which("pdftoppm")
                if converter is None:
                    return ""
                prefix = path.with_name(f"{path.stem}_ocr")
                subprocess.run(
                    [converter, "-f", "1", "-singlefile", "-png", "-r", "180", str(path), str(prefix)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                image_path = prefix.with_suffix(".png")
                if not image_path.exists():
                    return ""
                with Image.open(image_path) as image:
                    return pytesseract.image_to_string(image, config="--psm 6")
            except Exception:
                return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    @classmethod
    def _extract_voucher_code(cls, text: str, path: Path) -> str:
        normalized = cls._ascii(text)
        patterns = (
            r"c[oó]digo\s*(?:de\s*)?barras\D{0,40}(\d{8,30})",
            r"vale\s+troca\D{0,40}(\d{8,30})",
            r"voucher\D{0,40}(\d{8,30})",
            r"(?m)^\s*(\d{8,30})\s*$",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                return match.group(1)
        raise AssertionError(
            f"Comprovante salvo sem codigo de barras reconhecivel: path={path}; "
            f"texto={text[:1000]!r}"
        )

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
        def is_closed() -> bool:
            try:
                return not receipt.exists() or not receipt.is_visible()
            except Exception:
                # Destroyed VCL HWNDs can raise while querying visibility.
                return True

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
                        time.sleep(0.25)
                        if is_closed():
                            return
                except Exception:
                    continue

        # TppPrintPreview exposes the toolbar as TppToolbar/TppTBX controls,
        # not as a Win32 button. ESC is the documented preview close action,
        # but it must be verified because the previous implementation marked
        # it closed without checking the HWND.
        try:
            receipt.set_focus()
            receipt.type_keys("{ESC}", set_foreground=True)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if is_closed():
                    return
                time.sleep(0.1)
        except Exception:
            pass

        # Known TppPrintPreview fallback used by the shared app teardown. It
        # sends WM_CLOSE to the recognized preview window and does not guess a
        # screen coordinate or interact with an unknown modal.
        try:
            receipt.close()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if is_closed():
                    return
                time.sleep(0.1)
        except Exception:
            pass
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
    def _payment_method_list(payment: Any, desired: str | None = None) -> Any | None:
        try:
            lists = [
                control
                for control in payment.descendants(class_name="TListBox")
                if control.is_visible() and control.is_enabled()
            ]
            if desired:
                wanted = PdvPage._normalize_payment_method(desired)
                for control in lists:
                    try:
                        options = control.item_texts()
                    except Exception:
                        options = []
                    if any(
                        wanted in PdvPage._normalize_payment_method(str(option or ""))
                        for option in options
                    ):
                        return control
            return lists[0] if lists else None
        except Exception:
            return None

    def _convenio_tab(self, payment: Any, context_label: str) -> Any:
        """Return the active ``TTabSheet`` for the Convênio branch.

        ``TFrmInserirPgto`` contains controls from more than one payment
        branch in the Win32 tree.  A global search for ``TJvValidateEdit``
        therefore finds the amount field in the Formas tab (for example
        ``R$ 1,00``) before it finds the associated CPF field.  The DFM
        hierarchy is the reliable boundary here: the associated controls
        belong to the ``TTabSheet`` named ``Convênio``.
        """
        try:
            tabs = payment.descendants(class_name="TTabSheet")
        except Exception:
            tabs = []
        for tab in tabs:
            try:
                title = self._ascii(tab.window_text() or "")
                if title == "convenio" or "convenio" in title:
                    return tab
            except Exception:
                continue
        raise capture_unknown_state(payment, f"{context_label}_convenio_tab_missing")

    def convenio_associated_field(self, payment: Any, context_label: str) -> Any:
        """Locate the associated CPF editor inside the Convênio tab.

        In the homologation build the CPF editor is a ``TJvValidateEdit``
        inside ``TTabSheet(Convênio)``.  The payment amount editor is another
        ``TJvValidateEdit`` in ``TTabSheet(Formas)`` and must never be used
        for this operation.  The CPF editor is the uppermost eligible edit
        in the Convênio tab (the subsequent controls are saldo/limite and
        value fields).  We also reject a value formatted as currency as a
        defensive check against a future hierarchy change.
        """
        tab = self._convenio_tab(payment, context_label)
        try:
            candidates = [
                control
                for class_name in ("TJvValidateEdit", "TEdit", "TJvEdit")
                # Some VCL child edits are painted/created dynamically and
                # are omitted by ``TTabSheet.descendants()`` while the tab is
                # inactive.  Query the form tree and use the real parent
                # relationship to retain only controls owned by Convênio.
                for control in payment.descendants(class_name=class_name)
                # The CPF edit is intentionally disabled until an associate
                # is selected.  It must still be returned for mapping and
                # the disabled state is itself a useful precondition signal.
                if _is_visible(control)
                and self._control_parent_title(control) == "convenio"
            ]
        except Exception:
            candidates = []
        candidates = [
            control
            for control in candidates
            if not re.search(r"R\$\s*[0-9]", self._read_input_value(control))
        ]
        try:
            candidates.sort(key=lambda control: control.rectangle().top)
        except Exception:
            pass
        if not candidates:
            raise capture_unknown_state(payment, f"{context_label}_convenio_cpf_field_missing")
        return candidates[0]

    @staticmethod
    def _control_parent_is_tab(control: Any, tab: Any) -> bool:
        """Check the actual Win32 parent chain instead of tab descendants."""
        try:
            parent = control.parent()
            return parent.handle == tab.handle
        except Exception:
            return False

    @staticmethod
    def _control_parent_title(control: Any) -> str:
        try:
            return PdvPage._ascii(control.parent().window_text() or "")
        except Exception:
            return ""

    def focus_convenio_associate(
        self,
        payment: Any,
        context_label: str,
        preferred_name: str | None = None,
    ) -> Any:
        """Select an existing Convênio associate using keyboard navigation.

        The homologation data exposes the associates ``Fulano da Silva`` and
        ``Ciclano de Souza`` in the Convênio tab.  Depending on the VCL
        build, the list is exposed as a list-like ``TListBox`` or as the
        painted ``TMemo`` containing one name per line.  In both cases the
        logical selection is moved with HOME/DOWN and only then confirmed by
        the caller with ENTER; no positional mouse click is used. When the
        build exposes the explicit ``Convenio`` option, callers should pass
        ``preferred_name="Convenio"`` instead of selecting by row index.
        """
        tab = self._convenio_tab(payment, context_label)

        # Prefer a real list control when it exposes the associate names.
        try:
            list_controls = list(payment.descendants(class_name="TListBox"))
        except Exception:
            list_controls = []
        for control in list_controls:
            if not _is_visible(control):
                continue
            try:
                items = [str(item or "").strip() for item in control.item_texts()]
            except Exception:
                items = []
            names = [item for item in items if item]
            if not names:
                continue
            index = self._associate_index(names, preferred_name, payment, context_label)
            control.set_focus()
            press(control, "HOME")
            for _ in range(index):
                press(control, "DOWN")
            time.sleep(0.2)
            return control

        # The current SATPDV build paints the two names in a TMemo.  It still
        # receives the same keyboard navigation used by the Delphi handler.
        try:
            memos = list(payment.descendants(class_name="TMemo"))
        except Exception:
            memos = []
        for control in memos:
            if not _is_visible(control):
                continue
            text = self._read_input_value(control)
            names = [line.strip() for line in re.split(r"\r?\n", text) if line.strip()]
            if not names:
                continue
            index = self._associate_index(names, preferred_name, payment, context_label)
            control.set_focus()
            press(control, "HOME")
            for _ in range(index):
                press(control, "DOWN")
            time.sleep(0.2)
            return control

        # Keep the failure evidence specific: the caller must not press ENTER
        # on the unrelated payment amount field when the associate list is not
        # exposed by the current build.
        raise capture_unknown_state(payment, f"{context_label}_convenio_associate_list_missing")

    def _associate_index(
        self,
        names: list[str],
        preferred_name: str | None,
        payment: Any,
        context_label: str,
    ) -> int:
        if not preferred_name:
            return 1 if len(names) > 1 else 0
        wanted = self._ascii(preferred_name)
        for index, name in enumerate(names):
            if wanted in self._ascii(name):
                return index
        raise capture_unknown_state(
            payment, f"{context_label}_preferred_associate_missing"
        )

    def focus_convenio_cpf(self, payment: Any, context_label: str) -> Any:
        """Move from the selected associate to the Convênio CPF editor.

        After ENTER confirms the associate, this VCL form places focus on the
        ``Parcelas`` editor.  The tab order is ``Parcelas -> Disponível ->
        Associado -> CPF/CNPJ`` in reverse, so three SHIFT+TAB keystrokes
        reach the CPF editor without clicking its painted label.
        """
        payment.set_focus()
        for _ in range(3):
            payment.type_keys("+{TAB}", set_foreground=True, pause=0.08)
        time.sleep(0.2)
        try:
            field = self.convenio_associated_field(payment, context_label)
        except Exception:
            raise capture_unknown_state(payment, f"{context_label}_convenio_cpf_focus_failed")
        try:
            field.set_focus()
        except Exception:
            pass
        return field

    def activate_payment_tab(self, payment: Any, tab_title: str, context_label: str) -> Any:
        """Activate a payment page with the VCL page-control keyboard.

        Selecting ``5 - Convênio`` in the Formas list does not itself change
        the visible page in this build.  The Delphi ``TPageControl`` keeps
        Formas active until Ctrl+Tab is sent.  Starting from the documented
        Formas page, cycle through the tab headers with Ctrl+Tab and then
        return the named ``TTabSheet``.  This keeps the interaction on
        TAB/ENTER/arrows/ESC instead of using a coordinate click.
        """
        wanted = self._ascii(tab_title)
        try:
            page_control = next(
                control
                for control in payment.descendants(class_name="TPageControl")
                if _is_visible(control)
            )
        except (StopIteration, Exception):
            raise capture_unknown_state(payment, f"{context_label}_page_control_missing")

        try:
            tabs = payment.descendants(class_name="TTabSheet")
            order = [self._ascii(tab.window_text() or "") for tab in tabs]
            target_index = next(
                index for index, title in enumerate(order) if title == wanted
            )
        except (StopIteration, Exception):
            raise capture_unknown_state(payment, f"{context_label}_tab_missing")

        # Win32 enumeration puts the currently active VCL page first, but the
        # order of the remaining pages changes with the active page.  Use the
        # stable header order shown by TFrmInserirPgto instead of treating the
        # enumeration index as a tab position.
        static_order = [
            "formas", "pix", "cartao", "cheque", "convenio",
            "vale troca", "outro", "qr code", "lista",
        ]
        current_title = self._ascii(tabs[0].window_text() or "")
        try:
            current_index = static_order.index(current_title)
            target_index = static_order.index(wanted)
            steps = (target_index - current_index) % len(static_order)
        except ValueError:
            steps = target_index
        page_control.set_focus()
        # Ctrl+Tab is the VCL keyboard command for the next page.
        for _ in range(steps):
            page_control.type_keys("^{TAB}", set_foreground=True, pause=0.08)
        time.sleep(0.25)
        for tab in tabs:
            try:
                if self._ascii(tab.window_text() or "") == wanted:
                    return tab
            except Exception:
                continue
        raise capture_unknown_state(payment, f"{context_label}_tab_activation_failed")

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
        payment_list = self._payment_method_list(payment, payment_method)
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
                captions = [str(value or "") for value in combo.texts()]
            except Exception:
                captions = []
            try:
                captions.append(str(combo.window_text() or ""))
            except Exception:
                pass
            if "descri" not in self._ascii(" ".join(captions)):
                continue
            try:
                if not combo.is_visible():
                    continue
            except Exception:
                continue
            description_combo = combo
            break
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
            # Após fechar TFrmInserirPgto, o handle do TFrmPDV pode atravessar
            # uma transição VCL e ficar temporariamente não acionável. Releia
            # a janela pelo PID antes do F6; não envie a tecla para um wrapper
            # antigo/possivelmente desabilitado.
            try:
                if not self.window.is_enabled():
                    refreshed = self._wait_for_top_level_class(
                        "TFrmPDV", min(self.action_timeout, 3.0)
                    )
                    if refreshed is not None:
                        self.window = refreshed
            except Exception:
                pass
            try:
                press(self.window, "F6")
            except Exception:
                refreshed = self._wait_for_top_level_class(
                    "TFrmPDV", min(self.action_timeout, 3.0)
                )
                if refreshed is None:
                    raise
                self.window = refreshed
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

    def _dismiss_quantity_limit_warning(self, timeout: float = 1.5) -> bool:
        """Libera o PDV no aviso conhecido de quantidade máxima.

        A build homologada exibe uma dialog VCL com o texto ``Qtde inserida
        maior que quantidade máxima permitida!``. O próprio formulário
        documenta F2/Space como comandos de confirmação; somente essa dialog
        reconhecida pode receber essas teclas.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            for window in self._top_level_windows():
                try:
                    text = self._ascii(self._window_text(window))
                    if "qtde inserida maior que quantidade maxima permitida" not in text:
                        continue
                    if not _is_visible_and_enabled(window):
                        continue
                    self._observe_dialog(window, "before_dismiss_quantity_limit_warning")
                    self._confirm_quantity_limit_dialog(window)
                    return True
                except Exception:
                    continue
            time.sleep(0.05)
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

    def wait_for_client_form_after_payment(
        self, payment: Any, context_label: str, timeout: float | None = None
    ) -> Any:
        """Require ``TFrmCPFCNPJ`` before the convenio CPF can be cancelled.

        The roteiro's VEN-36 sequence is F3 -> Convênio -> Enter -> CPF form
        -> ESC.  This guard keeps ESC targeted at the real ``TFrmCPFCNPJ``;
        it must never be sent directly to ``TFrmInserirPgto`` when the CPF
        form was not created.
        """
        dialog = self.wait_until_window_class("TFrmCPFCNPJ", timeout)
        if dialog is None:
            raise capture_unknown_state(
                payment, f"{context_label}_client_form_not_open_after_convenio"
            )
        self._observe_dialog(dialog, f"{context_label}_client_form_open")
        return dialog

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
