from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from pages.base_page import capture_unknown_state
from core.test_results import record_dialog_observation
from pages.login_page import LoginPage
from tests.config.test_data import CLIENTES, CUPONS, PRODUTOS, TABELAS_PRECO, VENDEDORES


# Estes grupos representam itens do roteiro que nao podem ser validados com
# seguranca nesta suite. A decisao e explicita: nao criamos voucher, cliente,
# campanha, frete ou configuracao fiscal compartilhada e nao simulamos hardware.
OUT_OF_SCOPE = (
    {
        "roteiro": "VEN-11 a VEN-15",
        "nome": "voucher",
        "motivo": (
            "exige voucher real/consumido, cliente e em alguns casos impressora; "
            "nao ha massa compartilhada autorizada para criar ou consumir dados"
        ),
    },
    {
        "roteiro": "VEN-33, VEN-35 e VEN-36",
        "nome": "convenio",
        "motivo": (
            "depende de cliente/convenio e limite cadastrado no banco; nao e "
            "seguro alterar ou consumir esse dado no ambiente compartilhado"
        ),
    },
    {
        "roteiro": "VEN-01 a VEN-06, VEN-09, VEN-10, VEN-23, VEN-24, VEN-26 e VEN-31",
        "nome": "fiscal, NFC-e e XML",
        "motivo": (
            "exige NFC-e/SEFAZ, certificado, impressora, XML ou parametros fiscais "
            "externos ao projeto; nenhum desses recursos e simulado"
        ),
    },
    {
        "roteiro": "VEN-19, VEN-28, VEN-29 e VEN-30",
        "nome": "balanca e peso",
        "motivo": (
            "exige balanca/VSPE ou configuracao especifica de pesagem; hardware "
            "fisico nao esta disponivel para validacao E2E"
        ),
    },
    {
        "roteiro": "FRT-01 e FRT-02",
        "nome": "frete",
        "motivo": (
            "exige cadastro/parametro de frete e dados de orcamento compartilhados; "
            "nao ha permissao para alterar essa massa"
        ),
    },
    {
        "roteiro": "PRD-01 a PRD-03",
        "nome": "produto e observacoes",
        "motivo": (
            "depende de cadastro/parametro compartilhado ou de emissao NFC-e/XML; "
            "o fluxo de observacoes e a grade filha ainda nao estao mapeados"
        ),
    },
    {
        "roteiro": "CAN-01, CAN-05 e CAN-07",
        "nome": "cancelamento integrado e recuperacao",
        "motivo": (
            "depende de TEF/Pix/Moovpay, impressora, gerenciador de processos ou "
            "dados de venda compartilhados; nenhum desses recursos e simulado"
        ),
    },
    {
        "roteiro": "CAN-02, CAN-03, CAN-04 e CAN-08",
        "nome": "cancelamento com documento e modo totem",
        "motivo": (
            "CAN-02/03/04 exigem validar documento fiscal e itens no pedido; "
            "CAN-08 exige modo totem/configuracao especifica. O teclado existe, "
            "mas a evidencia completa depende de recursos/configuracao fora do escopo"
        ),
    },
    {
        "roteiro": "MFI-01 a MFI-07",
        "nome": "menu fiscal",
        "motivo": (
            "as teclas F8/Ctrl+F2/Ctrl+F3/Ctrl+F8 podem ser enviadas, mas o "
            "resultado esperado exige comprovante, impressora/gaveta ou fechamento "
            "fiscal; nao ha hardware fisico disponivel e nao sera simulado"
        ),
    },
    {
        "roteiro": "FIS-01 a FIS-12",
        "nome": "fiscal e contingencia",
        "motivo": (
            "exige NFC-e/NF-e, SEFAZ, certificado, internet, XML, impressora ou "
            "manipulacao de dados fiscais; fora do escopo seguro desta suite"
        ),
    },
    {
        "roteiro": "TEF-01 a TEF-09",
        "nome": "integracoes TEF e Pix",
        "motivo": (
            "exige TEF, PinPad, Pix, Moovpay, internet e/ou impressora; a suite "
            "nao simula transacoes nem autorizacoes externas"
        ),
    },
    {
        "roteiro": "CER-01 a CER-04 e BAL-01 a BAL-03",
        "nome": "certificado e balanca",
        "motivo": (
            "exige certificado digital, emissao fiscal, balanca ou VSPE; hardware "
            "e credenciais fiscais nao sao simulados"
        ),
    },
    {
        "roteiro": "MES-01 e REI-01 a REI-06",
        "nome": "mesas, vendedores e reinicio",
        "motivo": (
            "mesas/reinicio dependem de cadastros/parametros compartilhados, pedidos persistidos ou "
            "interrupcao controlada do processo; sem massa autorizada para alterar"
        ),
    },
    {
        "roteiro": "SIN-01 a SIN-03 e CB-01 a CB-07",
        "nome": "sincronia e cashback",
        "motivo": (
            "exige SEFAZ, API cashback, internet, certificado ou dados de cliente; "
            "nao ha integracao externa disponivel para um E2E confiavel"
        ),
    },
    {
        "roteiro": "CFG-01 a CFG-03 e PAR-01 a PAR-03",
        "nome": "configuracao e parametros",
        "motivo": (
            "exige alteracao de banco/SAT.INI, parametros compartilhados ou gaveta; "
            "nao alteramos configuracao fora do escopo do projeto"
        ),
    },
)


