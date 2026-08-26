from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def report_dir(root: Path, test_name: str) -> Path:
    day = datetime.now().strftime("%Y-%m-%d")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", test_name)
    path = root / day
    (path / "screenshots").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    (path / "results").mkdir(parents=True, exist_ok=True)
    return path


def configure_logger(path: Path, test_name: str) -> logging.Logger:
    logger = logging.getLogger(f"satpdv.{test_name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(path / "logs" / f"{test_name}.jsonl", encoding="utf-8")
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    return logger


def capture_failure(window: Any, path: Path, test_name: str, details: dict[str, Any] | None = None) -> None:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", test_name)
    screenshot = path / "screenshots" / f"{safe}.png"
    try:
        window.capture_as_image().save(screenshot)
    except Exception as exc:  # Evidence must not hide the original failure.
        screenshot.write_text(f"Screenshot indisponível: {exc}", encoding="utf-8")
    context = {
        "test": test_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "window": _window_context(window),
        "details": details or {},
        "screenshot": str(screenshot),
    }
    (path / "results" / f"{safe}.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")


def _window_context(window: Any) -> dict[str, Any]:
    try:
        return {
            "title": window.window_text(),
            "class_name": window.class_name(),
            "handle": getattr(window, "handle", None),
            "process_id": window.process_id(),
        }
    except Exception as exc:
        return {"error": str(exc)}
