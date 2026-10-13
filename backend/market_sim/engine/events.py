"""Append-only event store (JSONL).

Every run writes ``<runs_dir>/<run_id>/events.jsonl``; one JSON object per line:
``{"seq": n, "round": r, "type": "...", "data": {...}}``. Events carry no wall-clock
timestamps so two runs with the same seed and cached LLM responses produce
byte-identical logs. Wall-clock metadata lives in ``meta.json`` instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

Listener = Callable[[dict[str, Any]], None]


class EventStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._listeners: list[Listener] = []
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def append(self, type_: str, round_no: int, data: dict[str, Any]) -> dict[str, Any]:
        event = {"seq": len(self.events), "round": round_no, "type": type_, "data": data}
        self.events.append(event)
        if self._fh is not None:
            self._fh.write(json.dumps(event, sort_keys=True, default=_default) + "\n")
            self._fh.flush()
        for fn in list(self._listeners):
            fn(event)
        return event

    def of_type(self, *types: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] in types]

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def read_events(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")
