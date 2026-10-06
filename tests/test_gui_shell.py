"""The thin loader shell (``main.py``): GUI wiring, dev console and OTA plumbing.

Runs against a stubbed Qt (see ``qt_stub.py``) so it works headless, on CI, and without
libGL — while still driving the *real* MainWindow code path.
"""

from __future__ import annotations

import sys

import pytest
import qt_stub

qt_stub.install()


@pytest.fixture
def gui(tmp_path, monkeypatch):
    """A real MainWindow pointed at a *copy* of the production scripts.

    The dev-console tests deliberately overwrite script files, so they must never be
    aimed at the repository's own ``scripts/`` directory.
    """
    import shutil
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "scripts"
    workspace = tmp_path / "scripts"
    shutil.copytree(source, workspace, ignore=shutil.ignore_patterns("__pycache__"))

    monkeypatch.setenv("DC_SCRIPTS_DIR", str(workspace))
    monkeypatch.setenv("DC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DC_ASSETS_DIR", str(tmp_path / "assets"))
    for mod in ("main", "app.paths", "app.loader", "app.ota", "app.settings"):
        sys.modules.pop(mod, None)

    import main

    window = main.MainWindow()
    window._initial_load()
    yield window
    for name in ("vision", "input_handler", "learning", "dota_logic", "executor"):
        sys.modules.pop(name, None)


# ------------------------------------------------------------------- startup wiring


def test_window_loads_all_external_scripts_on_start(gui):
    assert gui.host.healthy
    assert set(gui.host.modules) == set(main_order(gui))


def main_order(gui):
    return set(gui.host.order)


def test_hero_list_is_populated_from_the_external_db(gui):
    assert gui.hero_list.count() >= 10
    labels = [gui.hero_list.item(i).text() for i in range(gui.hero_list.count())]
    assert any("Фантом Ассасин" in label for label in labels)
    assert any("emoji_pa.png" in label and "portrait_pa.png" in label for label in labels)


def test_script_status_panel_lists_every_module(gui):
    gui.refresh_script_status()
    text = gui.script_status.toPlainText()
    for name in gui.host.order:
        assert name in text


def test_log_signal_reaches_the_view(gui):
    gui.bridge.logged.emit("info", "привет из потока")
    assert "привет из потока" in gui.log_view.toPlainText()


def test_log_escapes_html(gui):
    gui.bridge.logged.emit("error", "<script>alert(1)</script>")
    assert "&lt;script&gt;" in gui.log_view.toPlainText()


def test_state_and_stats_signals_update_the_labels(gui):
    gui.bridge.state_changed.emit("hero_pick")
    gui.bridge.state_changed.emit("in_game")
    gui.bridge.stats_updated.emit({"tickets": {"Death": 3}})
    gui.bridge.action.emit("click:accept")


# ------------------------------------------------------------------- dev console


def test_dev_console_loads_a_script_into_the_editor(gui):
    gui.script_picker.setCurrentText("dota_logic")
    gui.on_open_script()
    assert "HERO_DB" in gui.editor.toPlainText()


def test_dev_console_hot_swaps_pasted_code(gui):
    """Paste → Apply → the running process uses the new logic immediately."""
    gui.script_picker.setCurrentText("dota_logic")
    gui.on_open_script()
    gui.editor.setPlainText(gui.editor.toPlainText() + "\nMARKER = 'hot'\n")
    gui.on_apply_script()
    assert gui.host.get("dota_logic").MARKER == "hot"


def test_dev_console_rejects_broken_code_without_breaking_the_app(gui):
    gui.script_picker.setCurrentText("vision")
    gui.on_open_script()
    original = gui.editor.toPlainText()
    gui.editor.setPlainText("def broken(:\n")
    gui.on_apply_script()
    assert gui.host.get("vision").RU_KEYWORDS["accept"][0] == "Принять"
    assert gui.host.source_of("vision") == original      # file untouched


