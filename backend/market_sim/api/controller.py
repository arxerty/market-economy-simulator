"""Run lifecycle for the API: start / pause / step / speed / policy, plus event fan-out."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from ..analysis import load_events, summarize
from ..config import ExperimentConfig, PolicyAction
from ..simulation import Simulation

_HISTORY_TYPES = ("round_summary", "decision", "message", "policy_change", "policy_rejected", "trade", "run_finished")


class RunController:
    def __init__(self, sim: Simulation, delay_ms: Optional[int] = None) -> None:
        self.sim = sim
        self.delay_ms = sim.config.round_delay_ms if delay_ms is None else delay_ms
        self.running = False
        self.error: Optional[str] = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._queues: set[asyncio.Queue] = set()
        self._closed = False
        sim.store.subscribe(self._on_event)

    # --------------------------------------------------------------- fan-out
    def _on_event(self, event: dict[str, Any]) -> None:
        if event["type"] == "run_started":
            return
        self._broadcast({"type": "event", "event": event})

    def _broadcast(self, msg: dict[str, Any]) -> None:
        for q in list(self._queues):
            q.put_nowait(msg)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def status(self) -> dict[str, Any]:
        return {
            "run_id": self.sim.run_id, "running": self.running, "finished": self.sim.finished,
            "round": self.sim.round, "rounds": self.sim.config.rounds, "delay_ms": self.delay_ms,
            "error": self.error, "live": True,
        }

    def _status_changed(self) -> None:
        self._broadcast({"type": "status", "status": self.status()})

    # -------------------------------------------------------------- controls
    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def start(self) -> None:
        if self.sim.finished:
            return
        self.running = True
        self._ensure_task()
        self._wake.set()
        self._status_changed()

    async def pause(self) -> None:
        self.running = False
        self._status_changed()

    async def step(self) -> Optional[dict[str, Any]]:
        if self.sim.finished:
            return None
        self.running = False
        async with self._lock:
            summary = await self._safe_step()
        self._status_changed()
        await self._maybe_close()
        return summary

    def set_speed(self, delay_ms: int) -> None:
        self.delay_ms = max(0, min(10_000, int(delay_ms)))
        self._wake.set()
        self._status_changed()

    def queue_policy(self, action: PolicyAction) -> None:
        self.sim.queue_policy(action, "user")
        self._broadcast({"type": "policy_queued", "action": action.model_dump()})

    async def stop(self) -> None:
        self.running = False
        if not self.sim.finished:
            self.sim.finish()
        await self._maybe_close()
        self._status_changed()

    async def _safe_step(self) -> Optional[dict[str, Any]]:
        try:
            return await self.sim.step()
        except Exception as exc:  # noqa: BLE001 - surface to UI instead of killing the server
            self.error = f"{type(exc).__name__}: {exc}"
            self.running = False
            self.sim.store.append("error", self.sim.round, {"error": self.error})
            return None

    async def _loop(self) -> None:
        while not self.sim.finished and not self._closed:
            if not self.running:
                self._wake.clear()
                await self._wake.wait()
                continue
            async with self._lock:
                if self.running and not self.sim.finished:
                    await self._safe_step()
            if self.sim.finished:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.delay_ms / 1000.0)
            except asyncio.TimeoutError:
                pass
        self.running = False
        await self._maybe_close()
        self._status_changed()

    async def _maybe_close(self) -> None:
        if self.sim.finished and not self._closed:
            self._closed = True
            await self.sim.close()

    async def shutdown(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
        if self._closed:
            return
        self._closed = True
        if not self.sim.finished:
            self.sim._write_meta(status="interrupted")
        await self.sim.close()

    # --------------------------------------------------------------- history
    def history(self) -> dict[str, Any]:
        return build_history(self.sim.store.events, self.status(), self.sim.state())


def build_history(events: list[dict[str, Any]], status: dict[str, Any], state: Optional[dict[str, Any]] = None,
                  max_log: int = 400) -> dict[str, Any]:
    start = next(e for e in events if e["type"] == "run_started")["data"]
    log_types = ("decision", "message", "policy_change", "policy_rejected", "error")
    log = [e for e in events if e["type"] in log_types][-max_log:]
    return {
        "status": status,
        "config": start["config"],
        "agents": start["agents"],
        "benchmarks": {g: {k: v for k, v in b.items() if k in ("competitive", "monopoly")} for g, b in start["benchmarks"].items()},
        "rounds": [e["data"] for e in events if e["type"] == "round_summary"],
        "log": log,
        "state": state,
    }


class RunManager:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self.live: dict[str, RunController] = {}

    def create(self, config: ExperimentConfig) -> RunController:
        config = config.model_copy(update={"runs_dir": str(self.runs_dir)})
        sim = Simulation(config)
        ctl = RunController(sim)
        self.live[sim.run_id] = ctl
        return ctl

    def get(self, run_id: str) -> Optional[RunController]:
        return self.live.get(run_id)

    def run_dir(self, run_id: str) -> Path:
        path = (self.runs_dir / run_id).resolve()
        if self.runs_dir.resolve() not in path.parents or not (path / "events.jsonl").exists():
            raise FileNotFoundError(run_id)
        return path

    def events(self, run_id: str) -> list[dict[str, Any]]:
        ctl = self.live.get(run_id)
        if ctl is not None:
            return list(ctl.sim.store.events)
        return load_events(self.run_dir(run_id))

    def summary(self, run_id: str) -> dict[str, Any]:
        return summarize(self.events(run_id))

    def list_runs(self) -> list[dict[str, Any]]:
        out = []
        if self.runs_dir.exists():
            for d in self.runs_dir.iterdir():
                meta_path = d / "meta.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        continue
                    ctl = self.live.get(meta["run_id"])
                    if ctl is not None:
                        meta.update(ctl.status())
                        meta["rounds_completed"] = ctl.sim.round
                    out.append(meta)
        return sorted(out, key=lambda m: m.get("run_id", ""), reverse=True)

    async def shutdown(self) -> None:
        for ctl in self.live.values():
            await ctl.shutdown()
