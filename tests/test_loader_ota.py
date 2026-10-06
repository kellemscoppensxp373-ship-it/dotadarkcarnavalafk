"""Phase 1: hot-swappable architecture — ScriptHost reloads and GitHub OTA sync."""

from __future__ import annotations

import sys

import pytest

from app.loader import ScriptError, ScriptHost
from app.ota import OtaClient, OtaConfig


@pytest.fixture
def host(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "alpha.py").write_text("VALUE = 1\n", encoding="utf-8")
    (scripts / "beta.py").write_text("import alpha\nDERIVED = alpha.VALUE * 10\n", encoding="utf-8")
    h = ScriptHost(directory=scripts, order=("alpha", "beta"), backups=tmp_path / "backups")
    yield h
    for name in ("alpha", "beta"):
        sys.modules.pop(name, None)


# ----------------------------------------------------------------------- loading


def test_loads_external_scripts_from_disk(host):
    results = host.load_all()
    assert all(r.ok for r in results)
    assert host.get("alpha").VALUE == 1
    assert host.healthy


def test_sibling_imports_work_between_scripts(host):
    """`beta.py` does a plain `import alpha` — the scripts dir must be on sys.path."""
    host.load_all()
    assert host.get("beta").DERIVED == 10


def test_hot_swap_changes_behaviour_without_restart(host):
    host.load_all()
    assert host.get("alpha").VALUE == 1
    host.write_script("alpha", "VALUE = 42\n")
    assert host.get("alpha").VALUE == 42        # same process, new logic


def test_reload_cascades_to_dependent_modules(host):
    host.load_all()
    assert host.get("beta").DERIVED == 10
    host.write_script("alpha", "VALUE = 5\n")   # beta derives from alpha
    assert host.get("beta").DERIVED == 50


def test_reload_all_reimports_everything(host):
    host.load_all()
    host.path_for("alpha").write_text("VALUE = 99\n", encoding="utf-8")
    results = host.reload_all()
    assert all(r.ok for r in results)
    assert host.get("alpha").VALUE == 99


def test_missing_script_is_reported_not_raised(host):
    host.path_for("alpha").unlink()
    assert "alpha" in host.missing()
    result = host.load("alpha")
    assert not result.ok and "missing" in result.error


# -------------------------------------------------------------------- safety nets


def test_syntax_errors_are_rejected_before_writing(host):
    host.load_all()
    with pytest.raises(ScriptError, match="syntax error"):
        host.write_script("alpha", "def broken(:\n")
    assert host.source_of("alpha") == "VALUE = 1\n"      # file untouched
    assert host.get("alpha").VALUE == 1                   # process untouched


def test_runtime_error_in_new_code_triggers_rollback(host):
    host.load_all()
    result = host.write_script("alpha", "raise RuntimeError('boom')\n")
    assert not result.ok
    assert "boom" in result.error
    # The rollback restored the last-known-good module.
    assert host.get("alpha").VALUE == 1


def test_backups_are_created_and_pruned(host):
    host.load_all()
    for i in range(14):
        host.write_script("alpha", f"VALUE = {i}\n")
    snaps = sorted((host.backups).glob("alpha.*.py"))
    assert 0 < len(snaps) <= 10


def test_explicit_rollback_restores_previous_version(host):
    host.load_all()
    host.write_script("alpha", "VALUE = 2\n")
    assert host.rollback("alpha")
    assert host.get("alpha").VALUE == 1


def test_status_reports_hashes(host):
    host.load_all()
    status = host.status()
    assert status["alpha"]["loaded"] and status["alpha"]["hash"]
    before = status["alpha"]["hash"]
    host.write_script("alpha", "VALUE = 3\n")
    assert host.status()["alpha"]["hash"] != before


def test_get_unloaded_script_raises(host):
    with pytest.raises(ScriptError):
        host.get("alpha")


# --------------------------------------------------------------------------- OTA


def test_raw_url_construction():
    cfg = OtaConfig(owner="o", repo="r", branch="dev", path="scripts")
    assert cfg.raw_url("vision.py") == \
        "https://raw.githubusercontent.com/o/r/dev/scripts/vision.py"
    assert "contents/scripts?ref=dev" in cfg.api_contents_url()


def test_raw_url_with_repo_root_path():
    cfg = OtaConfig(owner="o", repo="r", branch="main", path="")
    assert cfg.raw_url("vision.py") == "https://raw.githubusercontent.com/o/r/main/vision.py"


