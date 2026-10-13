"""Headless CLI.

    python -m market_sim run experiments/bubble.yaml [--rounds N] [--seed S] [--provider mock]
    python -m market_sim analyze data/runs/<run_id>
    python -m market_sim compare data/runs/<a> data/runs/<b>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .analysis import compare, load_events, summarize
from .config import load_config
from .simulation import Simulation


def _strip_series(summary: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(summary))
    for g in out.get("goods", {}).values():
        g.pop("series", None)
    out.get("wealth", {}).pop("final", None)
    return out


async def _run(args: argparse.Namespace) -> None:
    overrides: dict[str, Any] = {}
    if args.rounds:
        overrides["rounds"] = args.rounds
    if args.seed is not None:
        overrides["seed"] = args.seed
    if args.provider:
        overrides.setdefault("llm", {})["provider"] = args.provider
    if args.cache:
        overrides.setdefault("llm", {}).setdefault("cache", {})["mode"] = args.cache
    cfg = load_config(args.config, overrides)
    sim = Simulation(cfg)
    try:
        while not sim.finished:
            s = await sim.step()
            if not args.quiet:
                line = "  ".join(
                    f"{g}: close={v['close']:.2f} vol={v['volume']:>3} fund={v['fundamental'] or 0:.2f}"
                    for g, v in s["goods"].items()
                )
                print(f"round {s['round']:>3}  {line}")
    finally:
        await sim.close()
    summary = summarize(sim.store.events)
    print(json.dumps(_strip_series(summary), indent=2))
    print(f"\nrun saved to {sim.run_dir}")


def main() -> None:
    p = argparse.ArgumentParser(prog="market_sim")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("config")
    r.add_argument("--rounds", type=int)
    r.add_argument("--seed", type=int)
    r.add_argument("--provider")
    r.add_argument("--cache", choices=["off", "record", "replay", "strict_replay"])
    r.add_argument("--quiet", action="store_true")
    a = sub.add_parser("analyze")
    a.add_argument("run_dir")
    c = sub.add_parser("compare")
    c.add_argument("run_a")
    c.add_argument("run_b")
    args = p.parse_args()

    if args.cmd == "run":
        asyncio.run(_run(args))
    elif args.cmd == "analyze":
        print(json.dumps(_strip_series(summarize(load_events(Path(args.run_dir)))), indent=2))
    elif args.cmd == "compare":
        res = compare(summarize(load_events(Path(args.run_a))), summarize(load_events(Path(args.run_b))))
        for g in res["goods"].values():
            g.pop("series", None)
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
