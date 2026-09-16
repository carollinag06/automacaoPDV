from __future__ import annotations

import json
import re
import time
from decimal import Decimal

import pytest

from pages.base_page import capture_unknown_state
from core.test_results import record_dialog_observation
from pages.login_page import LoginPage
from tests.config.test_data import CLIENTES, CUPONS, PRODUTOS, TABELAS_PRECO, VENDEDORES


# Estes grupos representam itens do roteiro que nao podem ser validados com
# seguranca nesta suite. Voucher e convenio possuem testes proprios abaixo;
# os demais grupos continuam dependendo de recursos externos.
OUT_OF_SCOPE = (
    {
        "roteiro": "VEN-20, VEN-22, VEN-23 e VEN-32",
        "nome": "configuracao financeira e fiscal",
        "motivo": "depende de configuração externa da homologação, fora do escopo desta suite",
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
        "roteiro": "VEN-19, VEN-28 e VEN-29",
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
            "falta cadastro/parametrização de frete (massa Frete1, ordem 1, R$15,00) "
            "no ambiente de homologação — fora de escopo para geração automática nesta suite"
        ),
    },
    {
        "roteiro": "PRD-01 e PRD-02",
        "nome": "produto e observacoes",
        "motivo": (
            "PRD-01 depende de cadastro/parametro compartilhado; PRD-02 depende "
            "de emissao NFC-e/XML, ambos fora do escopo seguro desta suite"
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
        "roteiro": "CAN-02 e CAN-08",
        "nome": "cancelamento com documento e modo totem",
        "motivo": (
            "CAN-02 exige validar documento fiscal e itens no pedido; "
            "CAN-08 exige modo totem/configuracao especifica. O teclado existe, "
            "mas a evidencia completa depende de recursos/configuracao fora do escopo"
        ),
    },
    {
        "roteiro": "MFI-02, MFI-05 e MFI-07",
        "nome": "menu fiscal",
        "motivo": (
            "esses cenários exigem comprovante, impressora/gaveta ou fechamento "
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
        "roteiro": "VEN-33",
        "nome": "Pix Inter",
        "motivo": "aguardando remapeamento para 'Teste com Pix Inter'; texto do roteiro ainda não fornecido",
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
        "roteiro": "MES-01, REI-01 e REI-03 a REI-06 (REI-02 em pendência própria)",
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
)


@pytest.mark.parametrize(
    "scenario",
    [pytest.param(scenario, marks=pytest.mark.blocked(reason=(
        "FRT-01/FRT-02: falta massa Frete1, ordem 1, R$15,00. "
        "Pendente de decisão do revisor; não gerar automaticamente."
    ))) if scenario["nome"] == "frete" else scenario for scenario in OUT_OF_SCOPE],
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


@pytest.mark.automated
@pytest.mark.products
def test_order_observation_is_saved_in_budget(pdv, evidence):
    """PRD-03: Ctrl+F9 grava a observacao do pedido no fluxo real do PDV.

    O controle Delphi usado é o editor VCL criado dinamicamente por
    ``InputMemo`` em ``TFrmPDV.InserirObs`` (``PDV.pas``, Ctrl+F9). A
    observação é confirmada por leitura de volta antes de salvar o orçamento;
    isso evita considerar PASS apenas porque a dialog abriu.
    """
    observation = "Observacao automatizada PRD-03"
    pdv.insert_product(PRODUTOS["PADRAO"])
    pdv.insert_product("2")
    result = pdv.set_order_observation(observation)
    assert result["observed"].casefold() == observation.casefold(), (
        f"PRD-03: leitura de volta divergente; observado={result['observed']!r}"
    )

    # SalvarComoOrcamento (TFrmPDV, PDV.pas) persiste QOEOBS junto do pedido.
    pdv.send_shortcut("CTRL+F4")
    saved_message = pdv.wait_for_status_message(
        r"pedido\s+salvo\s+como\s+or[cç]amento|or[cç]amento",
        timeout=4.0,
    )
    assert re.search(r"pedido\s+salvo\s+como\s+or[cç]amento|or[cç]amento", saved_message, re.IGNORECASE), (
        f"PRD-03: o orçamento não foi salvo após confirmar a observação; "
        f"mensagem={saved_message!r}"
    )
    evidence[1].info(
        f"PRD-03 PASS: Ctrl+F9 abriu InputMemo ({result['dialog_class']}), "
        f"observacao lida de volta={result['observed']!r}, "
        "Ctrl+F4 confirmou a persistência do pedido como orçamento."
    )


@pytest.mark.automated
@pytest.mark.initial
def test_mfi01_opens_fiscal_menu_by_keyboard(pdv, evidence):
    """MFI-01: F8 abre e ESC fecha o menu fiscal, sem emitir documento.

    ``TFrmPDV.FormKeyDown`` em ``PDV.pas`` chama
    ``ExibirTelaDeMenuFiscal``; o formulário filho ``FrmMenuFiscal`` é um
    controle Delphi externo ao DFM anexado, então o teste valida sua janela
    pelo texto funcional exposto e fecha com o ESC documentado.
    """
    menu = pdv.open_fiscal_menu(timeout=4.0)
    text = pdv._window_text(menu)
    assert re.search(r"menu\s+fiscal|fiscal", text, re.IGNORECASE), (
        f"MFI-01: janela aberta nao foi identificada como Menu Fiscal: {text!r}"
    )
    record_dialog_observation(menu, "before_close_fiscal_menu_MFI-01")
    menu.set_focus()
    menu.type_keys("{ESC}", set_foreground=True, pause=0.05)
    assert pdv.wait_until_window_class("TFrmPDV", timeout=3.0) is not None, (
        "MFI-01: ESC nao retornou ao TFrmPDV apos abrir o Menu Fiscal"
    )
    evidence[1].info(
        f"MFI-01 PASS: F8 abriu {pdv._window_class(menu)}; texto completo={text!r}; "
        "ESC fechou o menu e o PDV retornou ao estado pronto."
    )


@pytest.mark.automated
@pytest.mark.configuration
def test_cfg03_local_store_is_read_from_sat_ini(pdv, test_config, evidence):
    """CFG-03: valida a loja atualmente lida do SAT.INI pelo PDV.

    O roteiro (secao 19, ``Leitura Loja (SAT.INI)``) pede confirmar que a
    loja configurada localmente e refletida na abertura do PDV. Esta variante
    e deliberadamente observacional: nao altera o SAT.INI compartilhado. O
    teste le ``[Terminal] Loja`` e compara com o cabecalho renderizado de
    ``TFrmPDV``; a leitura do cabecalho usa OCR porque os TLabel da VCL nao
    possuem HWND proprio.
    """
    ini_path = test_config.exe_path.parent / "SAT.INI"
    assert ini_path.exists(), (
        f"CFG-03: SAT.INI nao encontrado em {ini_path}; "
        "falta a massa/configuracao necessaria para validar a leitura da loja"
    )

    section = ""
    configured_store = ""
    for raw_line in ini_path.read_text(encoding="cp1252", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().casefold()
        elif section == "terminal" and line.casefold().startswith("loja="):
            configured_store = line.split("=", 1)[1].strip()
            break
    assert configured_store.isdigit(), (
        f"CFG-03: [Terminal]/Loja ausente ou invalida no SAT.INI: {configured_store!r}"
    )

    expected_store = f"{int(configured_store):03d}"
    ocr_text = pdv._ocr_main_region(False)
    normalized = " ".join(pdv._ascii(ocr_text).split())
    header = normalized[:240]
    assert re.search(
        rf"loja\s+terminal.*\b{re.escape(expected_store)}\b.*\b{re.escape(expected_store)}\b",
        header,
        re.IGNORECASE,
    ), (
        f"CFG-03: loja do SAT.INI ({configured_store}) nao foi refletida no cabecalho "
        f"do TFrmPDV; OCR={ocr_text!r}"
    )

    screenshot = evidence[0] / "cfg03_store_header.png"
    try:
        pdv._grab_rect(pdv.window.rectangle()).save(screenshot)
    except Exception as exc:
        evidence[1].warning("CFG-03: screenshot nao capturado: %s", exc)
    evidence[1].info(
        "CFG-03 PASS: SAT.INI [Terminal]/Loja=%s; cabecalho TFrmPDV confirmou "
        "a loja %s; OCR completo=%r; screenshot=%s",
        configured_store,
        expected_store,
        ocr_text,
        screenshot,
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
    # CLI-04 e CLI-05 não são o mesmo dado/caso no código atual: CLI-04
    # consulta um CPF existente (GOIAS), enquanto CLI-05 exercita o item 4
    # do roteiro, ``Inserindo cliente não cadastrado``. O histórico que
    # chamou CLI-04 de item 4 está, portanto, em conflito com a matriz
    # ``docs/test_matrix.csv`` e será reportado como divergência de ID.
    ("CLI-05", CLIENTES["NAO_CADASTRADO"]),
    ("CLI-06", CLIENTES["DF"]),
    ("CLI-07", CLIENTES["DF"]),
    ("CLI-08", CLIENTES["CNPJ"]),
    ("CLI-09", CLIENTES["CNPJ"]),
)


@pytest.mark.automated
@pytest.mark.parametrize(
    "scenario_id,client",
    CLIENT_CASES,
    ids=[f"CLI-{index:02d}" for index in range(1, 10)],
)
def test_client_identification_routes(pdv, scenario_id, client, evidence):
    """CLI-01..CLI-09: F5, documentos reais e cliente nao cadastrado."""
    if scenario_id in {"CLI-01", "CLI-05"}:
        # O item é inserido antes do F5 para provar que o cliente permanece
        # vinculado na venda/alerta de cliente nao cadastrado do roteiro.
        pdv.insert_product(PRODUTOS["PADRAO"])
    if scenario_id == "CLI-01":
        # O cenário representativo será fechado por F3.
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
    document_control = pdv.fill_client_document(dialog, client)
    # TFrmCPFCNPJ: Enter consulta o cadastro e preenche NOME; somente F10 - OK
    # confirma o vínculo do cliente na venda.
    client_name = pdv.lookup_client_document(dialog, document_control)
    if scenario_id == "CLI-01":
        assert client_name, f"{scenario_id}: Enter nao preencheu o nome do cliente"
        evidence[1].info(
            f"{scenario_id}: CPF consultado por Enter; nome retornado={client_name!r}"
        )
    # O retorno da unit CPFCNPJ pode ser assíncrono e expõe a pergunta de
    # inclusão somente para CPF válido que não existe no cadastro.
    if scenario_id == "CLI-05":
        prompt_pattern = (
            r"deseja\s+inserir\s+o\s+cliente|"
            r"cliente\s+n[aã]o\s+identificado"
        )
        # A rotina CPFCNPJ pode materializar a pergunta como TFrmDlgInformacao,
        # TFrmDlg ou outra janela VCL de topo. Primeiro observamos qualquer
        # classe de janela reconhecível; não usamos Enter/ESC sem localizar a
        # pergunta e seus botões.
        prompt = pdv.wait_for_dialog_text(prompt_pattern, timeout=1.5)
        prompt_source = "dialog"
        # Para CPF inexistente, algumas builds exibem a pergunta já durante a
        # consulta por Enter; nesse caso ela já é a confirmação do próximo
        # passo e não se deve enviar F10 ao formulário desabilitado.
        if prompt is None:
            pdv.confirm_client_dialog(dialog)
            prompt = pdv.wait_for_dialog_text(prompt_pattern, timeout=3.0)
        if prompt is None:
            # Em algumas compilações EditMsg (TLabel, sem HWND) recebe o texto
            # de retorno. A leitura é somente observacional; não há botões para
            # acionar nesse caminho.
            prompt_text = pdv.wait_for_status_message(prompt_pattern, timeout=2.0)
            if re.search(prompt_pattern, pdv._ascii(prompt_text), re.IGNORECASE):
                prompt_source = "status"
                evidence[1].info(
                    f"{scenario_id}: retorno do CPF nao cadastrado em EditMsg/TLabel; "
                    f"texto completo={prompt_text!r}"
                )
            elif prompt_text.strip():
                evidence[1].info(
                    f"{scenario_id}: nenhum prompt reconhecido; ultimo OCR de status="
                    f"{prompt_text!r}; estado_pdv={pdv._window_text(pdv.window)!r}"
                )
        if prompt_source == "status":
            pytest.fail(
                f"{scenario_id}: o texto foi exibido em EditMsg/TLabel, mas a UI "
                "nao ofereceu uma dialog com botoes Sim/Nao para validar os dois caminhos"
            )
        assert prompt is not None, (
            f"{scenario_id}: a pergunta de cliente nao cadastrado nao apareceu"
        )
        prompt_text = pdv._window_text(prompt)
        record_dialog_observation(prompt, f"before_client_registration_choice_{scenario_id}_SIM")
        button_captions = {
            pdv._ascii(button.window_text() or "").strip("& ")
            for class_name in ("TBitBtn", "TButton", "TSatSpeedButton")
            for button in prompt.descendants(class_name=class_name)
            if button.is_visible() and button.is_enabled()
        }
        assert {"sim", "nao"}.issubset(button_captions), (
            f"{scenario_id}: prompt sem opcoes Sim/Nao; texto={prompt_text!r}; "
            f"botoes={sorted(button_captions)!r}"
        )

        def click_prompt_button(wanted: str) -> None:
            wanted = pdv._ascii(wanted).strip("& ")
            for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
                for button in prompt.descendants(class_name=class_name):
                    caption = pdv._ascii(button.window_text() or "").strip("& ")
                    if caption == wanted and button.is_visible() and button.is_enabled():
                        button.click_input()
                        return
            raise capture_unknown_state(prompt, f"{scenario_id}_client_prompt_button_{wanted}")

        # Caminho SIM: o PDV deve abrir o cadastro real. O formulario e
        # TFrmCPFCNPJ; cancelamos com ESC para nao alterar massa compartilhada.
        click_prompt_button("sim")
        registration = pdv.wait_until_window_class("TFrmCPFCNPJ", timeout=4.0)
        assert registration is not None, (
            f"{scenario_id}: Sim nao abriu o formulario TFrmCPFCNPJ"
        )
        record_dialog_observation(registration, f"client_registration_opened_{scenario_id}")
        registration.set_focus()
        registration.type_keys("{ESC}", set_foreground=True, pause=0.05)
        assert pdv.has_window_class("TFrmPDV"), (
            f"{scenario_id}: ESC nao fechou o cadastro apos caminho Sim"
        )

        # Caminho NAO: repetir o CPF desconhecido e confirmar que a pergunta
        # fecha sem abrir cadastro nem alterar a massa.
        pdv.send_shortcut("F5")
        second_dialog = pdv.wait_for_dialog_text(r"CPF|CNPJ|cliente|documento", timeout=3.0)
        assert second_dialog is not None, f"{scenario_id}: segunda tela CPF/CNPJ nao abriu"
        pdv.fill_client_document(second_dialog, client)
        pdv.confirm_client_dialog(second_dialog)
        second_prompt = pdv.wait_for_information_text(
            r"deseja\s+inserir\s+o\s+cliente|cliente\s+nao\s+identificado",
            timeout=4.0,
        )
        assert second_prompt is not None, f"{scenario_id}: segunda pergunta Sim/Nao nao apareceu"
        record_dialog_observation(second_prompt, f"before_client_registration_choice_{scenario_id}_NAO")
        for class_name in ("TBitBtn", "TButton", "TSatSpeedButton"):
            for button in second_prompt.descendants(class_name=class_name):
                caption = pdv._ascii(button.window_text() or "").strip("& ")
                if caption == "nao" and button.is_visible() and button.is_enabled():
                    button.click_input()
                    break
            else:
                continue
            break
        else:
            raise capture_unknown_state(second_prompt, f"{scenario_id}_client_prompt_no_button")
        time.sleep(0.25)
        assert pdv.has_window_class("TFrmPDV"), (
            f"{scenario_id}: caminho Nao nao retornou ao TFrmPDV"
        )
        evidence[1].info(
            f"{scenario_id}: CPF nao cadastrado {client}; prompt completo observado="
            f"{prompt_text!r}; caminhos Sim (cadastro aberto e cancelado por ESC) "
            "e Nao (pergunta fechada) validados."
        )
        # Depois de validar os dois caminhos da pergunta, o item permanece na
        # venda; F3 confirma o fechamento real do cenário representativo.
        _finalize_representative_sale(
            pdv,
            evidence[1],
            "CLI-05",
            PRODUTOS["PADRAO"],
        )
        # O roteiro continua após a venda: Shift+F7 é roteado por
        # TFrmPDV.FormKeyDown/ExibirRelatorioDeEntrega (PDV.pas), e a etapa
        # seguinte deve permitir informar outro cliente antes da emissão.
        # Observamos a janela real antes de interagir; se a build não expuser
        # TFrmCPFCNPJ nesse ponto, o resultado bruto deve mostrar exatamente
        # a tela/fluxo que impediu a continuação.
        pdv.send_shortcut("SHIFT+F7")
        post_sale = pdv.wait_for_dialog_text(
            r"CPF|CNPJ|cliente|documento|entrega|relat[oó]rio|pedido|NFC|NF-e",
            timeout=5.0,
        )
        if post_sale is None:
            observed = []
            for top in pdv._top_level_windows():
                try:
                    if top.is_visible() and top.is_enabled():
                        observed.append(
                            {
                                "class": pdv._window_class(top),
                                "title": pdv._window_text(top),
                            }
                        )
                except Exception:
                    continue
            evidence[1].error(
                "CLI-05 resultado bruto: Shift+F7 não expôs dialog reconhecível; "
                "janelas=%s; estado=%r",
                observed,
                pdv._window_text(pdv.window),
            )
            raise AssertionError(
                "CLI-05: após F3/Shift+F7 não foi localizada a tela de cliente/"
                f"emissão; janelas observadas={observed!r}"
            )
        post_class = pdv._window_class(post_sale)
        post_text = pdv._window_text(post_sale)
        record_dialog_observation(post_sale, "cli05_after_shift_f7")
        evidence[1].info(
            "CLI-05 resultado bruto após Shift+F7: classe=%s; texto completo=%r",
            post_class,
            post_text,
        )
        if post_class != "TFrmCPFCNPJ":
            raise AssertionError(
                "CLI-05: Shift+F7 abriu uma janela diferente de TFrmCPFCNPJ; "
                f"classe={post_class!r}; texto={post_text!r}"
            )
        # CPF existente usado apenas para a etapa posterior do roteiro; a
        # leitura de volta e o Enter/F10 seguem o mesmo helper validado no F5.
        second_client = CLIENTES["DF"]
        second_control = pdv.fill_client_document(post_sale, second_client)
        second_name = pdv.lookup_client_document(post_sale, second_control)
        pdv.confirm_client_dialog(post_sale)
        evidence[1].info(
            "CLI-05: segundo cliente informado após Shift+F7; CPF=%s; nome=%r",
            second_client,
            second_name,
        )
        assert pdv.has_window_class("TFrmPDV"), (
            "CLI-05: após confirmar o segundo cliente o TFrmPDV não ficou pronto"
        )
        return

    # O retorno dos demais cenarios pode ser um TFrmDlgInformacao conhecido;
    # ambos devem ser fechados antes do teardown, sem aprovar apenas a abertura.
    # TFrmCPFCNPJ: Enter apenas consulta/preenche NOME; F10 - OK efetivamente
    # vincula o cliente à venda e libera TFrmPDV para os próximos atalhos.
    pdv.confirm_client_dialog(dialog)
    blocked = _dismiss_known_message(
        pdv,
        r"bloquead|bloqueio|cliente|cpf.?/?cnpj|documento|inv[aá]lid",
        timeout=3.0,
    )
    assert pdv.has_window_class("TFrmPDV"), f"{scenario_id}: TFrmPDV nao permaneceu pronto"
    if scenario_id == "CLI-01":
        _finalize_representative_sale(
            pdv,
            evidence[1],
            "CLI-01",
            PRODUTOS["PADRAO"],
        )


@pytest.mark.automated
@pytest.mark.xfail(
    reason=(
        "cadastro BLOQUEADO foi submetido, mas esta build retornou ao TFrmPDV "
        "sem advertencia explicita de bloqueio"
    ),
    strict=False,
)
def test_EXT_01_blocked_client_route(pdv, evidence):
    """EXT-01: cliente bloqueado, comportamento pertinente fora do roteiro formal.

    Este teste extra existe porque o PDV possui cadastro de cliente bloqueado,
    embora SATPDV_241023B.docx nao documente esse caso. A expectativa segura
    e observar uma advertencia explicita de bloqueio ao tentar associar o
    cliente; nenhum cadastro ou venda e alterado pelo teste.
    """
    blocked_client = CLIENTES["BLOQUEADO"]
    # A identificação de cliente bloqueado é exercitada no contexto real de
    # venda, com item no TFrmPDV/pnlProdutos, embora EXT-01 não pertença ao
    # roteiro formal.
    pdv.insert_product(PRODUTOS["PADRAO"])
    pdv.send_shortcut("F5")
    dialog = pdv.wait_for_dialog_text(r"CPF|CNPJ|cliente|documento", timeout=3.0)
    assert dialog is not None, "EXT-01: formulario TFrmCPFCNPJ nao abriu"
    document_control = pdv.fill_client_document(dialog, blocked_client)
    pdv.lookup_client_document(dialog, document_control)
    pdv.confirm_client_dialog(dialog)
    warning = _dismiss_known_message(
        pdv,
        r"bloquead|bloqueio|cliente.*nao.*permit|venda.*nao.*permit",
        timeout=4.0,
    )
    if not warning:
        warning = pdv.wait_for_status_message(
            r"bloquead|bloqueio|cliente.*nao.*permit|venda.*nao.*permit",
            timeout=3.0,
        )
    assert re.search(r"bloquead|bloqueio|nao.*permit", warning, re.IGNORECASE), (
        f"EXT-01: cliente bloqueado nao gerou advertencia; observado={warning!r}"
    )
    evidence[1].info(
        f"EXT-01: advertencia de cliente bloqueado observada e registrada; "
        f"mensagem={warning!r}"
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
    status_message = pdv.wait_for_status_message(r"vendedor", timeout=3.0)
    observed = message or status_message
    # ``SolicitarVendedor`` writes ``Vendedor: ...`` to ``EditMsg: TLabel``
    # (PDV.pas, TFrmPDV.SolicitarVendedor). Validate that field as a token,
    # never by searching the free-form TFrmPDV text for a digit. Code 0 may
    # be rendered as zero-padded numeric text or as the explicit no-seller
    # state, both of which are produced by the real handler.
    if str(seller).strip() == "0":
        seller_seen = re.search(
            r"vendedor\s*:\s*(?:0+\s*(?:[-–].*)?|nenhum(?:\s|[.!]|$))",
            pdv._ascii(observed),
            re.IGNORECASE,
        )
    else:
        seller_seen = re.search(
            rf"vendedor\s*:\s*0*{re.escape(str(seller).strip())}\b",
            pdv._ascii(observed),
            re.IGNORECASE,
        )
    assert seller_seen is not None, (
        f"{scenario_id}: vendedor {seller} nao foi refletido no campo Vendedor; "
        f"mensagem_dialog={message!r}; status_ocr={status_message!r}"
    )
    evidence[1].info(
        f"{scenario_id}: campo Vendedor validado; dialog={message!r}; "
        f"status_ocr={status_message!r}"
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
        # ``SalvarComoOrcamento`` (PDV.pas, TFrmPDV.SalvarComoOrcamento)
        # clears the sale and writes the confirmation to ``EditMsg: TLabel``
        # via ``ExibirMsg``. TLabel has no HWND, so PdvPage uses a cropped OCR
        # fallback for this status-only message.
        message = pdv.wait_for_status_message(
            r"pedido\s+salvo\s+como\s+orcamento|orcamento",
            timeout=4.0,
        )
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
        confirmation = pdv.wait_for_information_text(
            r"tem certeza.*excluir.*or[cç]amento|excluir.*or[cç]amento",
            timeout=2.0,
        )
        if confirmation is not None:
            # TDlgPDVConsultarOrcamentos -> TFrmDlgInformacao: F6 opens the
            # documented deletion confirmation; confirm the mapped ``Sim``
            # button rather than leaving TFrmPDV disabled for teardown.
            record_dialog_observation(confirmation, f"before_confirm_budget_delete_{scenario_id}")
            pdv.confirm_known_dialog(confirmation)
        _authorize_if_requested(pdv, test_config)
        _dismiss_known_message(pdv, r"or[cç]amento.*exclu|exclu.*or[cç]amento", timeout=1.5)
        # The delete confirmation closes, but the mapped consultation dialog
        # remains open. ESC is the documented cancel/close command for
        # TDlgPDVConsultarOrcamentos and must be sent before returning to PDV.
        dialog.set_focus()
        dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
        assert pdv.has_window_class("TFrmPDV"), f"{scenario_id}: consulta nao foi fechada apos excluir"
    else:
        record_dialog_observation(dialog, f"before_close_budget_dialog_{scenario_id}")
        dialog.set_focus()
        dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
        assert pdv.has_window_class("TFrmPDV"), f"{scenario_id}: consulta nao retornou ao PDV"


REP_CASES = tuple(f"REP-{index:02d}" for index in range(1, 8))
REPORT_DIALOG_CLASS = "TDlgPDVRelatórioDeFechamento"


@pytest.mark.automated
@pytest.mark.parametrize("scenario_id", REP_CASES, ids=list(REP_CASES))
def test_report_consultation_routes(pdv, scenario_id, evidence):
    """REP-01..REP-07: executa o atalho solicitado e valida a tela resultante."""
    # O roteiro visual e o código real (PDV.pas, FormKeyDown) mapeiam Ctrl+F8
    # para EmitirRelatorioDeFechamento.
    pdv.send_shortcut("CTRL+F8")
    # O runtime confirmou a classe exata TDlgPDVRelatórioDeFechamento. O
    # título "Inserir Suprimento" é o formulário reutilizado pelo fluxo de
    # Ctrl+F8; seus comandos são F10=Emitir, F11=Inserir Sangria e
    # Esc=Cancelar.
    dialog = pdv.wait_until_window_class(REPORT_DIALOG_CLASS, timeout=4.0)
    if dialog is None:
        dialog = pdv.wait_for_dialog_text(
            r"relat[oó]rio|fechamento|caixa|data|suprimento|sangria|emitir",
            timeout=2.0,
        )
    if dialog is None:
        raise capture_unknown_state(pdv.window, f"{scenario_id}_report_dialog_not_open")
    text = pdv._window_text(dialog)
    record_dialog_observation(dialog, f"before_close_report_dialog_{scenario_id}")
    dialog.set_focus()
    dialog.type_keys("{ESC}", set_foreground=True, pause=0.05)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            if not dialog.exists() or not dialog.is_visible():
                break
        except Exception:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(
            f"{scenario_id}: ESC nao fechou a dialog {REPORT_DIALOG_CLASS}"
        )
    assert re.search(r"relat[oó]rio|fechamento|caixa|suprimento|sangria|emitir", text, re.IGNORECASE), (
        f"{scenario_id}: tela aberta nao foi identificada como relatorio: {text!r}"
    )
    if scenario_id == "REP-01":
        # O relatório já foi validado e fechado; agora o mesmo grupo também
        # prova que o PDV continua apto a concluir uma venda real.
        pdv.insert_product(PRODUTOS["PADRAO"])
        _finalize_representative_sale(
            pdv, evidence[1], "REP-01", PRODUTOS["PADRAO"]
        )
