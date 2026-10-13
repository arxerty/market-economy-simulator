"""SQLite response cache keyed by the full decision context.

The key is a SHA-256 over the provider, model, sampling settings, system prompt and
user prompt. Because the user prompt contains the agent's complete observation, a hit
means the agent saw exactly the same world, so replaying the cached answer reproduces
the original run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .base import LLMRequest


def cache_key(provider: str, request: LLMRequest) -> str:
    payload = {
        "provider": provider,
        "model": request.model,
        "temperature": request.temperature,
        "effort": request.effort,
        "max_tokens": request.max_tokens,
        "schema": request.response_schema,
        "system": request.system,
        "user": request.user,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


class ResponseCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, text TEXT NOT NULL, model TEXT, created INTEGER DEFAULT (strftime('%s','now')))"
        )
        self._db.commit()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT text FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return row[0]

    def put(self, key: str, text: str, model: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO responses (key, text, model) VALUES (?, ?, ?)", (key, text, model))
            self._db.commit()

    def __len__(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()