def test_token_is_sent_as_bearer_header():
    seen = {}

    def opener(url, headers, timeout):
        seen.update(headers)
        return b"VALUE = 1\n"

    OtaClient(OtaConfig(token="secret"), opener=opener).fetch_source("alpha")
    assert seen["Authorization"] == "Bearer secret"


def test_sync_downloads_writes_and_reloads(host):
    host.load_all()
    remote = {"alpha": "VALUE = 7\n", "beta": "import alpha\nDERIVED = alpha.VALUE + 1\n"}

    def opener(url, headers, timeout):
        name = url.rsplit("/", 1)[-1][:-3]
        return remote[name].encode("utf-8")

    results = OtaClient(OtaConfig(), opener=opener).sync(host)
    assert all(r.ok for r in results)
    assert host.get("alpha").VALUE == 7
    assert host.get("beta").DERIVED == 8


def test_sync_skips_unchanged_files(host):
    host.load_all()
    source = host.source_of("alpha")

    def opener(url, headers, timeout):
        name = url.rsplit("/", 1)[-1][:-3]
        return (source if name == "alpha" else host.source_of(name)).encode("utf-8")

    results = OtaClient(OtaConfig(), opener=opener).sync(host)
    assert all(r.ok and not r.changed for r in results)


def test_sync_rejects_malformed_remote_code(host):
    host.load_all()

    def opener(url, headers, timeout):
        return b"def nope(:\n"

    results = OtaClient(OtaConfig(), opener=opener).sync(host)
    assert all(not r.ok for r in results)
    assert host.get("alpha").VALUE == 1          # local copy survived


def test_sync_is_atomic_across_a_partial_network_failure(host):
    """If one file fails to download, the healthy ones still apply, broken one doesn't."""
    host.load_all()

    def opener(url, headers, timeout):
        if url.endswith("beta.py"):
            raise RuntimeError("connection reset")
        return b"VALUE = 21\n"

    results = {r.name: r for r in OtaClient(OtaConfig(), opener=opener).sync(host)}
    assert results["alpha"].ok and results["alpha"].changed
    assert not results["beta"].ok
    assert host.get("alpha").VALUE == 21
    assert host.get("beta").DERIVED == 210       # cascaded reload from the good file


def test_list_remote_scripts_filters_to_python_files():
    payload = (b'[{"name":"vision.py","type":"file"},'
               b'{"name":"__init__.py","type":"file"},'
               b'{"name":"README.md","type":"file"},'
               b'{"name":"sub","type":"dir"}]')
    client = OtaClient(OtaConfig(), opener=lambda u, h, t: payload)
    assert client.list_remote_scripts() == ["vision"]


def test_network_failure_surfaces_a_clear_error():
    def opener(url, headers, timeout):
        raise RuntimeError("dns failure")

    with pytest.raises(RuntimeError, match="dns failure"):
        OtaClient(OtaConfig(), opener=opener).fetch_source("vision")


# ------------------------------------------------------- the real production scripts


def test_the_real_scripts_folder_loads_cleanly(tmp_path):
    """Smoke test: the shipped scripts/ dir imports under the production loader."""
    from pathlib import Path

    from app.loader import DEFAULT_ORDER

    real = Path(__file__).resolve().parent.parent / "scripts"
    host = ScriptHost(directory=real, order=DEFAULT_ORDER, backups=tmp_path / "bk")
    results = host.load_all()
    failed = [(r.name, r.error) for r in results if not r.ok]
    assert not failed, f"production scripts failed to load: {failed}"
    assert host.healthy
    assert host.get("dota_logic").HERO_DB
    assert host.get("vision").RU_KEYWORDS["accept"][0] == "Принять"


def test_reload_removes_names_deleted_from_the_source(host):
    """A hot-swap must be authoritative: deleted logic has to actually disappear."""
    host.load_all()
    host.write_script("alpha", "VALUE = 1\nTEMP = 'remove me'\n")
    assert host.get("alpha").TEMP == "remove me"
    host.write_script("alpha", "VALUE = 1\n")
    assert not hasattr(host.get("alpha"), "TEMP")


def test_module_identity_is_stable_across_reloads(host):
    """Sibling modules keep working because the module object is reused, not replaced."""
    host.load_all()
    before = host.get("alpha")
    host.write_script("alpha", "VALUE = 77\n")
    assert host.get("alpha") is before
    assert sys.modules["alpha"] is before
