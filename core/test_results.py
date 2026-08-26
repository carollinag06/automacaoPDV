from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config


_results: list[dict[str, Any]] = []


def pytest_runtest_logreport(report: Any) -> None:
    if report.when == "call" or (report.when == "setup" and report.outcome in {"failed", "skipped"}):
        message = ""
        if report.outcome == "failed":
            message = getattr(report, "longreprtext", "") or "Falha sem mensagem"
        elif report.outcome == "skipped":
            message = getattr(report, "longreprtext", "") or "Teste skipped"
        _results.append({
            "test": report.nodeid,
            "outcome": report.outcome.upper(),
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


def _redact(value: str) -> str:
    config = load_config()
    if config.password:
        value = value.replace(config.password, "<redacted>")
    return re.sub(r"(PDV_PASSWORD\s*[=:]\s*)[^\s,;]+", r"\1<redacted>", value, flags=re.IGNORECASE)
