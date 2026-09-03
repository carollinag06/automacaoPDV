from __future__ import annotations

import csv
import contextvars
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config


_results: list[dict[str, Any]] = []
_runtime_evidence: dict[str, str] = {}
_dialog_observations: list[dict[str, Any]] = []
_dialog_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "satpdv_dialog_context", default="fora_de_teste"
)

# Classes já inventariadas em PDV.dfm/PDV.pas ou confirmadas em execuções
# anteriores. Uma classe ausente desta lista é sinalizada no relatório para
# mapeamento manual, sem receber interação automática por este mecanismo.
KNOWN_DIALOG_CLASSES = {
    "TFrmPassWord",
    "TFrmDlgInformacao",
    "TFrmDlg",
    "TFrmInserirPgto",
    "TFrmPDVProdutoNaoEncontrado",
    "TFrmPDVDlg",
    "TppPrintPreview",
    "TDlgProd",
    "TFrmPDVAjuda",
    "TFrmPDVPausa",
    "TDlgPDVRelatórioDeFechamento",
    "TDlgPDVVRelatórioDeFechamento",
}

COMMAND_COVERAGE = {
    "F2": "INI-15 e VEN-07/VEN-08",
    "F3": "finalização de venda e VEN-07/VEN-08",
    "F5": "CLI-01 a CLI-09",
    "F6": "CAN-06/INI-06",
    "F7": "reimpressão; cenários externos quando aplicável",
    "F9": "INI-07 (SKIPPED por hardware)",
    "F10": "CLI e FUN",
    "F11": "DES",
    "F12": "DES",
    "SHIFT+F4": "PRE/GEST",
    "CTRL+F4": "ORC",
    "CTRL+O": "ORC",
    "CTRL+F8": "REP",
    "ESC": "INI-06, cancelamento e fechamento de dialogs",
    "ENTER": "confirmação de dialogs mapeadas",
    "SPACE": "confirmação de dialogs VCL mapeadas",
}


def set_dialog_context(nodeid: str) -> None:
    """Define o cenário associado às observações do teste corrente."""
    _dialog_context.set(str(nodeid))


def record_dialog_observation(dialog: Any, context_label: str) -> dict[str, Any] | None:
    """Registra o texto completo de uma dialog antes de qualquer interação.

    A função é deliberadamente somente de leitura. Campos editáveis são
    mascarados, pois a árvore pode conter usuário, CPF ou senha digitados.
    """
    if dialog is None:
        return None

    def safe(call: Any, default: str = "") -> str:
        try:
            value = call() if callable(call) else call
            return str(value or default)
        except Exception:
            return default

    class_name = safe(getattr(dialog, "class_name", None))
    title = safe(getattr(dialog, "window_text", None))
    controls: list[dict[str, str]] = []
    texts = [title] if title else []
    try:
        children = list(dialog.descendants())
    except Exception:
        children = []
    for child in children:
        child_class = safe(getattr(child, "class_name", None))
        child_text = safe(getattr(child, "window_text", None))
        if class_name == "TFrmPassWord" and child_class in {"TEdit", "TJvValidateEdit"}:
            # Mantém o controle e sua posição textual, mas nunca o conteúdo
            # digitado no formulário de credenciais.
            safe_text = "<redacted-input>"
        else:
            safe_text = _redact(child_text)
        controls.append({"class": child_class, "text": safe_text})
        if safe_text:
            texts.append(safe_text)

    full_text = _redact(" | ".join(texts))
    commands = _extract_commands(full_text)
    nodeid = _dialog_context.get()
    scenario_match = re.search(r"\b(?:INI|DES|CLI|FUN|PRE|GEST|ORC|REP|VEN|TEF)-\d+\b", nodeid, re.I)
    scenario_id = scenario_match.group(0).upper() if scenario_match else nodeid
    observation = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "scenario_id": scenario_id,
        "test": nodeid,
        "context": context_label,
        "class_name": class_name,
        "title": _redact(title),
        "full_text": full_text,
        "controls": controls,
        "commands": [
            {
                "command": command,
                "coverage": COMMAND_COVERAGE.get(
                    command,
                    "não localizado nos cenários automatizados; verificar divergência roteiro x sistema",
                ),
            }
            for command in commands
        ],
        "catalogada": class_name in KNOWN_DIALOG_CLASSES,
    }
    _dialog_observations.append(observation)

    # Escrita imediata protege a evidência caso o teste seguinte encontre um
    # modal residual ou o processo seja interrompido antes do sessionfinish.
    try:
        output_dir = load_config().report_root / datetime.now().strftime("%Y-%m-%d")
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "dialogs_observed.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(observation, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return observation