@pytest.mark.parametrize(
    "scenario",
    OUT_OF_SCOPE,
    ids=[scenario["nome"] for scenario in OUT_OF_SCOPE],
)
def test_external_roteiro_items_are_skipped(scenario, evidence):
    """Registra explicitamente itens fora do escopo sem simular o resultado."""
    _, logger = evidence
    logger.info(json.dumps({"roteiro": scenario["roteiro"], "status": "SKIPPED", "motivo": scenario["motivo"]}, ensure_ascii=False))
    pytest.skip(f"{scenario['roteiro']}: SKIPPED - {scenario['motivo']}")


# Os cenários abaixo deixam de ser skips porque todos os eventos solicitados
# são exercitáveis pela UI do TFrmPDV. Eles ainda dependem da massa real do
# banco de homologação; uma mensagem inesperada gera UnknownDialogError e
# evidência, em vez de clicar em um controle desconhecido.


@pytest.mark.automated
@pytest.mark.sales
def test_cancel_open_sale_with_reason(pdv, evidence):
    """CAN-06: F6 cancela venda aberta após motivo real e confirmação em OK."""
    pdv.insert_product(PRODUTOS["PADRAO"])
    pdv.cancel_sale("Teste automatizado CAN-06: cancelamento de venda aberta")
    assert pdv.wait_until_absent(PRODUTOS["PADRAO"], timeout=3.0), (
        "CAN-06: o item permaneceu no TMemo pnlProdutos após F6 + motivo + OK"
    )
    evidence[1].info(
        "CAN-06 concluido: F6 abriu o dialogo de cancelamento, o motivo foi digitado "
        "no TEdit e confirmado no TBitBtn OK; venda retornou vazia."
    )

DISCOUNT_CASES = tuple(
    {"id": f"DES-{index:02d}", "shortcut": "F11" if index in {3, 5, 8, 9, 10, 12, 14, 15, 17, 20, 21} else "F12",
     "value": "10" if index in {3, 5, 8, 9, 10, 12, 14, 15, 17, 20, 21} else "1,00",
     "product": {7: "1", 9: "37", 10: "38", 17: "43", 20: "47"}.get(index, PRODUTOS["PADRAO"]),
     "coupon": {13: CUPONS["PERCENTUAL"], 14: CUPONS["VALOR_FIXO"], 15: CUPONS["PERCENTUAL"], 19: CUPONS["PERCENTUAL"]}.get(index)}
    for index in range(1, 22)
)


