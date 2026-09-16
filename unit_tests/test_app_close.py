"""Safety regression tests, NOT SATPDV route results; no real UI/process."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.app import PdvApplication, PdvCloseBlockedError, PdvCloseError
from tests.conftest import _AppPool


pytestmark = pytest.mark.unit
WARNING = "Tecle F3 para finalizar a venda ou F6 para cancelar a venda"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    process = Mock(process=123)
    window = Mock()
    window.process_id.return_value = 123
    window.is_visible.return_value = True
    window.window_text.return_value = "SATPDV"
    window.descendants.return_value = []
    app = PdvApplication(SimpleNamespace(report_root=tmp_path), process, window)
    monkeypatch.setattr(app, "_windows", Mock(return_value=[window]))
    monkeypatch.setattr(app, "_find_form", Mock(return_value=window))
    monkeypatch.setattr(app, "reset_for_next_test", Mock())
    observer = Mock(return_value={"text": WARNING})
    monkeypatch.setattr("core.test_results.record_dialog_observation", observer)
    capture = Mock(return_value=SimpleNamespace(
        log_path=tmp_path / "controls.log", screenshot_path=tmp_path / "screen.png",
    ))
    monkeypatch.setattr("core.app.capture_unknown_state", capture)
    return SimpleNamespace(app=app, process=process, window=window,
                           observer=observer, capture=capture)


def assert_preserved(rig):
    assert rig.app.process is rig.process
    assert rig.app.window is rig.window
    rig.process.kill.assert_not_called()
    rig.window.send_keystrokes.assert_not_called()


def test_success_clears_handles_without_kill(rig):
    rig.app.close()
    rig.app.reset_for_next_test.assert_called_once()
    rig.window.close.assert_called_once()
    assert rig.app.process is None and rig.app.window is None
    rig.process.kill.assert_not_called()


@pytest.mark.parametrize("on_main", [True, False], ids=["EditMsg", "modal"])
def test_known_warning_after_close_raises_and_records(rig, on_main, tmp_path):
    def closing():
        if on_main:
            rig.window.descendants.return_value = [Mock(window_text=Mock(return_value=WARNING))]
        else:
            dialog = Mock()
            dialog.process_id.return_value = 123
            dialog.is_visible.return_value = True
            dialog.window_text.return_value = "Atenção"
            dialog.descendants.return_value = [Mock(window_text=Mock(return_value=WARNING))]
            rig.app._windows.return_value = [rig.window, dialog]
    rig.window.close.side_effect = closing
    rig.process.wait_for_process_exit.side_effect = TimeoutError
    with pytest.raises(PdvCloseBlockedError, match="Fechamento bloqueado"):
        rig.app.close(reset=False, timeout=0)
    assert_preserved(rig)
    assert list(tmp_path.glob("*/close_failure_*.json"))
    rig.capture.assert_called_once()
    assert rig.observer.called


def test_preexisting_warning_prevents_reset_and_close(rig):
    rig.window.window_text.return_value = WARNING
    with pytest.raises(PdvCloseBlockedError):
        rig.app.close()
    rig.app.reset_for_next_test.assert_not_called()
    rig.window.close.assert_not_called()
    assert_preserved(rig)


def test_foreign_pid_warning_is_ignored(rig):
    foreign = Mock()
    foreign.process_id.return_value = 987
    foreign.window_text.return_value = WARNING
    rig.app._windows.return_value.append(foreign)
    rig.app.close(reset=False)
    foreign.window_text.assert_not_called()
    rig.process.kill.assert_not_called()


def test_unrecognized_timeout_is_explicit(rig):
    rig.process.wait_for_process_exit.side_effect = TimeoutError
    with pytest.raises(PdvCloseError, match="não confirmado"):
        rig.app.close(reset=False, timeout=0)
    assert_preserved(rig)


def test_destroyed_handle_error_but_process_exited_is_success(rig):
    rig.window.close.side_effect = RuntimeError("invalid handle")
    rig.app.close(reset=False)
    assert rig.app.process is None
    rig.window.send_keystrokes.assert_not_called()


def test_delayed_exit_is_polled_without_kill(rig):
    rig.process.wait_for_process_exit.side_effect = [TimeoutError(), None]
    rig.app.close(reset=False, timeout=1)
    assert rig.process.wait_for_process_exit.call_count == 2
    rig.process.kill.assert_not_called()


def test_failed_capture_still_preserves_process(rig):
    rig.capture.side_effect = OSError("capture unavailable")
    rig.window.window_text.return_value = WARNING
    with pytest.raises(PdvCloseBlockedError, match="Captura incompleta"):
        rig.app.close()
    assert_preserved(rig)


def test_latched_error_prevents_close_restart_and_reset(rig):
    error = PdvCloseBlockedError("preserve")
    rig.app.close_error = error
    for action in (rig.app.close, rig.app.start,
                   lambda: PdvApplication.reset_for_next_test(rig.app)):
        with pytest.raises(PdvCloseBlockedError) as caught:
            action()
        assert caught.value is error
    rig.window.close.assert_not_called()
    assert_preserved(rig)


@pytest.mark.parametrize("exists", [True, False])
def test_window_without_process_requires_disappearance(rig, exists):
    rig.app.process = None
    rig.window.exists.return_value = exists
    if exists:
        with pytest.raises(PdvCloseError):
            rig.app.close(reset=False)
        assert rig.app.window is rig.window
    else:
        rig.app.close(reset=False)
        assert rig.app.window is None
    rig.window.send_keystrokes.assert_not_called()


def test_pool_preserves_failed_instance_and_refuses_acquire(rig):
    pool = _AppPool(SimpleNamespace(close_after_test=True))
    pool.shared = rig.app
    rig.app.close_error = PdvCloseBlockedError("preserve")
    pool.close()
    assert pool.shared is rig.app
    for fresh in (False, True):
        with pytest.raises(PdvCloseBlockedError):
            pool.acquire(fresh=fresh)
    with pytest.raises(PdvCloseBlockedError):
        pool.release_shared()
    rig.app.reset_for_next_test.assert_not_called()
    rig.window.close.assert_not_called()


def test_pool_does_not_drop_reference_when_close_fails(rig):
    pool = _AppPool(SimpleNamespace(close_after_test=True))
    pool.shared = rig.app
    rig.window.window_text.return_value = WARNING
    with pytest.raises(PdvCloseBlockedError):
        pool.close_shared()
    assert pool.shared is rig.app
    assert_preserved(rig)


def test_failed_fresh_instance_prevents_next_launch(rig, monkeypatch):
    pool = _AppPool(SimpleNamespace(close_after_test=True))
    launch = Mock(return_value=rig.app)
    monkeypatch.setattr(pool, "_start", launch)
    instance, transient = pool.acquire(fresh=True)
    assert instance is rig.app and transient
    rig.app.close_error = PdvCloseBlockedError("preserve fresh")
    for fresh in (False, True):
        with pytest.raises(PdvCloseBlockedError):
            pool.acquire(fresh=fresh)
    launch.assert_called_once()
    assert pool.transient is rig.app
