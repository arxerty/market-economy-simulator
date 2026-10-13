import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { DepthChart, PriceChart, VolumeChart, WealthChart } from "../components/Charts";
import { PolicyPanel } from "../components/PolicyPanel";
import { ReasoningLog } from "../components/ReasoningLog";
import { useRunStream } from "../hooks/useRunStream";
import type { ExperimentInfo, RunMeta } from "../types";
import { fmt, pct } from "../theme";

export function LivePage({ runId, onSelectRun }: { runId: string | null; onSelectRun: (id: string | null) => void }) {
  const [experiments, setExperiments] = useState<ExperimentInfo[]>([]);
  const [providers, setProviders] = useState<string[]>(["mock"]);
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [exp, setExp] = useState("");
  const [provider, setProvider] = useState("");
  const [rounds, setRounds] = useState<string>("");
  const [seed, setSeed] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [good, setGood] = useState<string>("");

  const { history, status, rounds: data, log, connected, error, pendingPolicies } = useRunStream(runId);

  const refreshRuns = () => api.runs().then(setRuns).catch(() => {});
  useEffect(() => {
    api.experiments().then((e) => {
      setExperiments(e);
      if (e.length && !exp) setExp(e.find((x) => !x.error)?.file ?? "");
    });
    api.health().then((h) => setProviders(h.providers)).catch(() => {});
    refreshRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    refreshRuns();
  }, [runId, status?.finished]);

  const goods: string[] = useMemo(() => history?.config.goods.map((g: any) => g.id) ?? [], [history]);
  useEffect(() => {
    if (goods.length && !goods.includes(good)) setGood(goods[0]);
  }, [goods, good]);

  const selectedExp = experiments.find((e) => e.file === exp);

  const createRun = async (autostart: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const overrides: Record<string, unknown> = {};
      if (rounds) overrides.rounds = Number(rounds);
      if (seed) overrides.seed = Number(seed);
      if (provider) overrides.llm = { provider };
      const st = await api.createRun(exp, overrides, autostart);
      onSelectRun(st.run_id);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const latest = data.length ? data[data.length - 1] : undefined;
  const g = latest && good ? latest.goods[good] : undefined;
  const totals = useMemo(() => {
    if (!good) return null;
    let eff = 0,
      real = 0,
      vol = 0;
    for (const r of data) {
      const x = r.goods[good];
      if (!x) continue;
      eff += x.efficient_surplus;
      real += x.realized_surplus;
      vol += x.volume;
    }
    return { efficiency: eff > 0 ? real / eff : null, dwl: eff - real, volume: vol };
  }, [data, good]);
  const policyRounds = useMemo(
    () => Array.from(new Set(log.filter((e) => e.type === "policy_change").map((e) => e.round))),
    [log],
  );
  const live = status?.live ?? false;
  const finished = status?.finished ?? false;
  const totalRounds = status?.rounds ?? history?.config.rounds ?? 0;

  return (
    <div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field">
            Experiment
            <select value={exp} onChange={(e) => setExp(e.target.value)}>
              {experiments.map((e) => (
                <option key={e.file} value={e.file} disabled={!!e.error}>
                  {e.file}
                  {e.error ? " (invalid)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            LLM provider
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="">config default ({selectedExp?.provider ?? "?"})</option>
              {providers.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Rounds
            <input type="number" placeholder={String(selectedExp?.rounds ?? "")} value={rounds} onChange={(e) => setRounds(e.target.value)} style={{ width: 80 }} />
          </label>
          <label className="field">
            Seed
            <input type="number" placeholder={String(selectedExp?.seed ?? "")} value={seed} onChange={(e) => setSeed(e.target.value)} style={{ width: 80 }} />
          </label>
          <button className="btn" disabled={busy || !exp} onClick={() => createRun(false)}>
            New run
          </button>
          <button className="btn primary" disabled={busy || !exp} onClick={() => createRun(true)}>
            New run &amp; start
          </button>
          <span className="spacer" />
          <label className="field">
            Open run
            <select value={runId ?? ""} onChange={(e) => onSelectRun(e.target.value || null)} style={{ maxWidth: 340 }}>
              <option value="">—</option>
              {runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} · {r.live ? (r.running ? "running" : r.status === "finished" ? "finished" : "paused") : r.status}
                </option>
              ))}
            </select>
          </label>
        </div>
        {selectedExp?.description && <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>{selectedExp.description}</div>}
        {(err || error || status?.error) && <div className="error" style={{ marginTop: 6 }}>{err ?? error ?? status?.error}</div>}
      </div>

      {!runId && <div className="empty">Create a run or open an existing one.</div>}

      {runId && history && (
        <>
          <div className="card row" style={{ marginBottom: 12 }}>
            <button
              className="btn primary"
              disabled={!live || finished}
              onClick={() => act(() => (status?.running ? api.pause(runId) : api.start(runId)))}
            >
              {status?.running ? "Pause" : "Start"}
            </button>
            <button className="btn" disabled={!live || finished} onClick={() => act(() => api.step(runId))}>
              Step
            </button>
            <button className="btn danger" disabled={!live || finished} onClick={() => act(() => api.stop(runId))}>
              Stop
            </button>
            <label className="row" style={{ gap: 6, fontSize: 12 }}>
              Speed
              <input
                type="range"
                min={0}
                max={2000}
                step={50}
                value={2000 - (status?.delay_ms ?? 300)}
                disabled={!live || finished}
                onChange={(e) => act(() => api.speed(runId, 2000 - Number(e.target.value)))}
              />
              <span className="muted mono">{status?.delay_ms ?? "–"} ms/round</span>
            </label>
            <div className="progress" title={`${status?.round ?? 0} / ${totalRounds}`}>
              <div style={{ width: `${totalRounds ? (100 * (status?.round ?? 0)) / totalRounds : 0}%` }} />
            </div>
            <span className="mono">
              Round {status?.round ?? 0}/{totalRounds}
            </span>
            <span className="badge" title={live ? "Live WebSocket stream" : "Loaded from event log"}>
              <span className="dot" style={{ background: live ? (connected ? "var(--good)" : "var(--warning)") : "var(--text-muted)", marginRight: 4 }} />
              {live ? (connected ? "live" : "connecting") : "recorded"}
            </span>
            {finished && (
              <>
                <a className="btn" href={`#/analysis/${runId}`}>
                  Analyse
                </a>
                <button className="btn" onClick={() => act(async () => onSelectRun((await api.replay(runId)).run_id))} title="Re-run with the same seed, serving LLM answers from the response cache">
                  Replay
                </button>
              </>
            )}
            {goods.length > 1 && (
              <select value={good} onChange={(e) => setGood(e.target.value)} aria-label="Good">
                {goods.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            )}
          </div>

          <div className="tiles" style={{ marginBottom: 12 }}>
            <div className="tile">
              <div className="label">Last price · {good}</div>
              <div className="value">{fmt(g?.close)}</div>
              <div className="sub">fundamental {fmt(g?.fundamental)}</div>
            </div>
            <div className="tile">
              <div className="label">Deviation from fundamental</div>
              <div className="value">{g?.fundamental ? pct((g.close - g.fundamental) / g.fundamental) : "–"}</div>
              <div className="sub">competitive price {fmt(history.benchmarks[good]?.competitive.price)}</div>
            </div>
            <div className="tile">
              <div className="label">Volume this round</div>
              <div className="value">{fmt(g?.volume, 0)}</div>
              <div className="sub">total {fmt(totals?.volume, 0)} units</div>
            </div>
            <div className="tile">
              <div className="label">Allocative efficiency</div>
              <div className="value">{pct(totals?.efficiency)}</div>
              <div className="sub">deadweight loss {fmt(totals?.dwl, 0)}</div>
            </div>
            <div className="tile">
              <div className="label">Government</div>
              <div className="value">{fmt(latest?.government.balance, 0)}</div>
              <div className="sub">tax revenue {fmt(latest?.government.tax_revenue, 0)}</div>
            </div>
            <div className="tile">
              <div className="label">LLM calls</div>
              <div className="value">{fmt(latest?.llm.llm_calls, 0)}</div>
              <div className="sub">
                {fmt(latest?.llm.fallbacks, 0)} fallbacks · {fmt(latest?.llm.cache_hits, 0)} cached
              </div>
            </div>
          </div>

          <div className="grid live-grid">
            <div className="grid" style={{ alignContent: "start" }}>
              <div className="card">
                <h3>
                  Price · {good}
                  <span className="hint">purple marks = policy changes</span>
                </h3>
                {good && <PriceChart rounds={data} good={good} policyRounds={policyRounds} />}
              </div>
              <div className="card">
                <h3>Volume</h3>
                {good && <VolumeChart rounds={data} good={good} />}
              </div>
              <div className="grid two">
                <div className="card">
                  <h3>Order book depth</h3>
                  <DepthChart latest={g} />
                </div>
                <div className="card">
                  <h3>Wealth distribution</h3>
                  <WealthChart wealth={latest?.wealth} />
                </div>
              </div>
            </div>
            <div className="grid" style={{ alignContent: "start" }}>
              <div className="card">
                <h3>Regulator controls</h3>
                <PolicyPanel
                  runId={runId}
                  goods={goods}
                  disabled={!live || finished}
                  policies={g ? Object.fromEntries(goods.map((x) => [x, latest!.goods[x].policy])) : {}}
                  pending={pendingPolicies}
                />
              </div>
              <div className="card">
                <h3>
                  Agent reasoning &amp; actions
                  <span className="hint">{history.agents.length} agents</span>
                </h3>
                <ReasoningLog log={log} />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
