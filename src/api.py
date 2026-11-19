"""Minimal client for Chess.com's Published Data API with an on-disk cache.

Every successful response is stored as JSON under ``data/raw/api/`` keyed by the
URL path, so re-running the pipeline never re-downloads anything. 404/410 are
cached as "missing" too (closed accounts, absent archives). Transient failures
(429 / 5xx / network errors) are retried with exponential backoff; if a URL keeps
failing, ``ApiError`` is raised and the caller decides whether to skip it.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE = "https://api.chess.com/pub"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "api"
USER_AGENT = "titled-tuesday-outcome-model/0.1 (research take-home)"
MISSING = {"__missing__": True}


class ApiError(RuntimeError):
    pass


class ChessComClient:
    def __init__(self, cache_dir: Path = CACHE_DIR, max_retries: int = 5, pause: float = 0.0):
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self.pause = pause
        self._local = threading.local()  # one requests.Session per thread
        self.n_network = 0

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = self._local.session = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
        return s

    def _cache_path(self, url: str) -> Path:
        path = urlparse(url).path.strip("/")
        return self.cache_dir / f"{path}.json"

    def get(self, url_or_path: str, transform=None, cache_path: Path | None = None) -> dict | None:
        """Return parsed JSON, or None if the resource does not exist (404/410).

        ``transform`` shrinks a response before it is cached (e.g. drop move text);
        the cache then only ever holds the transformed version. ``cache_path``
        overrides where it is stored."""
        url = url_or_path if url_or_path.startswith("http") else f"{BASE}/{url_or_path.lstrip('/')}"
        cp = cache_path or self._cache_path(url)
        if cp.exists():
            data = json.loads(cp.read_text())
            return None if data == MISSING else data
        raw_cp = self._cache_path(url)
        if transform and raw_cp != cp and raw_cp.exists():
            # an older run cached the full response: convert it and free the space
            data = json.loads(raw_cp.read_text())
            data = MISSING if data == MISSING else transform(data)
            self._write(cp, data)
            raw_cp.unlink()
            return None if data == MISSING else data

        delay = 2.0
        last_err = ""
        for _ in range(self.max_retries):
            try:
                r = self.session.get(url, timeout=60)
                self.n_network += 1
            except requests.RequestException as e:
                last_err = repr(e)
            else:
                if r.status_code == 200:
                    data = r.json()
                    if transform:
                        data = transform(data)
                    self._write(cp, data)
                    if self.pause:
                        time.sleep(self.pause)
                    return data
                if r.status_code in (404, 410):
                    self._write(cp, MISSING)
                    return None
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code not in (429,) and r.status_code < 500:
                    break  # other 4xx: retrying will not help
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise ApiError(f"{url} -> {last_err}")

    @staticmethod
    def _write(cp: Path, data) -> None:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(cp)
