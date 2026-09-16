from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from core.app import PdvApplication, PdvCloseError
from core.config import TestConfig, load_config
from core.evidence import capture_failure, configure_logger, report_dir
from core.test_results import (
    pytest_runtest_logreport,
    pytest_sessionfinish,
    set_dialog_context,
    record_blocked_case,
)
from pages.base_page import UnknownDialogError
from pages.login_page import LoginPage
from pages.pdv_page import PdvPage


class _AppPool:
    """Owns the reusable authenticated process and intentional fresh runs."""

    def __init__(self, config: TestConfig) -> None:
        self.config = config
        self.shared: PdvApplication | None = None
        self.transient: PdvApplication | None = None

    def _start(self, login_required: bool) -> PdvApplication:
        application = PdvApplication(self.config)
        application.start()
        application.dismiss_recovery_prompt(timeout=self.config.action_timeout)
        if login_required and self.config.login_required:
            login_dialog = application.reveal_login_dialog()
            login = LoginPage(login_dialog or application.window, self.config)
            if not login.is_present():
                raise AssertionError(
                    "PDV_LOGIN_REQUIRED=true, mas o clique na tela inicial nao expos o dialogo real de login"
                )
            login.login()
            application.wait_until_ready(self.config.start_timeout)
        if login_required and self.config.login_required:
            main = application._find_form("TFrmPDV", timeout=0.5)
            if main is None or not application._is_active_window(main):
                application.close(reset=False)
                raise AssertionError(
                    "O processo compartilhado nao terminou em TFrmPDV visivel e habilitado"
                )
            application.window = main
        return application

    def acquire(self, fresh: bool = False, login_required: bool = True) -> tuple[PdvApplication, bool]:
        if self.transient is not None and self.transient.close_error is not None:
            raise self.transient.close_error
        if self.shared is not None and self.shared.close_error is not None:
            raise self.shared.close_error
        if fresh:
            self.close_shared()
            self.transient = self._start(login_required)
            return self.transient, True
        if self.shared is None:
            self.shared = self._start(login_required)
        return self.shared, False

    def release_shared(self) -> None:
        if self.shared is not None:
            if self.shared.close_error is not None:
                raise self.shared.close_error
            try:
                self.shared.reset_for_next_test(timeout=max(5.0, self.config.action_timeout))
                # Depois de F6/cancelamento o VCL pode manter TFrmPDV
                # desabilitado durante um repaint curto. Não confundir essa
                # janela transitória com contaminação de estado; aguardar o
                # mesmo estado pronto usado no setup.
                deadline = time.monotonic() + max(1.5, self.config.action_timeout)
                main = None
                while time.monotonic() < deadline:
                    candidate = self.shared._find_form("TFrmPDV", timeout=0.15)
                    if candidate is not None and self.shared._is_active_window(candidate):
                        main = candidate
                        break
                    time.sleep(0.1)
                if main is None:
                    raise AssertionError(
                        "Reset entre testes nao restaurou TFrmPDV visivel e habilitado"
                    )
                self.shared.maximize_window(main, self.config.window_mode)
                self.shared.window = main
            except Exception as exc:
                # Não tentar fechar/forçar o processo quando o reset deixou um
                # erro nativo visível ou TFrmPDV desabilitado. Nesse estado, um
                # close() adicional pode gerar o erro "objeto Eureka já
                # destruído" e perder a evidência da sequência original.
                # Preservar a instância torna o próximo acquire explicitamente
                # bloqueado, em vez de vazar estado corrompido silenciosamente.
                try:
                    diagnostics = self.shared.top_level_diagnostics()
                    unsafe = any(
                        item.get("class_name") in {"TFrmPDV", "#32770"}
                        and item.get("visible")
                        and (
                            item.get("class_name") == "#32770"
                            or not item.get("enabled")
                        )
                        for item in diagnostics
                    )
                except Exception:
                    diagnostics = []
                    unsafe = True
                if unsafe:
                    self.shared.close_error = PdvCloseError(
                        "Reset entre testes deixou a instância em estado inseguro; "
                        f"TFrmPDV/modal desabilitado. Causa original: {exc}. "
                        f"Diagnóstico: {diagnostics}"
                    )
                    raise
                try:
                    self.shared.close(reset=False)
                except Exception as close_exc:
                    self.shared.close_error = PdvCloseError(
                        f"Falha ao preservar/fechar instância após reset: {close_exc}"
                    )
                raise

    def close_shared(self) -> None:
        if self.shared is None:
            return
        if self.shared.close_error is not None:
            # The original failure already has evidence. Preserve ownership
            # and do not attempt a second cleanup during session teardown.
            return
        self.shared.close(reset=False)
        self.shared = None

    def close(self) -> None:
        if self.config.close_after_test:
            self.close_shared()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--product-code",
        action="store",
        default=None,
        help="Codigo do produto usado pelos cenarios genericos (1 a 33 por padrao).",
    )
    parser.addoption(
        "--pdv-par-qtde-maxima",
        action="store",
        default=None,
        help="Valor positivo esperado de PDVQTDEMAXIMA para PAR-01.",
    )
    parser.addoption(
        "--pdv-par-inverter-lista",
        action="store",
        default=None,
        help="Valor esperado de PDVINVERTERLISTADEPRODUTOS para PAR-02 (S).",
    )


