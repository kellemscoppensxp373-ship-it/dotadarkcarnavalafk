"""
ota.py — Over-the-air script delivery from GitHub.

Pulls raw ``.py`` files straight from a GitHub repository and hands them to the
:class:`~app.loader.ScriptHost`, which validates, snapshots and hot-reloads them. The
compiled ``.exe`` therefore never needs rebuilding to ship a logic fix: push to the
branch, hit *Sync from GitHub*, and the running bot is patched in place.

Only the standard library is used (``urllib``), so the frozen binary stays small and has
no TLS/vendoring surprises.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

log = logging.getLogger("dc.ota")

RAW_HOST = "https://raw.githubusercontent.com"
API_HOST = "https://api.github.com"
USER_AGENT = "DarkCarnivalRPA/1.0 (+https://github.com)"


@dataclass
class OtaConfig:
    """Where to pull scripts from."""

    owner: str = "kellemscoppensxp373-ship-it"
    repo: str = "dotadarkcarnavalafk"
    branch: str = "main"
    path: str = "scripts"          # folder inside the repo holding the .py files
    token: str = ""                # optional PAT for private repos / rate limits
    timeout: float = 20.0

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    def raw_url(self, filename: str) -> str:
        prefix = f"{self.path.strip('/')}/" if self.path.strip("/") else ""
        return f"{RAW_HOST}/{self.owner}/{self.repo}/{self.branch}/{prefix}{filename}"

    def api_contents_url(self) -> str:
        prefix = self.path.strip("/")
        return (f"{API_HOST}/repos/{self.owner}/{self.repo}/contents/{prefix}"
                f"?ref={urllib.parse.quote(self.branch)}")

    def as_dict(self) -> dict:
        return {"owner": self.owner, "repo": self.repo, "branch": self.branch,
                "path": self.path, "token": self.token}


@dataclass
class SyncResult:
    name: str
    ok: bool
    changed: bool = False
    error: str = ""
    size: int = 0


class OtaClient:
    """Fetches script sources over HTTPS. Injectable ``opener`` keeps tests offline."""

    def __init__(
        self,
        config: OtaConfig | None = None,
        opener: Callable[[str, dict, float], bytes] | None = None,
    ) -> None:
        self.config = config or OtaConfig()
        self._opener = opener or self._urlopen

    # -- transport -----------------------------------------------------------------

    @staticmethod
    def _urlopen(url: str, headers: dict, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            return resp.read()

    def _headers(self, accept: str = "text/plain") -> dict:
        headers = {"User-Agent": USER_AGENT, "Accept": accept,
                   "Cache-Control": "no-cache"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        return headers

    def _get(self, url: str, accept: str = "text/plain") -> bytes:
        try:
            return self._opener(url, self._headers(accept), self.config.timeout)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"network error for {url}: {exc.reason}") from exc

    # -- API -----------------------------------------------------------------------

    def fetch_source(self, name: str) -> str:
        """Download one script's raw source (``name`` without the ``.py``)."""
        filename = name if name.endswith(".py") else f"{name}.py"
        url = self.config.raw_url(filename)
        log.info("OTA fetch %s", url)
        return self._get(url).decode("utf-8")

    def list_remote_scripts(self) -> list[str]:
        """List the ``.py`` files present in the remote scripts folder."""
        raw = self._get(self.config.api_contents_url(), accept="application/vnd.github+json")
        entries = json.loads(raw.decode("utf-8"))
        if not isinstance(entries, list):  # pragma: no cover - error payload
            raise RuntimeError(f"unexpected GitHub response: {entries}")
        return sorted(
            e["name"][:-3] for e in entries
            if e.get("type") == "file" and e.get("name", "").endswith(".py")
            and not e["name"].startswith("__")
        )

    def sync(
        self,
        host,
        names: Sequence[str] | None = None,
        *,
        reload: bool = True,
        on_log: Callable[[str, str], None] = lambda level, msg: None,
    ) -> list[SyncResult]:
        """Pull ``names`` (default: the host's module order) and hot-swap them.

        Files are fetched *first* and only written once the whole batch is in hand, so a
        mid-download network failure cannot leave the scripts folder half-updated.
        """
        targets = list(names) if names else list(host.order)
        fetched: dict[str, str] = {}
        results: list[SyncResult] = []

        for name in targets:
            try:
                source = self.fetch_source(name)
                host.validate_source(source, name)
                fetched[name] = source
            except Exception as exc:
                msg = f"OTA fetch failed for {name}.py: {exc}"
                on_log("error", msg)
                log.warning(msg)
                results.append(SyncResult(name, False, error=str(exc)))

        for name, source in fetched.items():
            try:
                current = host.source_of(name) if host.exists(name) else ""
            except OSError:
                current = ""
            if current == source:
                on_log("info", f"{name}.py already up to date")
                results.append(SyncResult(name, True, changed=False, size=len(source)))
                continue
            try:
                host.write_script(name, source, reload=False)
                on_log("info", f"updated {name}.py ({len(source)} bytes)")
                results.append(SyncResult(name, True, changed=True, size=len(source)))
            except Exception as exc:  # pragma: no cover - disk failure
                on_log("error", f"could not write {name}.py: {exc}")
                results.append(SyncResult(name, False, error=str(exc)))

        if reload and any(r.changed for r in results):
            for res in host.reload_all():
                if not res.ok:
                    on_log("error", f"post-sync reload failed for {res.name}: {res.error}")
        return results


__all__ = ["OtaClient", "OtaConfig", "SyncResult"]
