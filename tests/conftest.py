from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.app import PdvApplication
from core.config import TestConfig, load_config
from core.evidence import capture_failure, configure_logger, report_dir
from core.test_results import pytest_runtest_logreport, pytest_sessionfinish
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--product-code",
        action="store",
        default=None,
        help="Codigo do produto usado pelos cenarios genericos (1 a 33 por padrao).",
    )


@pytest.fixture(scope="session")
def test_config() -> TestConfig:
    return load_config()


@pytest.fixture
def product_code(request: pytest.FixtureRequest, test_config: TestConfig) -> str:
    """Retorna CLI > .env > codigo generico padrao, sem criar dados no PDV."""
    cli_value = request.config.getoption("--product-code")
    return str(cli_value or test_config.product_code or "1")


@pytest.fixture
def get_generic_product_code(product_code: str):
    """Retorna um codigo generico valido na faixa de 1 a 33."""
    def select(candidate: str | int | None = None) -> str:
        value = str(product_code if candidate is None else candidate)
        try:
            number = int(value)
        except (TypeError, ValueError):
            return "1"
        return str(number) if 1 <= number <= 33 else "1"

    return select


@pytest.fixture
def evidence(request: pytest.FixtureRequest, test_config: TestConfig):
    name = request.node.name.replace("[", "_").replace("]", "_")
    directory = report_dir(test_config.report_root, name)
    logger = configure_logger(directory, name)
    logger.info(json.dumps({"test": request.node.nodeid, "expected": "see test"}, ensure_ascii=False))
    return directory, logger


@pytest.fixture
def app(request: pytest.FixtureRequest, test_config: TestConfig, evidence):
    if not test_config.allow_real_run:
        pytest.skip("PDV_ALLOW_REAL_RUN=false; execução real é opt-in")
    if not test_config.exe_path.exists():
        pytest.skip(f"SATPDV.exe não encontrado: {test_config.exe_path}")
    if not test_config.credentials_configured:
        pytest.skip("Credenciais de teste não configuradas")

    directory, logger = evidence
    application = PdvApplication(test_config)
    try:
        application.start()
        allow_recovery = request.node.get_closest_marker("recovery") is not None
        application.dismiss_recovery_prompt(
            allow_recovery=allow_recovery,
            timeout=test_config.action_timeout,
        )
        if test_config.login_required:
            login_dialog = application.reveal_login_dialog()
            login = LoginPage(login_dialog or application.window, test_config)
            if not login.is_present():
                raise AssertionError("PDV_LOGIN_REQUIRED=true, mas o clique na tela inicial não expôs o diálogo de login")
            login.login()
            application.wait_until_ready(test_config.start_timeout, allow_recovery=allow_recovery)
        yield application
    except Exception as exc:
        directory, logger = evidence
        logger.exception("Falha na preparação/execução: %s", exc)
        capture_failure(application.window, directory, request.node.name, {"error": str(exc), "controls": application.diagnostics()})
        raise
    finally:
        logger.info(json.dumps({"phase": "teardown", "windows": application.top_level_diagnostics()}, ensure_ascii=False))
        if test_config.close_after_test:
            application.close()


@pytest.fixture
def pdv(app: PdvApplication, test_config: TestConfig) -> PdvPage:
    return PdvPage(app.window, test_config.action_timeout)


@pytest.fixture
def raw_app(request: pytest.FixtureRequest, test_config: TestConfig, evidence):
    if not test_config.allow_real_run:
        pytest.skip("PDV_ALLOW_REAL_RUN=false; execução real é opt-in")
    if not test_config.exe_path.exists():
        pytest.skip(f"SATPDV.exe não encontrado: {test_config.exe_path}")
    directory, logger = evidence
    application = PdvApplication(test_config)
    try:
        application.start()
        application.dismiss_recovery_prompt(
            allow_recovery=request.node.get_closest_marker("recovery") is not None,
            timeout=test_config.action_timeout,
        )
        if test_config.login_required:
            login_dialog = application.reveal_login_dialog()
            if login_dialog is None:
                raise AssertionError("A tela inicial não expôs o diálogo real de login após o clique")
            application.window = login_dialog
        yield application
    except Exception as exc:
        directory, logger = evidence
        logger.exception("Falha no fluxo bruto: %s", exc)
        capture_failure(application.window, directory, raw_app.__name__, {"error": str(exc), "controls": application.diagnostics()})
        raise
    finally:
        logger.info(json.dumps({"phase": "teardown", "windows": application.top_level_diagnostics()}, ensure_ascii=False))
        if test_config.close_after_test:
            application.close()