def _extract_commands(text: str) -> list[str]:
    pattern = re.compile(
        r"(?i)\b(?:CTRL|SHIFT|ALT)\s*\+\s*(?:F\d{1,2}|[A-Z0-9]+)\b|"
        r"\bF\d{1,2}\b|\b(?:ESC|ENTER|SPACE|ESPA[CÇ]O)\b"
    )
    commands: list[str] = []
    for match in pattern.findall(text or ""):
        command = re.sub(r"\s+", "", match.upper())
        if command in {"ESPAÇO", "ESPACO"}:
            command = "SPACE"
        if command not in commands:
            commands.append(command)
    return commands


def record_runtime_evidence(nodeid: str, evidence: str) -> None:
    """Attach evidence generated by a page object to the TXT report."""
    _runtime_evidence[nodeid] = _redact(evidence)


def pytest_runtest_logreport(report: Any) -> None:
    if report.when == "call" or (report.when == "setup" and report.outcome in {"failed", "skipped"}):
        message = ""
        was_xfail = getattr(report, "wasxfail", None)
        if was_xfail:
            outcome = "XFAIL" if report.outcome == "skipped" else "XPASS"
            message = str(was_xfail)
        elif report.outcome == "failed":
            outcome = "FAILED"
            message = getattr(report, "longreprtext", "") or "Falha sem mensagem"
        elif report.outcome == "skipped":
            outcome = "SKIPPED"
            message = getattr(report, "longreprtext", "") or "Teste skipped"
        else:
            outcome = report.outcome.upper()
        _results.append({
            "test": report.nodeid,
            "outcome": outcome,
            "duration_seconds": round(float(getattr(report, "duration", 0.0)), 3),
            "message": _redact(message),
        })


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if not _results:
        return
    config = load_config(Path(str(session.config.rootpath)))
    output_dir = config.report_root / datetime.now().strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(_results)
    (output_dir / "test_results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "test_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["test", "outcome", "duration_seconds", "message"])
        writer.writeheader()
        writer.writerows(rows)
    _write_text_report(output_dir / "test_results.txt", rows, _dialog_observations)
    _write_test_set_report(output_dir / "sets", rows, _dialog_observations)


def _write_test_set_report(
    path: Path,
    rows: list[dict[str, Any]],
    dialog_observations: list[dict[str, Any]] | None = None,
) -> None:
    """Cria um TXT unico para todo o conjunto registrado nesta execucao."""
    path.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = path / f"test_set_{run_stamp}.txt"
    _write_text_report(report_path, rows, dialog_observations)