def _par_values(pytest_config, test_config: TestConfig) -> tuple[str | None, str | None]:
    quantity = pytest_config.getoption("--pdv-par-qtde-maxima") or test_config.par_quantity_maxima
    invert = pytest_config.getoption("--pdv-par-inverter-lista") or test_config.par_inverter_lista
    return (str(quantity).strip() if quantity else None, str(invert).strip().upper() if invert else None)


def _par_block_reason(scenario_id: str, quantity: str | None, invert: str | None) -> str | None:
    if scenario_id == "PAR-01":
        if not quantity:
            return "PAR-01 bloqueado: PDV_PAR_QTDE_MAXIMA não foi definido no .env nem na CLI."
        try:
            if int(quantity) <= 0:
                return "PAR-01 bloqueado: PDV_PAR_QTDE_MAXIMA deve ser um inteiro positivo."
        except ValueError:
            return "PAR-01 bloqueado: PDV_PAR_QTDE_MAXIMA deve ser numérico."
    if scenario_id == "PAR-02":
        # PAR-02 tem skip explícito no teste porque a alteração deve ser feita
        # no módulo Parâmetros do Sistema, fora do escopo deste ambiente. Não
        # converter a ausência de variável em BLOCKED: o caso nem deve
        # materializar esta fixture quando o marker skip for aplicado.
        return None
    return None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """Bloqueios são inventariados antes das fixtures, sem virar SKIPPED.

    trylast aplica o filtro depois de -k/-m. --collect-only continua exibindo
    os casos para auditoria, sem criar resultado de execução.
    """
    if config.option.collectonly:
        return
    blocked, runnable = [], []
    static_config = load_config(Path(str(config.rootpath)))
    quantity, invert = _par_values(config, static_config)
    for item in items:
        par_marker = item.get_closest_marker("par_configuration")
        if par_marker is not None:
            scenario_id = str(par_marker.args[0])
            reason = _par_block_reason(scenario_id, quantity, invert)
            if reason:
                item.add_marker(pytest.mark.blocked(reason=reason))
        marker = item.get_closest_marker("blocked")
        if marker is None:
            runnable.append(item)
            continue
        reason = marker.kwargs.get("reason", "Pré-condição/seletor pendente; consultar o caso.")
        record_blocked_case(item.nodeid, str(reason))
        blocked.append(item)
    if blocked:
        items[:] = runnable
        config.hook.pytest_deselected(items=blocked)


@pytest.fixture(scope="session")
def app_pool(test_config: TestConfig):
    pool = _AppPool(test_config)
    try:
        yield pool
    finally:
        pool.close()


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
def manager_credentials(test_config: TestConfig) -> tuple[str, str]:
    """Fornece credenciais reais de gerente sem hardcode ou log da senha."""
    if not test_config.manager_credentials_configured:
        pytest.skip("Credenciais de gerente nao configuradas no .env")
    return test_config.manager_credentials


@pytest.fixture
def par_parameters(request: pytest.FixtureRequest, test_config: TestConfig) -> dict[str, int | str]:
    """Resolve PAR parameters from CLI over .env, without querying the database."""
    quantity, invert = _par_values(request.config, test_config)
    scenario_id = request.node.get_closest_marker("par_configuration")
    scenario = str(scenario_id.args[0]) if scenario_id else "PAR"
    reason = _par_block_reason(scenario, quantity, invert)
    if reason:
        pytest.skip("BLOCKED: " + reason)
    if scenario == "PAR-01":
        assert quantity is not None
        return {"quantity_maxima": int(quantity)}
    if scenario == "PAR-02":
        assert invert is not None
        return {"inverter_lista": invert}
    raise pytest.UsageError(f"Cenário de parâmetro não reconhecido: {scenario}")


