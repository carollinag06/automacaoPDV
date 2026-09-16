"""Reconcilia resultados por nodeid atual, sem executar testes funcionais.

Uso: python work/reconcile_history.py --cutoff test_set_YYYYMMDD_HHMMSS_ffffff.txt
O cutoff é o último conjunto anterior à rodada. IDs substituídos são
relacionados em retirados; decisões do revisor não reescrevem execuções.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=root, env=env, text=True, encoding="utf-8", capture_output=True, check=True,
    )
    active = {s.strip() for s in collected.stdout.splitlines() if s.startswith("tests/") and "::" in s}
    if not active:
        raise RuntimeError("A coleta não retornou nodeids; nenhuma contagem será produzida.")
    record_pattern = re.compile(
        r"^Teste: (.+)\nRoteiro:.*\nResultado: (\w+)", re.MULTILINE
    )
    before, latest = {}, {}
    for path in sorted((root / "reports").glob("*/sets/test_set_*.txt")):
        content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        # Não inclui observações de dialogs nem imprime corpos de mensagens.
        for match in record_pattern.finditer(content):
            nodeid, outcome = match.groups()
            row = {"test": nodeid, "outcome": outcome, "source": str(path)}
            latest[nodeid] = row
            if path.name <= args.cutoff:
                before[nodeid] = row

    new_nodes = {n for n in active if n.startswith("tests/test_configuration.py::")}
    retired_group = "tests/test_scope.py::test_external_roteiro_items_are_skipped[configuracao e parametros]"
    previous_nodes = (active - new_nodes) | {retired_group}

    def rows_for(nodes, history):
        return [history.get(n, {"test": n, "outcome": "NOT_RUN", "source": None}) for n in sorted(nodes)]

    previous = rows_for(previous_nodes, before)
    current = rows_for(active, latest)
    previous_counts = Counter(r["outcome"] for r in previous)
    current_counts = Counter(r["outcome"] for r in current)
    statuses = sorted(previous_counts.keys() | current_counts.keys())
    delta = {s: current_counts[s] - previous_counts[s] for s in statuses}
    output = root / "reports" / datetime.now().strftime("%Y-%m-%d")
    output.mkdir(parents=True, exist_ok=True)
    stem = "incremental_classification_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated": datetime.now().isoformat(),
        "cutoff": args.cutoff,
        "unit": "nodeid pytest; resultados brutos históricos, não cobertura integral do roteiro",
        "baseline_reconciled": dict(previous_counts),
        "delta": delta,
        "total": dict(current_counts),
        "baseline_nodes": len(previous), "total_nodes": len(current),
        "current_rows": current,
        "previous_rows": previous,
        "retired": [r for n, r in sorted(latest.items()) if n not in active],
        "new_dispositions": rows_for(new_nodes, latest),
        "extras": [r for r in current if "test_EXT_" in r["test"]],
        "duplicate_issue": {"MFI-04": "SUP-03; dois testes, um único achado"},
        "reviewer_classifications_separate_from_execution": {
            "VEN-14": "XFAIL confirmado pelo revisor; último conjunto bruto é FAILED, sem reexecução posterior",
            "CLI-05": "relatórios consolidados anteriores citam XFAIL; último conjunto bruto é FAILED; não reexecutado nesta rodada",
            "VEN-15": "divergência/regra pendente; SKIPPED no conjunto bruto é histórico, não classificação aceita nesta rodada",
            "CFG-03": "PASS histórico cobre apenas leitura da loja atual; não comprova troca de loja descrita no roteiro",
        },
    }
    json_path = output / (stem + ".json")
    txt_path = output / (stem + ".txt")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "SATPDV — RELATÓRIO INCREMENTAL / CLASSIFICAÇÃO E RECONCILIAÇÃO",
        "Gerado: " + payload["generated"], "",
        "DESTA RODADA",
        "PASS funcionais novos: 0. Nenhum fluxo do PDV foi executado.",
        "Verificação estática: compileall tests pages core aprovado.",
        "pytest tests/test_configuration.py: 3 skipped, 2 deselected (BLOCKED no relatório), 0.29s.",
        "CFG-01/CFG-02: manual por decisão do revisor; sem alteração de banco/módulo SAT.",
        "PAR-03: manual; F2/F3 pelo teclado não comprova abertura física da gaveta.",
        "PAR-01: blocked; consulta somente leitura a PARAM (PARAM=1) retornou PDVQTDEMAXIMA=NULL.",
        "Falta limite positivo definido/configurado pelo responsável e depois implementar o fluxo de excesso.",
        "PAR-02: blocked; PDVINVERTERLISTADEPRODUTOS='N'; roteiro exige 'S'.",
        "Falta configuração aprovada e implementação da validação da inversão.",
        "Fonte da consulta: D:\\SAT Sistemas\\SAT_HOMOLOGADAS\\SAT.FDB; sem escrita no banco.",
        "Seção 21 reconsultada: F5/Ctrl+R/F10/F2/F3 existem pelo teclado, mas geração/resgate",
        "dependem da API de cashback; classificação manual externa histórica mantida, sem reexecução.",
        "Nenhuma seção declarada integralmente validada nesta rodada.", "",
        "DIALOGS OBSERVADAS — COMANDOS/INSTRUÇÕES",
        "Nenhuma: execução de classificação não abre aplicação.",
        "Novos XFAIL: 0; novas divergências observadas no sistema: 0.", "",
        "RECONCILIAÇÃO DO HISTÓRICO (NÃO SÃO NOVAS EXECUÇÕES)",
        "O agregado anterior 109/28/8 não é reproduzível por nodeid atual e não foi usado como baseline.",
        "Baseline reconstruído usando o último conjunto até " + args.cutoff,
        "Uma entrada agrupada SKIPPED foi substituída por 3 casos manual/SKIPPED e 2 BLOCKED.",
    ]
    lines += [f"{s}: {previous_counts[s]} + ({delta[s]:+d}) = {current_counts[s]}" for s in statuses]
    lines += [
        f"Nodeids: {len(previous)} + ({len(current)-len(previous):+d}) = {len(current)}.",
        "PASSED nesta tabela significa último resultado registrado do teste, sem garantir estabilidade",
        "na revisão atual nem cobertura completa do caso do roteiro. A suíte completa não foi executada.",
        "Os SKIPPED agrupam múltiplos IDs de roteiro; não converter este número em cobertura formal.",
        "EXT-01 é extra: separar do total formal. MFI-04/SUP-03 não são dois bugs.", "",
        "DECISÕES POSTERIORES SEPARADAS DOS RESULTADOS BRUTOS",
    ]
    lines += [f"{k}: {v}" for k, v in payload["reviewer_classifications_separate_from_execution"].items()]
    lines += ["", "NOVIDADES DE CLASSIFICAÇÃO E SUAS FONTES"]
    lines += [f"{r['outcome']} | {r['test']} | {r['source']}" for r in payload["new_dispositions"]]
    lines += ["", "ÚLTIMO RESULTADO POR TESTE ATUAL — HISTÓRICO COM FONTE"]
    lines += [f"{r['outcome']} | {r['test']} | {r['source'] or 'nenhuma execução localizada'}" for r in current]
    lines += ["", "ARTEFATOS", str(txt_path), str(json_path)]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"txt": str(txt_path), "json": str(json_path), "baseline": dict(previous_counts),
                      "delta": delta, "total": dict(current_counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
