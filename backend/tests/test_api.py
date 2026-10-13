import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from market_sim.api.app import create_app


@pytest.fixture
def client(tmp_path):
    exp = tmp_path / "experiments"
    exp.mkdir()
    cfg = {
        "name": "api", "seed": 5, "rounds": 6, "round_delay_ms": 0,
        "goods": [{"id": "w", "initial_price": 90}],
        "llm": {"provider": "mock", "cache": {"mode": "off"}},
        "agents": [
            {"role": "buyer", "count": 4, "cash": 1000, "valuations": {"w": {"dist": "uniform", "low": 60, "high": 140}}, "units_per_round": 2},
            {"role": "seller", "count": 2, "costs": {"w": {"dist": "uniform", "low": 40, "high": 90}}, "capacity": {"w": 4}},
        ],
    }
    (exp / "tiny.yaml").write_text(yaml.safe_dump(cfg))
    (exp / "broken.yaml").write_text("name: x\ngoods: []\nagents: [{role: wizard}]\n")
    app = create_app(runs_dir=tmp_path / "runs", experiments_dir=exp)
    with TestClient(app) as c:
        yield c


def wait_for(client, run_id, pred, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/runs/{run_id}").json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError("timeout waiting for run")


def test_experiments_listing(client):
    exps = {e["file"]: e for e in client.get("/api/experiments").json()}
    assert exps["tiny.yaml"]["agents"] == 6
    assert "error" in exps["broken.yaml"]
    assert client.get("/api/experiments/../secret.yaml").status_code == 404


def test_step_policy_and_summary(client):
    run = client.post("/api/runs", json={"experiment": "tiny.yaml"}).json()
    rid = run["run_id"]
    assert run["round"] == 0 and not run["running"]
    assert client.post(f"/api/runs/{rid}/step").json()["round"] == 1
    r = client.post(f"/api/runs/{rid}/policy", json={"action": "set_tax", "good": "w", "rate": 5})
    assert r.json()["applies_at_round"] == 2
    assert client.post(f"/api/runs/{rid}/policy", json={"action": "set_tax", "good": "zzz", "rate": 5}).status_code == 400
    client.post(f"/api/runs/{rid}/step")
    hist = client.get(f"/api/runs/{rid}/history").json()
    assert len(hist["rounds"]) == 2
    assert any(e["type"] == "policy_change" for e in hist["log"])
    assert hist["rounds"][1]["goods"]["w"]["policy"]["per_unit_tax"] == 5
    summary = client.get(f"/api/runs/{rid}/summary").json()
    assert summary["rounds_completed"] == 2


def test_start_runs_to_completion_and_compare(client):
    a = client.post("/api/runs", json={"experiment": "tiny.yaml", "autostart": True}).json()["run_id"]
    b = client.post("/api/runs", json={"experiment": "tiny.yaml", "overrides": {"seed": 9}, "autostart": True}).json()["run_id"]
    for rid in (a, b):
        wait_for(client, rid, lambda s: s["finished"])
    runs = {r["run_id"]: r for r in client.get("/api/runs").json()}
    assert runs[a]["status"] == "finished"
    cmp = client.get(f"/api/compare?a={a}&b={b}").json()
    assert "total_surplus" in cmp["goods"]["w"]["metrics"]
    assert client.post(f"/api/runs/{a}/policy", json={"action": "hold"}).status_code == 409


def test_pause_and_speed(client):
    rid = client.post("/api/runs", json={"experiment": "tiny.yaml"}).json()["run_id"]
    client.post(f"/api/runs/{rid}/speed", json={"delay_ms": 200})
    client.post(f"/api/runs/{rid}/start")
    st = wait_for(client, rid, lambda s: s["round"] >= 1)
    assert st["delay_ms"] == 200
    st = client.post(f"/api/runs/{rid}/pause").json()
    assert not st["running"]
    r0 = client.get(f"/api/runs/{rid}").json()["round"]
    time.sleep(0.5)
    assert client.get(f"/api/runs/{rid}").json()["round"] <= r0 + 1


def test_websocket_streams_events(client):
    rid = client.post("/api/runs", json={"experiment": "tiny.yaml"}).json()["run_id"]
    with client.websocket_connect(f"/api/runs/{rid}/ws") as ws:
        init = ws.receive_json()
        assert init["type"] == "init" and init["history"]["status"]["round"] == 0
        client.post(f"/api/runs/{rid}/step")
        seen = set()
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] == "event":
                seen.add(msg["event"]["type"])
                if msg["event"]["type"] == "round_summary":
                    break
        assert {"round_started", "decision", "round_summary"} <= seen


def test_inline_config_validation(client):
    r = client.post("/api/runs", json={"config": {"name": "x", "goods": [], "agents": [{"role": "wizard"}]}})
    assert r.status_code == 422
    assert client.get("/api/runs/does-not-exist/summary").status_code == 404


def test_replay_endpoint(client):
    rid = client.post("/api/runs", json={"experiment": "tiny.yaml", "autostart": True}).json()["run_id"]
    wait_for(client, rid, lambda s: s["finished"])
    rep = client.post(f"/api/runs/{rid}/replay?autostart=true").json()
    assert rep["run_id"] != rid
    wait_for(client, rep["run_id"], lambda s: s["finished"])
    sa = client.get(f"/api/runs/{rid}/summary").json()
    sb = client.get(f"/api/runs/{rep['run_id']}/summary").json()
    assert sa["goods"]["w"]["series"] == sb["goods"]["w"]["series"]