@pytest.fixture
def evidence(request: pytest.FixtureRequest, test_config: TestConfig):
    name = request.node.name.replace("[", "_").replace("]", "_")
    directory = report_dir(test_config.report_root, name)
    logger = configure_logger(directory, name)
    set_dialog_context(request.node.nodeid)
    logger.info(json.dumps({"test": request.node.nodeid, "expected": "see test"}, ensure_ascii=False))
    return directory, logger


@pytest.fixture
def app(request: pytest.FixtureRequest, test_config: TestConfig, evidence, app_pool: _AppPool):
    if not test_config.allow_real_run:
        pytest.skip("PDV_ALLOW_REAL_RUN=false; execução real é opt-in")
    if not test_config.exe_path.exists():
        pytest.skip(f"SATPDV.exe não encontrado: {test_config.exe_path}")
    if not test_config.credentials_configured:
        pytest.skip("Credenciais de teste não configuradas")

    directory, logger = evidence
    application: PdvApplication | None = None
    transient = False
    unknown_state = False
    try:
        fresh = request.node.get_closest_marker("fresh_instance") is not None
        application, transient = app_pool.acquire(fresh=fresh, login_required=True)
        yield application
    except Exception as exc:
        unknown_state = isinstance(exc, UnknownDialogError)
        directory, logger = evidence
        logger.exception("Falha na preparação/execução: %s", exc)
        controls = application.diagnostics() if application is not None else []
        capture_failure(application.window if application is not None else None, directory, request.node.name, {"error": str(exc), "controls": controls})
        raise
    finally:
        if application is not None:
            logger.info(json.dumps({"phase": "teardown", "windows": application.top_level_diagnostics()}, ensure_ascii=False))
            if application.close_error is not None:
                logger.error("Teardown suspenso: fechamento não confirmado; instância preservada.")
            elif transient:
                if test_config.close_after_test:
                    application.close(reset=False)
            elif not unknown_state:
                app_pool.release_shared()


@pytest.fixture
def pdv(app: PdvApplication, test_config: TestConfig) -> PdvPage:
    return PdvPage(app.window, test_config.action_timeout)


@pytest.fixture
def raw_app(request: pytest.FixtureRequest, test_config: TestConfig, evidence, app_pool: _AppPool):
    if not test_config.allow_real_run:
        pytest.skip("PDV_ALLOW_REAL_RUN=false; execução real é opt-in")
    if not test_config.exe_path.exists():
        pytest.skip(f"SATPDV.exe não encontrado: {test_config.exe_path}")
    directory, logger = evidence
    application: PdvApplication | None = None
    unknown_state = False
    try:
        # Raw login scenarios are intentionally isolated from the shared
        # authenticated process, especially invalid-login checks.
        application, _ = app_pool.acquire(fresh=True, login_required=False)
        application.dismiss_recovery_prompt(
            allow_recovery=request.node.get_closest_marker("recovery") is not None,
            timeout=test_config.action_timeout,
        )
        if test_config.login_required:
            # A inicializacao VCL pode deixar TFrmPDVCaixaFechado visivel antes
            # de aceitar o primeiro clique. O retry atua somente no formulario
            # conhecido e evita transformar uma race de abertura em falha de
            # credencial ou em interacao cega.
            login_dialog = None
            for _ in range(3):
                login_dialog = application.reveal_login_dialog()
                if login_dialog is not None:
                    break
                time.sleep(0.5)
            if login_dialog is None:
                raise AssertionError("A tela inicial não expôs o diálogo real de login após o clique")
            application.window = login_dialog
        yield application
    except Exception as exc:
        unknown_state = isinstance(exc, UnknownDialogError)
        directory, logger = evidence
        logger.exception("Falha no fluxo bruto: %s", exc)
        controls = application.diagnostics() if application is not None else []
        capture_failure(application.window if application is not None else None, directory, raw_app.__name__, {"error": str(exc), "controls": controls})
        raise
    finally:
        if application is not None:
            logger.info(json.dumps({"phase": "teardown", "windows": application.top_level_diagnostics()}, ensure_ascii=False))
            if test_config.close_after_test and not unknown_state:
                application.close(reset=False)