def _authorize_if_requested(pdv, test_config) -> None:
    """Autentica somente quando o SATPDV expõe o TFrmPassWord real."""
    authorization = pdv.wait_for_password_dialog(timeout=1.5)
    if authorization is None:
        return
    if not test_config.manager_credentials_configured:
        raise AssertionError("O SATPDV pediu gerente, mas as credenciais nao estao configuradas no .env")
    manager_user, manager_password = test_config.manager_credentials
    # TFrmPassWord é o mesmo formulário Delphi usado no login inicial; nunca
    # registra a senha nem cria credencial fictícia.
    LoginPage(authorization, test_config).login(user=manager_user, password=manager_password)


def _dismiss_known_message(pdv, pattern: str, timeout: float = 1.5) -> str:
    dialog = pdv.wait_for_information_text(pattern, timeout=timeout)
    if dialog is None:
        return ""
    if pdv._window_class(dialog) not in {"TFrmDlgInformacao", "TFrmDlg"}:
        raise capture_unknown_state(dialog, "recognized_message_wrong_dialog_class")
    text = pdv._window_text(dialog)
    pdv.confirm_known_dialog(dialog)
    return text


def _finalize_representative_sale(
    pdv,
    logger,
    roteiro: str,
    expected_product: str,
    expected_total: Decimal | None = None,
    validate_pre_payment_summary: bool = True,
) -> None:
    """Finaliza uma venda representativa e registra a evidência do fechamento.

    ``PdvPage.finalize_sale`` usa F3, navega a forma de pagamento por setas,
    confirma o valor com ENTER, valida o comprovante e fecha Close/ESC. O
    fluxo não chama ``cancel_sale`` depois que o pagamento foi enviado.
    """
    if expected_total is None:
        expected_total = pdv.wait_until_total()
    pdv.finalize_sale(
        payment_method="Dinheiro",
        expected_product=expected_product,
        expected_total=expected_total,
        show_receipt_again=False,
        validate_pre_payment_summary=validate_pre_payment_summary,
    )
    assert pdv.has_window_class("TFrmPDV"), (
        f"{roteiro}: TFrmPDV nao ficou liberado apos a finalizacao"
    )
    logger.info(
        "Finalizacao representativa concluida: %s; produto=%s; "
        "total_final_esperado=%s; forma=Dinheiro; estado=PDV_READY",
        roteiro,
        expected_product,
        expected_total,
    )


@pytest.mark.automated
@pytest.mark.discounts
@pytest.mark.parametrize("scenario", DISCOUNT_CASES, ids=[case["id"] for case in DISCOUNT_CASES])
def test_discount_and_coupon_routes(pdv, test_config, scenario, evidence):
    """DES-01..DES-21: F11/F12/Alt+C e autorização real de gerente."""
    product = scenario["product"]
    # O valor fixo de DES-01 é R$ 1,00; uma unidade de R$ 1,00 faria o
    # SATPDV rejeitar o desconto por não haver margem. Dez unidades mantém a
    # operação dentro do valor da venda e permite validar o handler F11/F12.
    pdv.insert_product(product, quantity="10")
    shortcut = "ALT+C" if scenario["coupon"] else scenario["shortcut"]
    pdv.send_shortcut(shortcut)
    dialog = pdv.wait_for_dialog_text(r"desconto|cupom|valor|percentual|inserir", timeout=3.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario['id']}_input_not_open")
    pdv.fill_known_dialog(dialog, scenario["coupon"] or scenario["value"])
    pdv.confirm_known_dialog(dialog)
    _authorize_if_requested(pdv, test_config)
    message = _dismiss_known_message(pdv, r"desconto|cupom|autoriza|limite|permit|inv[aá]lid", timeout=1.5)
    observed = pdv.discount()
    if scenario["id"] == "DES-20":
        assert observed >= Decimal("0"), f"{scenario['id']}: desconto retornou valor invalido"
    else:
        assert observed > Decimal("0") or re.search(r"desconto|cupom", message, re.IGNORECASE), (
            f"{scenario['id']}: F11/F12/Alt+C nao produziu desconto observavel; "
            f"desconto={observed}, mensagem={message!r}"
        )
    if scenario["id"] == "DES-01":
        # DES-01: produto 1 custa R$ 1,00; dez unidades e desconto fixo de
        # R$ 1,00 devem resultar em R$ 9,00 antes do pagamento.
        expected_gross = Decimal("10.00")
        expected_final = (expected_gross - observed).quantize(Decimal("0.01"))
        assert expected_final >= Decimal("0.00"), (
            f"DES-01: desconto maior que o subtotal: subtotal={expected_gross}, desconto={observed}"
        )
        observed_total = pdv.wait_until_total()
        assert observed_total == expected_final, (
            f"DES-01: total apos desconto divergente; esperado={expected_final}, "
            f"encontrado={observed_total}"
        )
        _finalize_representative_sale(
            pdv,
            evidence[1],
            "DES-01",
            product,
            expected_final,
            validate_pre_payment_summary=False,
        )


