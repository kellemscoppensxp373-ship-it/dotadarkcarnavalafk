"""
loader.py — Hot-swappable external script host.

This is the mechanism that makes the architecture "live": the compiled ``.exe`` contains
only the shell, and every line of business logic is read from raw ``.py`` files sitting in
``scripts/`` next to it. Edit a file (or push one over OTA) and hit Reload — the running
process picks it up in milliseconds, with no restart and no lost GUI state.

Implementation notes
--------------------
* The scripts directory is prepended to ``sys.path`` so the external modules can import
  each other with plain ``import vision`` statements — exactly as they would in a normal
  package, which keeps them readable and editable by non-experts.
* Reload order matters: modules are reloaded leaf-first (dependencies before dependents)
  so a dependent never binds to a stale object.
* Every load is syntax-checked with ``compile()`` *before* it is executed, and a failed
  reload automatically rolls the file back to the last-known-good snapshot, so a bad OTA
  push can never brick the running bot.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import shutil
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

log = logging.getLogger("dc.loader")

#: Leaf-first dependency order. ``vision`` has no sibling deps; ``executor`` needs all.
DEFAULT_ORDER: tuple[str, ...] = (
    "vision",
    "input_handler",
    "learning",
    "dota_logic",
    "executor",
)


class ScriptError(RuntimeError):
    """Raised when an external script cannot be compiled or imported."""


@dataclass
class LoadResult:
    name: str
    ok: bool
    error: str = ""
    duration: float = 0.0
    source_hash: str = ""


@dataclass
class ScriptHost:
    """Owns the lifecycle of the external ``scripts/`` modules."""

    directory: Path
    order: Sequence[str] = DEFAULT_ORDER
    backups: Path | None = None
    modules: dict[str, ModuleType] = field(default_factory=dict)
    on_log: Callable[[str, str], None] = lambda level, msg: None

    def __post_init__(self) -> None:
        self.directory = Path(self.directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._ensure_on_path()

    # -- path management -----------------------------------------------------------

    def _ensure_on_path(self) -> None:
        p = str(self.directory)
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)

    def _emit(self, level: str, msg: str) -> None:
        getattr(log, level, log.info)(msg)
        try:
            self.on_log(level, msg)
        except Exception:  # pragma: no cover
            pass

    def path_for(self, name: str) -> Path:
        return self.directory / f"{name}.py"

    def exists(self, name: str) -> bool:
        return self.path_for(name).is_file()

    def missing(self) -> list[str]:
        return [n for n in self.order if not self.exists(n)]

    def source_of(self, name: str) -> str:
        return self.path_for(name).read_text(encoding="utf-8")

    def digest(self, name: str) -> str:
        import hashlib

        path = self.path_for(name)
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

    # -- validation & backups ------------------------------------------------------

    @staticmethod
    def validate_source(source: str, name: str = "<script>") -> None:
        """Syntax-check source before it is ever written or executed."""
        try:
            compile(source, f"{name}.py", "exec")
        except SyntaxError as exc:
            raise ScriptError(
                f"syntax error in {name}.py line {exc.lineno}: {exc.msg}"
            ) from exc

    def snapshot(self, name: str) -> Path | None:
        """Copy the current file into the backups dir; returns the snapshot path."""
        src = self.path_for(name)
        if not src.is_file() or self.backups is None:
            return None
        self.backups.mkdir(parents=True, exist_ok=True)
        dst = self.backups / f"{name}.{time.strftime('%Y%m%d-%H%M%S')}.py"
        shutil.copy2(src, dst)
        self._prune_backups(name)
        return dst

    def _prune_backups(self, name: str, keep: int = 10) -> None:
        if self.backups is None:
            return
        snaps = sorted(self.backups.glob(f"{name}.*.py"))
        for old in snaps[:-keep]:
            try:
                old.unlink()
            except OSError:  # pragma: no cover
                pass

    def latest_backup(self, name: str) -> Path | None:
        if self.backups is None:
            return None
        snaps = sorted(self.backups.glob(f"{name}.*.py"))
        return snaps[-1] if snaps else None

    def rollback(self, name: str) -> bool:
        """Restore the most recent snapshot of ``name`` and reload it."""
        snap = self.latest_backup(name)
        if snap is None:
            self._emit("warning", f"no backup available for {name}.py")
            return False
        shutil.copy2(snap, self.path_for(name))
        self._emit("info", f"rolled {name}.py back to {snap.name}")
        return self.reload(name).ok

    # -- writing (OTA / Dev Console entry point) -----------------------------------

    def write_script(self, name: str, source: str, *, reload: bool = True) -> LoadResult:
        """Validate → snapshot → overwrite → reload, with automatic rollback on failure."""
        self.validate_source(source, name)
        self.snapshot(name)
        target = self.path_for(name)
        tmp = target.with_suffix(".py.tmp")
        tmp.write_text(source, encoding="utf-8")
        tmp.replace(target)
        self._emit("info", f"wrote {name}.py ({len(source)} bytes)")
        if not reload:
            return LoadResult(name, True, source_hash=self.digest(name))
        result = self.reload(name)
        if not result.ok:
            self._emit("error", f"reload of {name}.py failed — rolling back")
            self.rollback(name)
        return result

    # -- loading -------------------------------------------------------------------

    def _import(self, name: str) -> ModuleType:
        """Import (or re-import) one script, always executing the *current* source.

        We deliberately do not use ``importlib.reload``: its ``SourceFileLoader`` honours
        the ``__pycache__`` entry, which is validated by (mtime, size) only. Hot-swapped
        scripts are routinely rewritten inside the same second *and* with an identical
        byte count (``VALUE = 1`` → ``VALUE = 7``), so a cached ``.pyc`` would silently
        win and the "reload" would be a no-op. Compiling the text ourselves makes a
        reload unconditionally authoritative.

        The existing module object is reused and re-executed in place (standard reload
        semantics), so sibling modules that already hold a reference to it observe the
        new definitions immediately.
        """
        path = self.path_for(name)
        if not path.is_file():
            raise ScriptError(f"missing external script: {path}")
        source = path.read_text(encoding="utf-8")
        self.validate_source(source, name)
        self._ensure_on_path()
        importlib.invalidate_caches()

        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None:  # pragma: no cover - defensive
            raise ScriptError(f"cannot build import spec for {path}")

        existing = sys.modules.get(name)
        is_new = existing is None
        module = importlib.util.module_from_spec(spec) if is_new else existing

        module.__spec__ = spec
        module.__loader__ = spec.loader
        module.__file__ = str(path)
        module.__name__ = name

        if not is_new:
            # Purge stale globals so a hot-swap is *authoritative*: if the new source
            # deletes a function or constant, it must actually disappear. Plain
            # ``importlib.reload`` leaves removed names behind, which silently keeps
            # dead logic alive after a rollback or an OTA push.
            for key in [k for k in module.__dict__ if not k.startswith("__")]:
                del module.__dict__[key]

        code = compile(source, str(path), "exec")
        sys.modules[name] = module
        try:
            exec(code, module.__dict__)
        except Exception:
            if is_new:
                sys.modules.pop(name, None)
            raise
        self.modules[name] = module
        return module

    def load(self, name: str) -> LoadResult:
        started = time.perf_counter()
        try:
            self._import(name)
        except ScriptError as exc:
            self._emit("error", str(exc))
            return LoadResult(name, False, str(exc), time.perf_counter() - started)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self._emit("error", f"failed to import {name}.py — {detail}")
            log.debug(traceback.format_exc())
            return LoadResult(name, False, detail, time.perf_counter() - started)
        dt = time.perf_counter() - started
        return LoadResult(name, True, duration=dt, source_hash=self.digest(name))

    def reload(self, name: str) -> LoadResult:
        """Reload one module, then every module that depends on it (cascade)."""
        result = self.load(name)
        if not result.ok:
            return result
        try:
            idx = list(self.order).index(name)
        except ValueError:
            return result
        for dependent in list(self.order)[idx + 1:]:
            if dependent in self.modules or self.exists(dependent):
                cascade = self.load(dependent)
                if not cascade.ok:
                    return cascade
        return result

    def load_all(self) -> list[LoadResult]:
        """Import every script leaf-first. Always returns one result per module."""
        results: list[LoadResult] = []
        for name in self.order:
            results.append(self.load(name))
        ok = sum(1 for r in results if r.ok)
        self._emit("info" if ok == len(results) else "error",
                   f"loaded {ok}/{len(results)} external scripts from {self.directory}")
        return results

    def reload_all(self) -> list[LoadResult]:
        """Full hot-swap: drop every script module and re-import from disk."""
        for name in self.order:
            sys.modules.pop(name, None)
        self.modules.clear()
        importlib.invalidate_caches()
        return self.load_all()

    # -- accessors -----------------------------------------------------------------

    def get(self, name: str) -> ModuleType:
        mod = self.modules.get(name)
        if mod is None:
            raise ScriptError(f"script {name!r} is not loaded")
        return mod

    @property
    def healthy(self) -> bool:
        return all(n in self.modules for n in self.order)

    def status(self) -> dict[str, dict]:
        return {
            name: {
                "loaded": name in self.modules,
                "exists": self.exists(name),
                "hash": self.digest(name),
                "path": str(self.path_for(name)),
            }
            for name in self.order
        }


__all__ = ["ScriptHost", "LoadResult", "ScriptError", "DEFAULT_ORDER"]