def _write_text_report(
    path: Path,
    rows: list[dict[str, Any]],
    dialog_observations: list[dict[str, Any]] | None = None,
) -> None:
    lines = [
        "SATPDV - RESULTADO DOS TESTES",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for row in rows:
        message = row["message"] or "Sem mensagem adicional."
        runtime = _runtime_evidence.get(row["test"])
        if runtime:
            message = f"{message}\n{runtime}"
        roteiro, tentativa = _test_details(row["test"])
        lines.extend(
            [
                f"Teste: {row['test']}",
                f"Roteiro: {roteiro}",
                f"Resultado: {row['outcome']}",
                f"Duracao: {row['duration_seconds']}s",
                "Tentativa realizada:",
                tentativa,
                "Motivo/mensagem:",
                message,
                "",
                "-" * 80,
                "",
            ]
        )
    lines.extend(
        [
            "",
            "=" * 80,
            "DIALOGS OBSERVADAS — COMANDOS/INSTRUÇÕES",
            "=" * 80,
            "",
        ]
    )
    if not dialog_observations:
        lines.append("Nenhuma dialog foi observada nesta execução.")
    else:
        for observation in dialog_observations:
            lines.extend(
                [
                    f"Cenário: {observation['scenario_id']}",
                    f"Teste: {observation['test']}",
                    f"Contexto: {observation['context']}",
                    f"Classe: {observation['class_name'] or '<não exposta>'}",
                    f"Título: {observation['title']}",
                    f"Dialog catalogada: {'SIM' if observation['catalogada'] else 'NÃO — mapear manualmente'}",
                    "Texto completo (campos editáveis mascarados):",
                    observation["full_text"] or "<vazio>",
                    "Comandos/instruções identificados:",
                ]
            )
            if observation["commands"]:
                for command in observation["commands"]:
                    lines.append(f"- {command['command']}: {command['coverage']}")
            else:
                lines.append("- Nenhum atalho identificável no texto capturado.")
            lines.extend(["", "-" * 80, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _test_details(nodeid: str) -> tuple[str, str]:
    """Relaciona cada teste automatizado ao roteiro e à tentativa executada."""
    details = {
        "test_pause_cash_register": (
            "Pausa/fechamento do caixa (identificador do roteiro não informado)",
            "Iniciei o PDV real, acionei a pausa, cliquei em TFrmPDVPausa, reabri TFrmPassWord, autentiquei e validei PDV_READY.",
        ),
        "test_blank_password_is_rejected": (
            "INI-01",
            "Abri TFrmPassWord, usei a matricula real configurada e deixei a senha vazia; validei o modal de credenciais invalidas e o confirmei.",
        ),
        "test_open_help": (
            "Abertura da ajuda (identificador do roteiro não informado)",
            "Abri a ajuda, validei TFrmPDVAjuda e enviei ESC para retornar ao fluxo.",
        ),
        "test_escape_after_product": (
            "Venda ativa/ESC (identificador do roteiro não informado)",
            "Inseri o produto, enviei ESC, li o modal ativo, aceitei a confirmação quando necessário e informei o motivo do cancelamento.",
        ),
        "test_escape_after_cancelled_sale_closes_pdv": (
            "INI-06",
            "Inseri o produto, cancelei a venda preenchendo o motivo no TEdit do dialogo VCL, enviei ESC e validei TFrmPDVCaixaFechado.",
        ),
        "test_open_cash_drawer_with_authorization": (
            "INI-07",
            "Enviei F9 ao TFrmPDV, localizei TFrmPassWord e autentiquei; a abertura fisica depende de impressora/gaveta e permanece explicitamente manual.",
        ),
        "test_license_validation_by_cnpj": (
            "INI-16",
            "Cenario reservado para alterar CNPJ no Cadastro de Empresa e testar o SAT principal e o atalho direto; nao foi simulado nem alterado banco/cadastro.",
        ),
        "test_insert_product_from_roteiro": (
            "INI-10",
            "Usei o código de produto configurado, inseri o item e validei sua presença no TMemo pnlProdutos; ao finalizar, tentei cancelar informando o motivo.",
        ),
        "test_remove_product_from_sale": (
            "INI-11",
            "Usei o código de produto configurado, inseri duas linhas, cancelei o item 2 pela referência negativa -2 e validei a linha negativa renderizada; ao finalizar, informei o motivo do cancelamento.",
        ),
        "test_remove_quantity_from_product": (
            "INI-12",
            "Inseri o produto 1 com quantidade 2 usando asterisco, removi uma unidade com -1*1 e validei o total de R$ 1,00; ao finalizar, informei o motivo do cancelamento.",
        ),
        "test_remove_quantity_by_item_index": (
            "INI-13",
            "Inseri 3* do produto 3 e 4* do produto 5, validei o total de R$ 7,00, removi uma unidade pelo indice -1*2 e validei o total de R$ 6,00; ao finalizar, informei o motivo do cancelamento.",
        ),
        "test_remove_nonexistent_item_shows_warning": (
            "INI-14",
            "Inseri o produto 1, enviei -5, capturei a mensagem de item inexistente/ja cancelado e fechei o aviso conhecido antes de cancelar a venda com motivo.",
        ),
        "test_insert_nonexistent_product_shows_warning": (
            "INI-15",
            "Enviei o codigo 1234, capturei a dialog de produto nao encontrado e fechei somente o aviso conhecido.",
        ),
        "test_price_consultation_validates_store_stock": (
            "INI-08/INI-09 - consulta de preco e estoque por loja",
            "Acionei F1 no TEdit real, consultei o produto 4, abri a GridLoja por Ctrl+E, li a coluna Quant. por controle/OCR recortado e validei cada loja contra o roteiro.",
        ),
        "test_finalize_sale_with_cash": (
            "Finalização de venda com pagamento em dinheiro (identificador do roteiro não informado)",
            "Iniciei uma venda real, inseri o produto configurado, abri F3 - Formas de Pagamento, selecionei Dinheiro com Home/Down, confirmei o valor integral com dois ENTERs, analisei o comprovante, fechei com Close/ESC e respondi Não ao modal de reexibição; em caso de falha antes do envio, informei o motivo do cancelamento.",
        ),
        "test_finalize_sale_with_card": (
            "VEN-16 / TEF-02 - venda por tipo de cartao exibido no SATPDV",
            "O cenario foi identificado, mas marcado SKIPPED porque exige terminal TEF, cartao/pinpad e autorizacao externa; a suite nao simula hardware ou servico.",
        ),
        "test_card_installments_over_100_are_rejected": (
            "VEN-21 - parcelamento acima de 100",
            "Iniciei uma venda real, selecionei Mastercard Credito, informei 101 parcelas e validei a regra do roteiro; o comportamento observado (aceite de 101) permanece como known issue/xfail.",
        ),
        "test_fractional_quantity_is_limited_to_three_decimal_places": (
            "VEN-34 - quantidade fracionada",
            "Usei o produto 46 do roteiro, informei 0,12345 seguido de asterisco e validei no TMemo pnlProdutos que a quantidade renderizada ficou limitada a 0,123.",
        ),
        "test_empty_sale_shortcut_does_not_open_payment": (
            "VEN-07/VEN-08 - F2/F3 com venda vazia",
            "Com o PDV sem itens, acionei o atalho parametrizado F2 ou F3 e validei que nenhum formulario de pagamento ou modal foi aberto e que o TFrmPDV permaneceu habilitado.",
        ),
        "test_external_roteiro_items_are_skipped": (
            "Itens do roteiro fora do escopo seguro",
            "O grupo foi avaliado quanto a dependencias externas e marcado SKIPPED sem abrir fluxo, simular hardware ou alterar dados compartilhados; o motivo especifico foi gravado no log de evidencia.",
        ),
        "test_poc_open_login_and_insert_product": (
            "PoC de venda/login (identificador do roteiro não informado)",
            "Iniciei o fluxo real, tratei recuperação de venda com Não/ESC, autentiquei com as credenciais do ambiente, dispensei avisos, inseri o produto e tentei cancelar informando o motivo.",
        ),
        "test_poc_invalid_login": (
            "Login inválido (identificador do roteiro não informado)",
            "Abri o formulário de login, enviei credenciais inválidas e tentei validar o diálogo/mensagem retornado pelo SATPDV.",
        ),
    }
    for test_name, value in details.items():
        if test_name in nodeid:
            return value
    return (
        "Não identificado na suíte",
        "O teste foi executado pelo pytest com o fluxo configurado para o cenário.",
    )


def _redact(value: str) -> str:
    config = load_config()
    if config.password:
        value = value.replace(config.password, "<redacted>")
    return re.sub(r"(PDV_PASSWORD\s*[=:]\s*)[^\s,;]+", r"\1<redacted>", value, flags=re.IGNORECASE)
