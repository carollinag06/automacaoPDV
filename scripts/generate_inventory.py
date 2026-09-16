"""Generate the raw SATPDV case inventory using latest-run-wins semantics.

The session reports are authoritative for dynamically executed cases. The
test id is read from the ``Teste:`` node id because ``Roteiro:`` can be the
generic value ``Não identificado na suíte``. Cases without a dynamic report
retain the static status from ``docs/test_matrix.csv``.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


CASE_RE = re.compile(
    r"\b(?:INI|SUP|SAN|VEN|TEF|DES|CLI|FUN|PRE|GEST|ORC|REP|CAN|FIS|CER|BAL|MFI|MES|REI|SIN|CFG|PAR|CB|FRT|PRD|EXT)-\d{2}\b",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r"\b([A-Z]+)-(\d{2})\s+a\s+([A-Z]+)-(\d{2})\b",
    re.IGNORECASE,
)
BLOCK_RE = re.compile(
    r"(?ms)^Teste: (?P<nodeid>[^\r\n]+)\r?\n"
    r"Roteiro: (?P<roteiro>[^\r\n]+)\r?\n"
    r"Resultado: (?P<status>PASSED|FAILED|SKIPPED|XFAIL|BLOCKED|ERROR)"
    r"(?P<body>.*?)(?=^Teste: |\Z)",
)
SESSION_RE = re.compile(r"^Gerado em:\s*(\S+)", re.MULTILINE)
FILENAME_RE = re.compile(r"test_set_(\d{8})_(\d{6})_")


@dataclass(frozen=True)
class DynamicResult:
    case_id: str
    status: str
    timestamp: datetime
    report_path: Path
    nodeid: str


def session_timestamp(path: Path, text: str) -> datetime:
    match = SESSION_RE.search(text)
    if match:
        raw = match.group(1).strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=None)
        except ValueError:
            pass
    match = FILENAME_RE.search(path.name)
    if match:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    # A report without an internal/session timestamp cannot win over a
    # timestamped report, but it remains parseable as the oldest dynamic run.
    return datetime.min


def case_ids(text: str) -> set[str]:
    """Extract ids and expand Portuguese ranges such as SIN-01 a SIN-03."""
    found = {match.upper() for match in CASE_RE.findall(text)}
    for match in RANGE_RE.finditer(text):
        prefix_start, start, prefix_end, end = match.groups()
        if prefix_start.upper() != prefix_end.upper():
            continue
        for number in range(int(start), int(end) + 1):
            found.add(f"{prefix_start.upper()}-{number:02d}")
    return found


def parse_dynamic_reports(reports_root: Path) -> dict[str, DynamicResult]:
    """Recursively parse reports/YYYY-MM-DD/sets/*.txt and deduplicate."""
    latest: dict[str, DynamicResult] = {}
    date_dir_re = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
    # rglob keeps the parser recursive while the parent/date checks restrict
    # input to session-set reports and exclude generated inventories/logs.
    paths = sorted(
        path
        for path in reports_root.rglob("*.txt")
        if path.parent.name == "sets"
        and any(date_dir_re.fullmatch(part) for part in path.parts)
    )
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        timestamp = session_timestamp(path, text)
        for block in BLOCK_RE.finditer(text):
            nodeid = block.group("nodeid").strip()
            status = block.group("status").upper()
            # For direct tests the node id is the source of truth. The body
            # can contain tracebacks/evidence mentioning other case IDs and
            # must not contaminate their latest result. Only the grouped
            # out-of-scope registrar needs its reason text to expand ranges.
            source = " ".join((nodeid, block.group("roteiro")))
            if "test_external_roteiro_items_are_skipped" in nodeid:
                source += " " + block.group("body")
            ids = case_ids(source)
            for case_id in ids:
                candidate = DynamicResult(case_id, status, timestamp, path, nodeid)
                current = latest.get(case_id)
                if current is None or (candidate.timestamp, str(candidate.report_path)) > (
                    current.timestamp,
                    str(current.report_path),
                ):
                    latest[case_id] = candidate
    return latest


def static_cases(matrix_path: Path) -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    with matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter="|"):
            if not row:
                continue
            case_id = row[0].strip().upper()
            if not CASE_RE.fullmatch(case_id):
                continue
            static_status = row[6].strip().upper() if len(row) > 6 else "UNCLASSIFIED"
            cases.append((case_id, static_status))
    if len(cases) != 203:
        raise RuntimeError(f"Esperados 203 casos na matriz; encontrados {len(cases)}")
    if len({case_id for case_id, _ in cases}) != len(cases):
        raise RuntimeError("A matriz contém IDs de caso duplicados")
    return cases


def render_inventory(
    matrix_path: Path,
    reports_root: Path,
    output_path: Path,
) -> tuple[str, dict[str, DynamicResult]]:
    matrix_cases = static_cases(matrix_path)
    dynamic = parse_dynamic_reports(reports_root)
    lines = [
        "SATPDV - LISTA BRUTA DE CASOS CONHECIDOS",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "Regra: latest run wins; resultado dinamico mais recente prevalece.",
        "Casos sem relatorio dinamico preservam a classificacao estatica da matriz.",
        "Formato: id_do_caso | categoria_atual | data_ou_classificacao",
        "",
    ]
    for case_id, static_status in matrix_cases:
        result = dynamic.get(case_id)
        if result is None:
            category = static_status
            when = "classificacao estatica da matriz; sem relatorio dinamico"
        else:
            category = result.status
            relative = result.report_path.as_posix()
            when = f"{result.timestamp.isoformat()} ({relative})"
        lines.append(f"{case_id} | {category} | {when}")
    content = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return content, dynamic


def validate_acceptance(dynamic: dict[str, DynamicResult]) -> list[str]:
    expectations = [
        ("ORC", 1, 12, "2026-09-03"),
        ("REP", 1, 7, "2026-09-03"),
        ("PRE", 1, 10, "2026-09-02T12:35"),
        ("GEST", 1, 6, "2026-09-02T12:35"),
    ]
    problems: list[str] = []
    for prefix, start, end, expected_date in expectations:
        for number in range(start, end + 1):
            case_id = f"{prefix}-{number:02d}"
            result = dynamic.get(case_id)
            if result is None:
                problems.append(f"{case_id}: nenhum relatorio dinamico")
                continue
            if result.status != "PASSED":
                problems.append(f"{case_id}: status atual {result.status}")
            if not result.timestamp.isoformat().startswith(expected_date):
                problems.append(
                    f"{case_id}: timestamp atual {result.timestamp.isoformat()} "
                    f"difere do esperado {expected_date} (latest run wins)"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--check-acceptance", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output or root / "reports" / "2026-09-11" / "casos_conhecidos_brutos_20260911.txt"
    if not output.is_absolute():
        output = root / output
    _, dynamic = render_inventory(root / "docs" / "test_matrix.csv", root / "reports", output)
    problems = validate_acceptance(dynamic)
    print(f"Inventario gerado: {output}")
    print(f"Casos estaticos: {len(static_cases(root / 'docs' / 'test_matrix.csv'))}")
    if problems:
        print("VALIDACAO:")
        for problem in problems:
            print(f"- {problem}")
        return 1 if args.check_acceptance else 0
    print("VALIDACAO: ORC/REP/PRE/GEST conforme expectativas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
