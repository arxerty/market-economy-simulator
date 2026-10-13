"""FastAPI application: REST control plane + WebSocket event stream."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from ..analysis import compare, summarize
from ..config import ExperimentConfig, PolicyAction, deep_merge, load_config
from ..llm.base import available_providers
from .controller import RunController, RunManager, build_history

BACKEND_DIR = Path(__file__).resolve().parents[2]
EXPERIMENTS_DIR = Path(os.environ.get("MARKET_SIM_EXPERIMENTS", BACKEND_DIR / "experiments"))
RUNS_DIR = Path(os.environ.get("MARKET_SIM_RUNS_DIR", BACKEND_DIR / "data" / "runs"))


class CreateRun(BaseModel):
    experiment: Optional[str] = None  # file name in the experiments directory
    config: Optional[dict[str, Any]] = None  # inline config (takes precedence)
    overrides: dict[str, Any] = {}
    autostart: bool = False


class Speed(BaseModel):
    delay_ms: int


def create_app(runs_dir: Path = RUNS_DIR, experiments_dir: Path = EXPERIMENTS_DIR) -> FastAPI:
    manager = RunManager(runs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        runs_dir.mkdir(parents=True, exist_ok=True)
        yield
        await manager.shutdown()

    app = FastAPI(title="Market Economy Simulator", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.manager = manager

    def _experiment_path(name: str) -> Path:
        path = (experiments_dir / name).resolve()
        if path.parent != experiments_dir.resolve() or path.suffix not in (".yaml", ".yml", ".json") or not path.exists():
            raise HTTPException(404, f"experiment '{name}' not found")
        return path

    def _ctl(run_id: str) -> RunController:
        ctl = manager.get(run_id)
        if ctl is None:
            raise HTTPException(404, f"run '{run_id}' is not live (finished runs are read-only)")
        return ctl

    def _events(run_id: str) -> list[dict[str, Any]]:
        try:
            return manager.events(run_id)
        except FileNotFoundError:
            raise HTTPException(404, f"run '{run_id}' not found") from None

    # ----------------------------------------------------------------- meta
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "providers": available_providers(), "live_runs": len(manager.live)}

    @app.get("/api/experiments")
    async def list_experiments() -> list[dict[str, Any]]:
        out = []
        for p in sorted(experiments_dir.glob("*.y*ml")) + sorted(experiments_dir.glob("*.json")):
            try:
                cfg = load_config(p)
            except (ValidationError, ValueError, yaml.YAMLError) as exc:
                out.append({"file": p.name, "error": str(exc).splitlines()[0]})
                continue
            out.append({
                "file": p.name, "name": cfg.name, "description": cfg.description,
                "experiment_type": cfg.experiment_type, "rounds": cfg.rounds, "seed": cfg.seed,
                "goods": [g.id for g in cfg.goods], "provider": cfg.llm.provider, "model": cfg.llm.model,
                "agents": sum(g.count for g in cfg.agents),
            })
        return out

    @app.get("/api/experiments/{name}")
    async def get_experiment(name: str) -> dict[str, Any]:
        path = _experiment_path(name)
        return {"file": name, "raw": path.read_text(encoding="utf-8"), "config": load_config(path).model_dump()}

    # ----------------------------------------------------------------- runs
    @app.post("/api/runs")
    async def create_run(body: CreateRun) -> dict[str, Any]:
        try:
            if body.config is not None:
                cfg = ExperimentConfig.model_validate(deep_merge(body.config, body.overrides))
            elif body.experiment:
                cfg = load_config(_experiment_path(body.experiment), body.overrides)
            else:
                raise HTTPException(400, "provide 'experiment' or 'config'")
            ctl = manager.create(cfg)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if body.autostart:
            await ctl.start()
        return ctl.status()

    @app.get("/api/runs")
    async def list_runs() -> list[dict[str, Any]]:
        return manager.list_runs()

    @app.get("/api/runs/{run_id}")
    async def run_status(run_id: str) -> dict[str, Any]:
        ctl = manager.get(run_id)
        if ctl is not None:
            return {**ctl.status(), "state": ctl.sim.state()}
        events = _events(run_id)
        last = max((e["round"] for e in events), default=0)
        return {"run_id": run_id, "running": False, "finished": any(e["type"] == "run_finished" for e in events),
                "round": last, "live": False}

    @app.get("/api/runs/{run_id}/history")
    async def run_history(run_id: str) -> dict[str, Any]:
        ctl = manager.get(run_id)
        if ctl is not None:
            return ctl.history()
        events = _events(run_id)
        status = {"run_id": run_id, "running": False, "live": False,
                  "finished": any(e["type"] == "run_finished" for e in events),
                  "round": max((e["round"] for e in events), default=0)}
        start = events[0]["data"]
        status["rounds"] = start["config"]["rounds"]
        return build_history(events, status)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = -1, types: Optional[str] = None, limit: int = 5000) -> list[dict[str, Any]]:
        wanted = set(types.split(",")) if types else None
        return [e for e in _events(run_id) if e["seq"] > after and (wanted is None or e["type"] in wanted)][:limit]

    @app.post("/api/runs/{run_id}/start")
    async def start(run_id: str) -> dict[str, Any]:
        ctl = _ctl(run_id)
        await ctl.start()
        return ctl.status()

    @app.post("/api/runs/{run_id}/pause")
    async def pause(run_id: str) -> dict[str, Any]:
        ctl = _ctl(run_id)
        await ctl.pause()
        return ctl.status()

    @app.post("/api/runs/{run_id}/step")
    async def step(run_id: str) -> dict[str, Any]:
        ctl = _ctl(run_id)
        await ctl.step()
        return ctl.status()

    @app.post("/api/runs/{run_id}/stop")
    async def stop(run_id: str) -> dict[str, Any]:
        ctl = _ctl(run_id)
        await ctl.stop()
        return ctl.status()

    @app.post("/api/runs/{run_id}/speed")
    async def speed(run_id: str, body: Speed) -> dict[str, Any]:
        ctl = _ctl(run_id)
        ctl.set_speed(body.delay_ms)
        return ctl.status()

    @app.post("/api/runs/{run_id}/policy")
    async def policy(run_id: str, action: PolicyAction) -> dict[str, Any]:
        ctl = _ctl(run_id)
        if ctl.sim.finished:
            raise HTTPException(409, "run already finished")
        if action.good is not None and action.good not in ctl.sim.goods:
            raise HTTPException(400, f"unknown good '{action.good}'")
        ctl.queue_policy(action)
        return {"queued": action.model_dump(), "applies_at_round": ctl.sim.round + 1}

    @app.post("/api/runs/{run_id}/replay")
    async def replay(run_id: str, strict: bool = False, autostart: bool = False) -> dict[str, Any]:
        """Re-run a past experiment with the same config and seed, serving LLM answers from the cache."""
        events = _events(run_id)
        cfg = ExperimentConfig.model_validate(events[0]["data"]["config"])
        cfg.llm.cache.mode = "strict_replay" if strict else "replay"
        ctl = manager.create(cfg)
        if autostart:
            await ctl.start()
        return ctl.status()

    @app.get("/api/runs/{run_id}/summary")
    async def run_summary(run_id: str) -> dict[str, Any]:
        events = _events(run_id)
        if not any(e["type"] == "round_summary" for e in events):
            raise HTTPException(409, "run has no completed rounds yet")
        return summarize(events)

    @app.get("/api/compare")
    async def compare_runs(a: str, b: str) -> dict[str, Any]:
        ea, eb = _events(a), _events(b)
        for ev, rid in ((ea, a), (eb, b)):
            if not any(e["type"] == "round_summary" for e in ev):
                raise HTTPException(409, f"run '{rid}' has no completed rounds")
        sa, sb = summarize(ea), summarize(eb)
        return {**compare(sa, sb), "summary_a": _strip(sa), "summary_b": _strip(sb)}

    # ------------------------------------------------------------ websocket
    @app.websocket("/api/runs/{run_id}/ws")
    async def stream(ws: WebSocket, run_id: str) -> None:
        await ws.accept()
        ctl = manager.get(run_id)
        if ctl is None:
            await ws.send_json({"type": "error", "error": "run is not live"})
            await ws.close()
            return
        queue = ctl.subscribe()
        try:
            await ws.send_json({"type": "init", "history": ctl.history()})
            receiver = asyncio.create_task(_drain(ws))
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    getter.cancel()
                    break
                await ws.send_json(getter.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            ctl.unsubscribe(queue)

    return app


async def _drain(ws: WebSocket) -> None:
    """Consume (and ignore) client messages until the socket closes."""
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        return


def _strip(summary: dict[str, Any]) -> dict[str, Any]:
    return {**summary, "goods": {g: {k: v for k, v in d.items() if k != "series"} for g, d in summary["goods"].items()}}


app = create_app()
