from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


def _bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "sim", "s"}


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class TestConfig:
    exe_path: Path
    user: str
    password: str
    manager_user: str
    manager_password: str
    login_required: bool
    product_code: str
    invalid_product_code: str
    par_quantity_maxima: str | None
    par_inverter_lista: str | None
    allow_real_run: bool
    backend: str
    window_mode: str
    window_title_regex: str
    login_title_regex: str
    start_timeout: float
    action_timeout: float
    report_root: Path
    close_after_test: bool

    def __repr__(self) -> str:
        values = []
        for field in fields(self):
            value = "<redacted>" if "password" in field.name else getattr(self, field.name)
            values.append(f"{field.name}={value!r}")
        return f"TestConfig({', '.join(values)})"

    @property
    def credentials_configured(self) -> bool:
        return not self.login_required or bool(self.user and self.password)

    @property
    def manager_credentials(self) -> tuple[str, str]:
        """Credenciais de gerente vindas do .env, com fallback ao operador real."""
        return self.manager_user, self.manager_password

    @property
    def manager_credentials_configured(self) -> bool:
        return bool(self.manager_user and self.manager_password)

    @property
    def product_configured(self) -> bool:
        return bool(self.product_code)

    @property
    def runnable(self) -> bool:
        return self.allow_real_run and self.exe_path.exists() and self.credentials_configured


def load_config(project_root: Path | None = None) -> TestConfig:
    root = project_root or Path(__file__).resolve().parents[1]
    env = _load_dotenv(root / ".env")

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, env.get(name, default))

    json_path = root / "config" / "test_config.json"
    json_values: dict[str, object] = {}
    if json_path.exists():
        json_values = json.loads(json_path.read_text(encoding="utf-8"))

    def value(env_name: str, json_name: str, default: object = "") -> object:
        env_value = get(env_name)
        if env_value != "":
            return env_value
        return json_values.get(json_name, default)

    raw_exe_path = str(value("PDV_EXE_PATH", "exe_path", "")).strip()
    exe_path = Path(raw_exe_path).expanduser() if raw_exe_path else Path("__PDV_EXE_PATH_NOT_CONFIGURED__")
    report_root = Path(str(value("PDV_REPORT_ROOT", "report_root", "reports")))
    if not report_root.is_absolute():
        report_root = root / report_root
    return TestConfig(
        exe_path=exe_path,
        user=str(value("PDV_USER", "user", "")),
        password=str(value("PDV_PASSWORD", "password", "")),
        manager_user=str(value("PDV_MANAGER_USER", "manager_user", value("PDV_USER", "user", ""))),
        manager_password=str(value("PDV_MANAGER_PASSWORD", "manager_password", value("PDV_PASSWORD", "password", ""))),
        login_required=_bool(value("PDV_LOGIN_REQUIRED", "login_required", True), True),
        product_code=str(value("PDV_PRODUCT_CODE", "product_code", "")),
        invalid_product_code=str(value("PDV_INVALID_PRODUCT_CODE", "invalid_product_code", "1234")),
        par_quantity_maxima=(
            str(value("PDV_PAR_QTDE_MAXIMA", "par_quantity_maxima", "")).strip() or None
        ),
        par_inverter_lista=(
            str(value("PDV_PAR_INVERTER_LISTA", "par_inverter_lista", "")).strip().upper() or None
        ),
        allow_real_run=_bool(value("PDV_ALLOW_REAL_RUN", "allow_real_run", False)),
        backend=str(value("PDV_BACKEND", "backend", "win32")),
        window_mode=str(value("PDV_WINDOW_MODE", "window_mode", "fullscreen")).strip().lower(),
        window_title_regex=str(value("PDV_WINDOW_TITLE_REGEX", "window_title_regex", r"SAT\s*-\s*PDV|SATPDV|PDV")),
        login_title_regex=str(value("PDV_LOGIN_TITLE_REGEX", "login_title_regex", r"login|senha|matr[ií]cula|SAT")),
        start_timeout=float(value("PDV_START_TIMEOUT", "start_timeout", 30)),
        action_timeout=float(value("PDV_ACTION_TIMEOUT", "action_timeout", 10)),
        report_root=report_root,
        close_after_test=_bool(value("PDV_CLOSE_AFTER_TEST", "close_after_test", True), True),
    )
