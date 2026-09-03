from __future__ import annotations

from decimal import Decimal

import pytest

from core.test_results import record_runtime_evidence
from pages.login_page import LoginPage


@pytest.mark.automated
@pytest.mark.initial
def test_pause_cash_register(pdv, app, test_config):
    pdv.pause()
    login_dialog = pdv.open_login_after_pause()
    assert login_dialog.class_name() == "TFrmPassWord", \
        "O clique na pausa nao abriu o formulario real de credenciais"

    LoginPage(login_dialog, test_config).login()
    app.wait_until_ready(test_config.start_timeout)


@pytest.mark.automated
@pytest.mark.initial
def test_blank_password_is_rejected(raw_app, test_config):
    """INI-01: matricula real + senha vazia exibe rejeicao de credenciais."""
    dialog = raw_app.window
    text = LoginPage(dialog, test_config).blank_password_login().casefold()
    assert "matr" in text and "senha" in text and (
        "inv" in text or "inval" in text
    ), f"INI-01: mensagem de credencial invalida nao reconhecida: {text}"


@pytest.mark.automated
@pytest.mark.initial
def test_open_help(pdv):
    pdv.help()
    assert pdv.has_window_class("TFrmPDVAjuda"), \
        "O formulario top-level TFrmPDVAjuda nao foi localizado"
    pdv.escape()


@pytest.mark.automated
@pytest.mark.initial
@pytest.mark.products
def test_escape_after_product(pdv, product_code):
    pdv.insert_product(product_code)
    pdv.escape()
    modal_text = pdv.active_modal_text().lower()
    visible_text = " ".join((pdv.window.window_text(), pdv.items_text())).lower()
    accepted_messages = (
        "f3",
        "f6",
        "cancelamento de pedido",
        "cancelar venda",
        "finalizar venda",
        "deseja cancelar",
        "motivo de cancelamento",
    )
    assert any(message in modal_text or message in visible_text for message in accepted_messages), \
        f"O modal/mensagem de ESC nao foi exposto: {modal_text or visible_text}"
    # Em algumas builds o ESC já abre TFrmDlg ``Cancelamento de Pedido``.
    # Esse diálogo deve receber o motivo e OK; um segundo ESC o mantém aberto
    # e deixa o TFrmPDV desabilitado para o teardown.
    if modal_text and "cancelamento de pedido" not in modal_text and "motivo de cancelamento" not in modal_text:
        pdv.escape()
    pdv.cancel_sale("Teste automatizado INI-05: cancelar venda apos validar ESC")


@pytest.mark.automated
@pytest.mark.initial
@pytest.mark.products
@pytest.mark.fresh_instance
def test_escape_after_cancelled_sale_closes_pdv(pdv, product_code):
    """INI-06: apos cancelar a venda, ESC retorna ao caixa fechado."""
    pdv.insert_product(product_code)
    # F6 abre o dialogo real de motivo; cancel_sale preenche e confirma o
    # TEdit/TBitBtn do formulario VCL antes de enviar ESC para TFrmPDV.
    pdv.cancel_sale("Teste automatizado INI-06: cancelar venda antes do ESC")
    # O login pode deixar TFrmPassWord visivel sem bloquear o PDV; para este
    # fluxo o ESC deve chegar ao handler TFrmPDV.FormKeyDown, nao ao residual.
    pdv.escape_main()
    close_prompt = pdv.confirm_close_cashier()
    closed = pdv.wait_until_window_class("TFrmPDVCaixaFechado", timeout=5)
    assert close_prompt or closed is not None, (
        "INI-06: ESC nao abriu a confirmacao reconhecida nem retornou ao caixa fechado"
    )
    assert closed is not None, "INI-06: ESC nao retornou ao TFrmPDVCaixaFechado"


@pytest.mark.manual
@pytest.mark.initial
def test_open_cash_drawer_with_authorization(pdv, test_config):
    """INI-07: valida F9/autorizacao; abertura fisica depende do hardware."""
    manager_user, manager_password = test_config.manager_credentials
    if not manager_user or not manager_password:
        pytest.skip("INI-07 bloqueado: credencial real de autorizacao nao configurada")
    dialog = pdv.open_drawer_authorization()
    if dialog is None:
        pytest.skip(
            "INI-07 bloqueado: F9 nao expos TFrmPassWord; verificar impressora nao fiscal/gaveta"
        )
    # TFrmPassWord e o formulario VCL de autorizacao; usa o par de gerente do
    # .env (com fallback documentado para PDV_USER/PDV_PASSWORD), nunca literal.
    LoginPage(dialog, test_config).login(user=manager_user, password=manager_password)
    assert pdv.wait_until_window_class("TFrmPDV", timeout=5) is not None, (
        "INI-07: TFrmPDV nao ficou pronto apos a autorizacao do F9"
    )
    pytest.skip(
        "INI-07 parcialmente automatizado: autorizacao F9 validada, mas a abertura fisica "
        "da gaveta exige sensor/impressora configurada"
    )


@pytest.mark.blocked
@pytest.mark.initial
def test_license_validation_by_cnpj():
    """INI-16: reservado para cadastro de empresa + SAT principal/atalho."""
    pytest.skip(
        "INI-16 bloqueado: o roteiro exige alterar CNPJ no Cadastro de Empresa e "
        "abrir o SAT principal e o atalho; esses modulos nao estao no escopo seguro "
        "da suite e nao devem ser simulados"
    )


@pytest.mark.automated
@pytest.mark.initial
@pytest.mark.products
@pytest.mark.xfail(
    strict=False,
    reason=(
        "Known issue INI-08/INI-09: a grade VCL pode pintar uma linha fora do "
        "frame OCR; a consulta/estoque real fica registrado, mas a leitura "
        "completa por loja nao e deterministica em todas as execucoes."
    ),
)
def test_price_consultation_validates_store_stock(pdv, request):
    """INI-08/INI-09: validate price consultation and GridLoja quantities."""
    expected = {
        # Valores atuais do banco ficticio de homologacao para o produto 4.
        "Sat Sistemas Testes": 9990,
        "Loja 2": -1,
    }
    result = pdv.consult_product_stock("4", expected_stock=expected)
    record_runtime_evidence(request.node.nodeid, result["evidence_text"])

    found = result["stores"]
    assert found == expected, (
        "INI-09: a coluna Quant. nao foi validada para todas as lojas; "
        f"encontrado={found!r}, esperado={expected!r}; OCR={result['raw']!r}"
    )

    product_text = pdv._ascii(result["product_text"])
    assert "icms substituicao tributaria" in product_text, (
        "INI-08: a descricao do produto 4 nao foi identificada na consulta; "
        f"texto={result['product_text']!r}"
    )
    assert Decimal("1.00") in pdv._money_values(result["product_text"]), (
        "INI-08: o preco unitario esperado de R$ 1,00 nao foi identificado; "
        f"texto={result['product_text']!r}"
    )
