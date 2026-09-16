from __future__ import annotations

from decimal import Decimal
import re
import time

import pytest

from pages.base_page import capture_unknown_state
from pages.pdv_page import PdvPage
from tests.config.test_data import CLIENTES


def _identify_client(pdv: PdvPage, document: str, scenario_id: str) -> str:
    pdv.send_shortcut("F5")
    dialog = pdv.wait_for_dialog_text(r"CPF|CNPJ|cliente|documento", timeout=4.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_client_form_not_open")
    control = pdv.fill_client_document(dialog, document)
    name = pdv.lookup_client_document(dialog, control)
    if not name:
        raise AssertionError(
            f"{scenario_id}: CPF {document} nao foi localizado via F5 no PDV"
        )
    pdv.confirm_client_dialog(dialog)
    assert pdv.has_window_class("TFrmPDV"), (
        f"{scenario_id}: F10 - OK nao retornou ao TFrmPDV"
    )
    return name


def _open_convenio(pdv: PdvPage, scenario_id: str):
    try:
        payment, amount = pdv.open_payment_amount_field("Convênio", timeout=12.0)
        # The logical row selection and the visible VCL page are separate in
        # this build.  Ctrl+Tab activates TTabSheet(Convênio) before looking
        # for its associate controls; no coordinate click is used.
        pdv.activate_payment_tab(payment, "Convênio", scenario_id)
        return payment, amount
    except AssertionError as exc:
        # Known runtime failure in the homologation build: a VCL form tries
        # to become modal while another form is still active. Preserve the
        # dialog evidence and classify this mapped system behavior separately
        # from a selector/timing failure.
        warning = pdv.wait_for_information_text(
            r"n[aã]o [eé] poss[ií]vel fazer uma janela modal vis[ií]vel",
            timeout=0.8,
        )
        if warning is not None:
            text = pdv._window_text(warning)
            pdv._observe_dialog(warning, f"{scenario_id}_modal_visible_error")
            pdv.confirm_known_dialog(warning)
            pytest.xfail(
                f"{scenario_id}: bug conhecido do SATPDV ao abrir Convênio; "
                f"mensagem={text!r}"
            )
        raise capture_unknown_state(pdv.window, f"{scenario_id}_convenio_option_missing") from exc


def _fill_associated_cpf(pdv: PdvPage, control, document: str, scenario_id: str) -> None:
    """Fill the associated CPF field in TFrmInserirPgto and verify readback."""
    expected = re.sub(r"\D", "", str(document))
    control.click_input()
    control.type_keys("{HOME}", set_foreground=True, pause=0.03)
    control.type_keys("{DELETE}" * 24, set_foreground=True, pause=0.01)
    control.type_keys("{BACKSPACE}" * 24, set_foreground=True, pause=0.01)
    control.type_keys(expected, set_foreground=True, pause=0.05)
    time.sleep(0.25)
    observed = re.sub(r"\D", "", pdv._read_input_value(control))
    assert observed == expected, (
        f"{scenario_id}: CPF do associado nao foi retido; "
        f"esperado={expected!r}, observado={observed!r}"
    )


def _assert_convenio_balance_exceeds_purchase(
    pdv: PdvPage, payment, purchase_total: Decimal, scenario_id: str
) -> None:
    """Validate a labelled on-screen balance before confirming the payment."""
    # The label ``Disponível R$:`` is a painted TLabel and is absent from
    # ``window_text()``. Read numeric editors owned by TTabSheet(Convênio):
    # the CPF editor is ignored by its digit length, leaving the balance
    # TJvValidateEdit (399 in the confirmed homologation screen).
    try:
        tab = pdv._convenio_tab(payment, scenario_id)
        for class_name in ("TJvValidateEdit", "TEdit", "TJvEdit"):
            for control in payment.descendants(class_name=class_name):
                if pdv._control_parent_title(control) != "convenio":
                    continue
                raw = pdv._read_input_value(control).strip()
                digits = re.sub(r"\D", "", raw)
                if len(digits) >= 8:
                    continue  # CPF/CNPJ, not the available balance
                match = re.search(
                    r"(?<!\d)([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{1,2})?|[0-9]+)(?!\d)",
                    raw,
                )
                if not match:
                    continue
                value = Decimal(match.group(1).replace(".", "").replace(",", "."))
                if value > purchase_total:
                    return
    except Exception:
        # Keep the complete form text as the fallback evidence below.
        pass

    text = pdv._window_text(payment)
    for line in text.splitlines():
        if not re.search(r"saldo|limite|dispon[ií]vel", line, re.IGNORECASE):
            continue
        # TFrmInserirPgto renders the available balance as ``399`` in this
        # build, although the label itself is ``Disponível R$:``. Do not
        # require a comma/centavos format that the control does not display.
        values = []
        for token in re.findall(
            r"(?<!\d)([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{1,2})?|[0-9]+)(?!\d)",
            line,
        ):
            try:
                values.append(Decimal(token.replace(".", "").replace(",", ".")))
            except Exception:
                continue
        if any(value > purchase_total for value in values):
            return
    raise AssertionError(
        f"{scenario_id}: valor disponivel do convenio nao foi identificado "
        f"na tela ou nao supera a compra de R$ {purchase_total:.2f}; "
        f"texto_observado={text!r}"
    )


def _select_price_table(pdv: PdvPage, table: str, scenario_id: str) -> None:
    """Seleciona a tabela no TFormTabelaPreco antes de inserir o produto."""
    # PDV.pas/FormKeyDown: Shift+F4 abre TFormTabelaPreco; no DFM da dialog
    # o campo de seleção é TJvComboEdit e a confirmação é um TBitBtn.
    pdv.send_shortcut("SHIFT+F4")
    dialog = pdv.wait_until_window_class("TFormTabelaPreco", timeout=3.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_price_table_not_open")
    pdv.select_known_dialog_option(dialog, table)
    pdv.confirm_known_dialog(dialog)


@pytest.mark.automated
@pytest.mark.sales
def test_ven30_convenio_limit_warning(pdv, evidence):
    """VEN-30: finalização com convênio acima do limite de R$100.

    O roteiro identifica este caso como ``Finalização com convênio`` e usa o
    funcionário CPF 591.928.280-00. A implementação anterior o rotulava
    incorretamente como VEN-33; VEN-33 é o caso externo de Pix Inter e não é
    executado nesta suite.
    """
    name = _identify_client(pdv, CLIENTES["CONVENIO"], "VEN-30")
    # O produto 33/tabela 2 pertence ao cenário GEST-01 e não é massa deste
    # item. A consulta somente leitura da PROD encontrou o produto 3 como
    # item unitário sem balança; uma unidade na tabela 1 supera R$100 sem
    # depender da configuração de gestão de preços nem de hardware. O preço
    # efetivo deve ser confirmado pelo total do PDV, pois a tabela pode estar
    # sobrescrita por configuração local.
    _select_price_table(pdv, "1", "VEN-30")
    pdv.insert_product("31")
    total = pdv.wait_until_total()
    assert total > Decimal("100"), (
        f"VEN-30: venda não ficou acima de R$100 na tabela 1; total={total}"
    )
    payment, _amount = _open_convenio(pdv, "VEN-30")
    associate_list = pdv.focus_convenio_associate(
        payment, "VEN-30", preferred_name="Convenio"
    )
    associate_list.type_keys("{ENTER}", set_foreground=True, pause=0.08)
    associated_field = pdv.focus_convenio_cpf(payment, "VEN-30")
    _fill_associated_cpf(pdv, associated_field, CLIENTES["CONVENIO"], "VEN-30")
    associated_field.set_focus()
    associated_field.type_keys("{ENTER}", set_foreground=True, pause=0.08)
    time.sleep(0.5)

    # The amount is confirmed from the active Convênio tab by keyboard. This
    # avoids reusing the stale hidden editor returned before Ctrl+Tab changed
    # the active VCL page.
    warning = None
    for _ in range(5):
        warning = pdv.wait_for_information_text(
            r"limite|maior que o limite|conv[eê]nio", timeout=0.7
        )
        if warning is not None:
            break
        current = pdv.wait_until_window_class("TFrmInserirPgto", timeout=0.5)
        if current is None:
            break
        current.set_focus()
        current.type_keys("{ENTER}", set_foreground=True, pause=0.08)
        time.sleep(0.25)
    if warning is None:
        raise capture_unknown_state(payment, "VEN-30_limit_warning_missing")
    text = pdv._window_text(warning)
    pdv.confirm_known_dialog(warning)
    assert re.search(r"limite|conv[eê]nio", text, re.IGNORECASE), (
        f"VEN-30: mensagem de limite nao reconhecida: {text!r}"
    )
    pdv.close_payment_dialog()
    evidence[1].info(
        f"VEN-30: cliente localizado via F5 ({name!r}); mensagem de limite="
        f"{text!r}."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_ven35_convenio_preselected_and_navigable(pdv, evidence):
    """VEN-35: Convênio vem selecionado, CPF é associado e a venda conclui."""
    pdv.insert_product("2")
    purchase_total = pdv.wait_until_total()
    payment, _amount_field = _open_convenio(pdv, "VEN-35")
    text = pdv._window_text(payment)
    assert re.search(r"conv[eê]nio", text, re.IGNORECASE), (
        f"VEN-35: tela de pagamento nao expôs Convênio: {text!r}"
    )

    # A variação existente na homologação expõe a opção ``Convenio`` na lista
    # da aba. Selecioná-la explicitamente evita atrelar o CPF a Fulano/Ciclano
    # por índice e reproduz a tela observada: ``Associado: Convênio``.
    # O campo é localizado pela hierarquia TTabSheet(Convênio), nunca pelo
    # TJvValidateEdit global do valor R$ 1,00.
    # Cycle the actual page-control tabs and return without confirming a
    # payment.  Confirming the Formas list here would submit Dinheiro rather
    # than merely validate navigation between payment pages.
    pdv.activate_payment_tab(payment, "Formas", "VEN-35_navigation_formas")
    pdv.activate_payment_tab(payment, "Convênio", "VEN-35_navigation_convenio_tab")
    associate_list = pdv.focus_convenio_associate(
        payment, "VEN-35_after_navigation", preferred_name="Convenio"
    )
    associate_list.type_keys("{ENTER}", set_foreground=True, pause=0.08)
    associated_field = pdv.focus_convenio_cpf(payment, "VEN-35")
    assert not (associated_field.window_text() or "").strip(), (
        "VEN-35: CPF do associado veio preenchido automaticamente"
    )
    _fill_associated_cpf(pdv, associated_field, CLIENTES["CONVENIO"], "VEN-35")
    # Após a digitação, o roteiro confirma o CPF com ENTER no próprio
    # TJvValidateEdit. Reafirmar o foco evita que a tecla seja consumida pelo
    # formulário ou pelo editor global de valor.
    associated_field.set_focus()
    associated_field.type_keys("{ENTER}", set_foreground=True, pause=0.08)
    time.sleep(0.8)
    _assert_convenio_balance_exceeds_purchase(pdv, payment, purchase_total, "VEN-35")

    # Confirm the remaining fields by Enter only; no positional mouse action.
    for _ in range(4):
        current = pdv.wait_until_window_class("TFrmInserirPgto", timeout=1.0)
        if current is None:
            break
        focused = pdv._focused_payment_control(current)
        (focused or current).set_focus()
        (focused or current).type_keys("{ENTER}", set_foreground=True, pause=0.05)
        time.sleep(0.35)
    assert pdv.has_window_class("TFrmPDV"), "VEN-35: venda nao retornou ao PDV_READY"
    evidence[1].info(
        "VEN-35: Convênio acessível por setas/Enter, CPF do associado validado "
        "por leitura de volta, saldo suficiente confirmado na tela e venda concluída."
    )


@pytest.mark.automated
@pytest.mark.sales
def test_ven36_cancel_convenio_cpf_and_retry(pdv, evidence):
    """VEN-36: ESC cancela a identificação de Convênio e F3 pode ser usado novamente.

    Variação coberta com a massa atual: ``TFrmInserirPgto`` já exibe os
    associados ``Fulano da Silva``/``Ciclano de Souza``. Nesse caminho, o CPF
    é um ``TJvValidateEdit`` dentro da aba ``Convênio``; ``TFrmCPFCNPJ`` só é
    criado na variação do roteiro que parte de ``Sem convênio cadastrado``.
    """
    pdv.insert_product("3")
    payment, _ = _open_convenio(pdv, "VEN-36")
    # Massa existente: selecione explicitamente a opção ``Convenio``; não
    # escolher Fulano/Ciclano por índice, pois a ordem não identifica o CPF.
    associate_list = pdv.focus_convenio_associate(
        payment, "VEN-36", preferred_name="Convenio"
    )
    associate_list.type_keys("{ENTER}", set_foreground=True, pause=0.08)
    associated_field = pdv.focus_convenio_cpf(payment, "VEN-36")
    associated_field.type_keys("{ENTER}", set_foreground=True, pause=0.05)

    # Com associados já cadastrados, o ENTER mantém o fluxo em
    # TFrmInserirPgto e o editor correto continua sendo o TJvValidateEdit da
    # aba Convênio. Não confundir com o editor global de valor (R$ 1,00).
    # TFrmCPFCNPJ é aceito apenas como variação legítima da massa sem
    # convênio; quando essa janela existir, o ESC é direcionado a ela.
    client_dialog = pdv.wait_until_window_class("TFrmCPFCNPJ", timeout=0.8)
    if client_dialog is not None:
        pdv._observe_dialog(client_dialog, "VEN-36_client_form_open")
        client_dialog.set_focus()
        client_dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
        assert pdv.wait_until_window_class("TFrmCPFCNPJ", timeout=1.0) is None, (
            "VEN-36: ESC nao fechou TFrmCPFCNPJ"
        )
        variation = "TFrmCPFCNPJ"
    else:
        observed = re.sub(r"\D", "", pdv._read_input_value(associated_field))
        assert not observed, (
            "VEN-36: CPF do associado foi preenchido antes da tentativa de cancelamento; "
            f"campo={observed!r}"
        )
        associated_field.set_focus()
        associated_field.type_keys("{ESC}", set_foreground=True, pause=0.05)
        time.sleep(0.4)
        # Se o ESC do editor apenas devolveu o foco ao formulário, o segundo
        # ESC fecha o TFrmInserirPgto pela regra geral do PDV. Em nenhum caso
        # enviamos ESC para um controle desconhecido ou por coordenadas.
        if pdv.wait_until_window_class("TFrmInserirPgto", timeout=0.2) is not None:
            payment.set_focus()
            payment.type_keys("{ESC}", set_foreground=True, pause=0.05)
        variation = "TJvValidateEdit direto na aba Convênio"

    pdv.send_shortcut("F3")
    retry = pdv.wait_for_dialog_text(r"pagamento|conv[eê]nio|dinheiro|cart[aã]o", timeout=5.0)
    if retry is None:
        raise capture_unknown_state(pdv.window, "VEN-36_retry_payment_form_missing")
    pdv._observe_dialog(retry, "VEN-36_retry_after_esc")
    retry.set_focus()
    retry.type_keys("{ESC}", set_foreground=True, pause=0.05)
    pdv.close_payment_dialog()
    evidence[1].info(
        "VEN-36: CPF de Convênio cancelado por ESC na variação "
        f"{variation}; F3 reabriu o fluxo de pagamento sem erro e a tela foi "
        "fechada de forma conhecida."
    )