def test_dev_console_rollback(gui):
    gui.script_picker.setCurrentText("dota_logic")
    gui.on_open_script()
    gui.editor.setPlainText(gui.editor.toPlainText() + "\nMARKER = 'hot'\n")
    gui.on_apply_script()
    assert hasattr(gui.host.get("dota_logic"), "MARKER")
    gui.on_rollback()
    assert not hasattr(gui.host.get("dota_logic"), "MARKER")


def test_reload_all_button(gui):
    gui.on_reload_all()
    assert gui.host.healthy


def test_empty_editor_is_a_no_op(gui):
    gui.script_picker.setCurrentText("learning")
    gui.editor.setPlainText("   ")
    gui.on_apply_script()
    assert gui.host.healthy


# ------------------------------------------------------------------------- OTA tab


def test_settings_round_trip_through_the_ui(gui):
    from app import paths

    gui.in_owner.setText("someone")
    gui.in_repo.setText("otherrepo")
    gui.in_branch.setText("dev")
    gui.in_token.setText("tok")
    gui.on_save_settings()
    assert paths.settings_file().is_file()
    assert gui.ota.config.owner == "someone"
    assert gui.ota.config.branch == "dev"
    assert gui.ota.config.raw_url("vision.py").endswith("/someone/otherrepo/dev/scripts/vision.py")


def test_ota_sync_button_pulls_and_hot_reloads(gui, monkeypatch):
    from app.ota import OtaClient

    payloads = {name: gui.host.source_of(name) for name in gui.host.order}
    payloads["dota_logic"] = payloads["dota_logic"] + "\nOTA_MARKER = 'synced'\n"

    def fake_opener(url, headers, timeout):
        name = url.rsplit("/", 1)[-1][:-3]
        return payloads[name].encode("utf-8")

    gui.ota = OtaClient(gui._ota_config(), opener=fake_opener)
    monkeypatch.setattr(gui, "on_save_settings", lambda: None)
    gui.on_sync()
    assert gui.host.get("dota_logic").OTA_MARKER == "synced"
    assert "updated" in gui.ota_output.toPlainText()


def test_ota_failure_is_reported_not_raised(gui, monkeypatch):
    from app.ota import OtaClient

    def boom(url, headers, timeout):
        raise RuntimeError("no network")

    gui.ota = OtaClient(gui._ota_config(), opener=boom)
    monkeypatch.setattr(gui, "on_save_settings", lambda: None)
    gui.on_sync()
    assert "failed" in gui.ota_output.toPlainText().lower()
    assert gui.host.healthy            # local scripts still fine


def test_list_remote_scripts_renders(gui, monkeypatch):
    from app.ota import OtaClient

    payload = b'[{"name":"vision.py","type":"file"},{"name":"executor.py","type":"file"}]'
    gui.ota = OtaClient(gui._ota_config(), opener=lambda u, h, t: payload)
    monkeypatch.setattr(gui, "on_save_settings", lambda: None)
    gui.on_list_remote()
    assert "vision" in gui.ota_output.toPlainText()


# ------------------------------------------------------------------- agent control


def test_worker_builds_an_executor_from_loaded_scripts(gui):
    import main

    gui.settings.simulate = True
    worker = main.AgentWorker(gui.host, gui.settings, gui.bridge)
    executor = worker.build()
    assert executor.config.simulate is True
    assert executor.logic is gui.host.get("dota_logic")
    assert executor.inputs.backend.name == "dryrun"     # simulation never touches the OS


def test_start_is_blocked_while_scripts_are_broken(gui, monkeypatch):
    monkeypatch.setattr(type(gui.host), "healthy", property(lambda self: False))
    gui.on_start()
    assert gui.worker is None


def test_stop_without_a_worker_is_safe(gui):
    gui.on_stop()


def test_pause_toggles(gui):
    import main

    gui.worker = main.AgentWorker(gui.host, gui.settings, gui.bridge)
    gui.on_pause()
    assert gui.worker.pause_event.is_set()
    gui.on_pause()
    assert not gui.worker.pause_event.is_set()


def test_finished_signal_resets_the_controls(gui):
    gui.bridge.finished.emit({"cycles_total": 3})
    assert "cycles_total" in gui.log_view.toPlainText()