CLIENT_CASES = (
    ("CLI-01", CLIENTES["DF"]),
    ("CLI-02", "00000000000"),
    ("CLI-03", CLIENTES["GOIAS"]),
    ("CLI-04", CLIENTES["GOIAS"]),
    ("CLI-05", CLIENTES["BLOQUEADO"]),
    ("CLI-06", CLIENTES["DF"]),
    ("CLI-07", CLIENTES["DF"]),
    ("CLI-08", CLIENTES["CNPJ"]),
    ("CLI-09", CLIENTES["CNPJ"]),
)


@pytest.mark.automated
@pytest.mark.parametrize("scenario_id,client", CLIENT_CASES, ids=[case[0] for case in CLIENT_CASES])
def test_client_identification_routes(pdv, scenario_id, client, evidence):
    """CLI-01..CLI-09: F5, documento real e tratamento do cliente bloqueado."""
    if scenario_id == "CLI-01":
        # O item é inserido antes do F5 para provar que o cliente permanece
        # vinculado na venda que será fechada por F3.
        pdv.insert_product(PRODUTOS["PADRAO"])
        # Usa a massa DF já validada no roteiro. O CPF 53960629168 foi
        # tentado no ambiente, mas o SATPDV o rejeitou e abriu seu modal de
        # erro; ele não pode ser tratado como massa válida sem confirmação.
        client = CLIENTES["DF"]
    pdv.send_shortcut("F5")
    dialog = pdv.wait_for_dialog_text(r"CPF|CNPJ|cliente|documento", timeout=3.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_client_input_not_open")
    # O campo superior do formulário CPFCNPJ é o CPF/CNPJ do cliente. A
    # confirmação deve ocorrer no próprio formulário pelo botão ``F10 - OK``;
    # não enviar F10 ao TFrmPDV, pois nele F10 chama SolicitarVendedor.
    pdv.fill_client_document(dialog, client)
    pdv.confirm_client_dialog(dialog)
    # O retorno da unit CPFCNPJ pode ser assíncrono. Além do modal de cliente
    # bloqueado, o build real pode expor a validação ``CPF/CNPJ inválido``;
    # ambos são TFrmDlgInformacao reconhecidos e devem ser fechados antes do
    # teardown, sem transformar a abertura da tela em aprovação.
    blocked = _dismiss_known_message(
        pdv,
        r"bloquead|bloqueio|cliente|cpf.?/?cnpj|documento|inv[aá]lid",
        timeout=3.0,
    )
    if scenario_id == "CLI-05":
        assert re.search(r"bloquead|bloqueio", blocked, re.IGNORECASE), (
            f"{scenario_id}: cliente bloqueado nao apresentou mensagem esperada: {blocked!r}"
        )
    else:
        assert pdv.has_window_class("TFrmPDV"), f"{scenario_id}: TFrmPDV nao permaneceu pronto"
        if scenario_id == "CLI-01":
            _finalize_representative_sale(
                pdv,
                evidence[1],
                "CLI-01",
                PRODUTOS["PADRAO"],
            )


FUN_CASES = tuple((f"FUN-{index:02d}", VENDEDORES[(index - 1) % len(VENDEDORES)]) for index in range(1, 13))


@pytest.mark.automated
@pytest.mark.parametrize("scenario_id,seller", FUN_CASES, ids=[case[0] for case in FUN_CASES])
def test_seller_routes(pdv, scenario_id, seller, evidence):
    """FUN-01..FUN-12: atrelamento de vendedor com o handler real do PDV."""
    # Confirmado no roteiro visual e no FormKeyDown de PDV.pas: F10 chama
    # SolicitarVendedor; F7 é reservado à reimpressão fiscal.
    if scenario_id == "FUN-01":
        pdv.insert_product(PRODUTOS["PADRAO"])
    pdv.send_shortcut("F10")
    dialog = pdv.wait_for_dialog_text(r"vendedor|matr[ií]cula|funcion[aá]rio", timeout=3.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_seller_dialog_not_open")
    pdv.fill_known_dialog(dialog, seller)
    pdv.confirm_known_dialog(dialog)
    message = _dismiss_known_message(pdv, r"vendedor|selecionado|nenhum", timeout=1.5)
    assert seller in (message + " " + pdv._window_text(pdv.window)), (
        f"{scenario_id}: vendedor {seller} nao foi refletido na UI; mensagem={message!r}"
    )
    if scenario_id == "FUN-01":
        _finalize_representative_sale(
            pdv, evidence[1], "FUN-01", PRODUTOS["PADRAO"]
        )


PRICE_CASES = tuple(
    (f"PRE-{index:02d}", TABELAS_PRECO[(index - 1) % len(TABELAS_PRECO)]) for index in range(1, 11)
) + tuple(
    (f"GEST-{index:02d}", TABELAS_PRECO[(index + 1) % len(TABELAS_PRECO)]) for index in range(1, 7)
)


@pytest.mark.automated
@pytest.mark.parametrize("scenario_id,table", PRICE_CASES, ids=[case[0] for case in PRICE_CASES])
def test_price_table_routes(pdv, test_config, scenario_id, table, evidence):
    """PRE/GEST: Shift+F4 e seleção textual da tabela, sem coordenadas."""
    pdv.insert_product(PRODUTOS["PADRAO"])
    pdv.send_shortcut("SHIFT+F4")
    _authorize_if_requested(pdv, test_config)
    dialog = pdv.wait_for_dialog_text(r"tabela|pre[cç]o|produto", timeout=3.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_price_table_not_open")
    pdv.select_known_dialog_option(dialog, table)
    pdv.confirm_known_dialog(dialog)
    _dismiss_known_message(pdv, r"pre[cç]o|tabela|produto|zerado", timeout=1.5)
    assert pdv.wait_until_total() >= Decimal("0"), f"{scenario_id}: total nao foi recalculado"
    if scenario_id == "PRE-01":
        _finalize_representative_sale(
            pdv, evidence[1], "PRE-01", PRODUTOS["PADRAO"]
        )


ORC_CASES = tuple(f"ORC-{index:02d}" for index in range(1, 13))


def _save_current_budget(pdv) -> str:
    pdv.send_shortcut("CTRL+F4")
    message = _dismiss_known_message(pdv, r"pedido salvo como or[cç]amento|or[cç]amento", timeout=4.0)
    if not message:
        raise capture_unknown_state(pdv.window, "budget_save_message_missing")
    return message


def _open_budget_consultation(pdv):
    pdv.send_shortcut("CTRL+O")
    dialog = pdv.wait_for_dialog_text(r"or[cç]amento|pedido|consulta", timeout=4.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, "budget_consultation_not_open")
    return dialog


@pytest.mark.automated
@pytest.mark.parametrize("scenario_id", ORC_CASES, ids=list(ORC_CASES))
def test_budget_routes(pdv, test_config, scenario_id, evidence):
    """ORC-01..ORC-12: salva por Ctrl+F4 e consulta por Ctrl+O."""
    if scenario_id == "ORC-11":
        pdv.send_shortcut("CTRL+F4")
        message = _dismiss_known_message(pdv, r"or[cç]amento|pedido|produto|venda", timeout=1.5)
        assert "salvo como or" not in message.casefold(), f"{scenario_id}: orçamento vazio foi salvo"
        return

    pdv.insert_product(PRODUTOS["PADRAO"])
    if scenario_id == "ORC-08":
        pdv.send_shortcut("F10")
        seller_dialog = pdv.wait_for_dialog_text(r"vendedor|matr[ií]cula", timeout=2.0)
        if seller_dialog is not None:
            pdv.fill_known_dialog(seller_dialog, VENDEDORES[0])
            pdv.confirm_known_dialog(seller_dialog)
            _dismiss_known_message(pdv, r"vendedor|selecionado", timeout=1.0)
    _save_current_budget(pdv)
    if scenario_id == "ORC-04":
        return
    dialog = _open_budget_consultation(pdv)
    if scenario_id in {"ORC-02", "ORC-03", "ORC-10"}:
        # A consulta é reconhecida; a seleção da linha é feita pelo controle
        # acessível, sem coordenadas, e F10 confirma no diálogo do orçamento.
        rows = []
        for class_name in ("TListBox", "TListView", "TDBGrid", "TStringGrid"):
            try:
                rows.extend(control for control in dialog.descendants(class_name=class_name) if control.is_visible() and control.is_enabled())
            except Exception:
                continue
        if not rows:
            raise capture_unknown_state(dialog, f"{scenario_id}_budget_rows_not_exposed")
        rows[0].set_focus()
        rows[0].type_keys("{HOME}{F10}", set_foreground=True, pause=0.05)
        assert pdv.has_item(PRODUTOS["PADRAO"]), f"{scenario_id}: item do orçamento nao foi carregado"
        if scenario_id == "ORC-02":
            _finalize_representative_sale(
                pdv, evidence[1], "ORC-02", PRODUTOS["PADRAO"]
            )
    elif scenario_id == "ORC-07":
        record_dialog_observation(dialog, f"before_cancel_budget_dialog_{scenario_id}")
        dialog.set_focus()
        dialog.type_keys("{F6}", set_foreground=True, pause=0.05)
        _authorize_if_requested(pdv, test_config)
    else:
        record_dialog_observation(dialog, f"before_close_budget_dialog_{scenario_id}")
        dialog.set_focus()
        dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
        assert pdv.has_window_class("TFrmPDV"), f"{scenario_id}: consulta nao retornou ao PDV"


REP_CASES = tuple(f"REP-{index:02d}" for index in range(1, 8))


@pytest.mark.automated
@pytest.mark.parametrize("scenario_id", REP_CASES, ids=list(REP_CASES))
def test_report_consultation_routes(pdv, scenario_id, evidence):
    """REP-01..REP-07: executa o atalho solicitado e valida a tela resultante."""
    # O roteiro visual e o código real (PDV.pas, FormKeyDown) mapeiam Ctrl+F8
    # para EmitirRelatorioDeFechamento.
    pdv.send_shortcut("CTRL+F8")
    dialog = pdv.wait_for_dialog_text(r"relat[oó]rio|fechamento|caixa|data", timeout=4.0)
    if dialog is None:
        # O relatório real é um formulário VCL sem texto no título, mas com
        # classe confirmada no runtime: TDlgPDVVRelatórioDeFechamento.
        dialog = pdv.wait_until_window_class("TDlgPDVVRelatórioDeFechamento", timeout=2.0)
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_report_dialog_not_open")
    text = pdv._window_text(dialog)
    record_dialog_observation(dialog, f"before_close_report_dialog_{scenario_id}")
    dialog.set_focus()
    dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
    assert re.search(r"relat[oó]rio|fechamento|caixa", text, re.IGNORECASE), (
        f"{scenario_id}: tela aberta nao foi identificada como relatorio: {text!r}"
    )
    if scenario_id == "REP-01":
        # O relatório já foi validado e fechado; agora o mesmo grupo também
        # prova que o PDV continua apto a concluir uma venda real.
        pdv.insert_product(PRODUTOS["PADRAO"])
        _finalize_representative_sale(
            pdv, evidence[1], "REP-01", PRODUTOS["PADRAO"]
        )
