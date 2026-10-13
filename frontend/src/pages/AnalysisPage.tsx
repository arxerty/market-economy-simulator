import { useEffect, useState } from "react";
import { api } from "../api";
import { SeriesChart } from "../components/Charts";
import type { RunMeta } from "../types";
import { fmt, pct, useColors } from "../theme";

export function RunSelect({ runs, value, onChange, label }: { runs: RunMeta[]; value: string; onChange: (v: string) => void; label: string }) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ maxWidth: 380 }}>
        <option value="">—</option>
        {runs.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {r.run_id} ({r.rounds_completed}/{r.rounds} rounds, {r.status})
          </option>
        ))}
      </select>
    </label>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

function KV({ rows }: { rows: [string, string | number | null | undefined][] }) {
  return (
    <table className="metrics">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>{k}</td>
            <td>{v === null || v === undefined ? "–" : v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TAX_KEYS: [string, string][] = [
  ["avg_volume", "Volume / round"],
  ["avg_price", "Market price"],
  ["avg_buyer_price", "Price paid by consumers"],
  ["avg_total_surplus", "Total surplus / round"],
  ["avg_consumer_surplus", "Consumer surplus / round"],
  ["avg_producer_surplus", "Producer surplus / round"],
  ["avg_tax_revenue", "Net tax revenue / round"],
  ["avg_dwl", "Deadweight loss / round"],
  ["theoretical_dwl", "Theoretical DWL / round"],
];

function describeAction(a: any): string {
  if (a.action === "set_tax") return `${a.tax_type === "ad_valorem" ? pct(a.rate, 0) : fmt(a.rate)} ${a.tax_type.replace("_", " ")} tax (${a.payer})`;
  if (a.action === "set_subsidy") return `subsidy ${fmt(a.value)}`;
  if (a.value !== undefined && a.value !== null) return `${a.action.replace(/_/g, " ")} ${fmt(a.value)}`;
  return a.action.replace(/_/g, " ");
}

export function AnalysisPage({ runId, onSelectRun }: { runId: string | null; onSelectRun: (id: string | null) => void }) {
  const c = useColors();
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.runs().then(setRuns).catch(() => {});
  }, []);
  useEffect(() => {
    setSummary(null);
    setErr(null);
    if (runId) api.summary(runId).then(setSummary).catch((e) => setErr(e.message));
  }, [runId]);

  return (
    <div>
      <div className="card row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
        <RunSelect runs={runs} value={runId ?? ""} onChange={(v) => onSelectRun(v || null)} label="Run" />
        {runId && (
          <button className="btn" onClick={() => api.summary(runId).then(setSummary)}>
            Refresh
          </button>
        )}
        {summary && (
          <span className="muted">
            {summary.name} · {summary.experiment_type} · seed {summary.seed} · {summary.rounds_completed}/{summary.rounds_planned} rounds
            {summary.finished ? "" : " (in progress)"}
          </span>
        )}
      </div>
      {err && <div className="error">{err}</div>}
      {!runId && <div className="empty">Pick a run to analyse.</div>}
      {summary &&
        Object.entries(summary.goods).map(([good, g]: [string, any]) => {
          const policyRounds = summary.policy_changes.filter((p: any) => p.good === good || p.good === null).map((p: any) => p.round);
          const b = g.bubble ?? {};
          const k = g.cartel ?? {};
          return (
            <div key={good}>
              <div className="section-title">{good}</div>
              <div className="tiles" style={{ marginBottom: 12 }}>
                <Tile label="Total surplus" value={fmt(g.total_surplus, 0)} sub={`efficient ${fmt(g.efficient_surplus, 0)}`} />
                <Tile label="Deadweight loss" value={fmt(g.deadweight_loss, 0)} sub={`efficiency ${pct(g.efficiency)}`} />
                <Tile label="Avg price" value={fmt(g.avg_price)} sub={`competitive ${fmt(g.competitive_price)} · monopoly ${fmt(g.monopoly_price)}`} />
                <Tile label="Price volatility" value={fmt(g.price_volatility, 4)} sub="σ of log returns" />
                <Tile label="HHI (sellers)" value={fmt(g.hhi_sellers, 0)} sub={`producers only ${fmt(g.hhi_producers, 0)}`} />
                <Tile label="Volume" value={fmt(g.total_volume, 0)} sub={`${fmt(g.avg_volume, 1)} / round · eq. ${g.competitive_qty}`} />
                <Tile label="Tax revenue" value={fmt(g.tax_revenue, 0)} sub={`subsidies ${fmt(g.subsidies, 0)}`} />
              </div>
              <div className="grid two">
                <div className="card">
                  <h3>Price vs fundamental</h3>
                  <SeriesChart
                    rounds={g.series.round}
                    policyRounds={policyRounds}
                    digits={0}
                    series={[
                      { key: "vwap", label: "Trade price (VWAP)", values: g.series.vwap, color: c["--series-1"] },
                      { key: "fund", label: "Fundamental", values: g.series.fundamental, color: c["--neutral-line"], dashed: true },
                      { key: "eq", label: "Equilibrium under policy", values: g.series.equilibrium, color: c["--series-2"], dashed: true },
                    ]}
                  />
                </div>
                <div className="card">
                  <h3>Realised surplus and deadweight loss per round</h3>
                  <SeriesChart
                    rounds={g.series.round}
                    policyRounds={policyRounds}
                    series={[
                      { key: "s", label: "Realised surplus", values: g.series.realized_surplus, color: c["--series-3"] },
                      { key: "d", label: "Deadweight loss", values: g.series.dwl, color: c["--series-2"] },
                    ]}
                  />
                </div>
              </div>

              <div className="grid three" style={{ marginTop: 12 }}>
                <div className="card">
                  <h3>Bubble</h3>
                  <KV
                    rows={[
                      ["Bubble detected", b.bubble_detected ? "yes" : "no"],
                      ["Relative deviation (RD)", pct(b.relative_deviation)],
                      ["Relative absolute deviation (RAD)", pct(b.relative_absolute_deviation)],
                      ["Max deviation", pct(b.max_deviation)],
                      ["Bubble start round", b.bubble_start_round],
                      ["Peak round / price", b.peak_round ? `${b.peak_round} / ${fmt(b.peak_price)}` : null],
                      ["Crash round (−20% from peak)", b.crash_round],
                      ["Crash depth", pct(b.crash_depth)],
                    ]}
                  />
                </div>
                <div className="card">
                  <h3>Market power / cartel</h3>
                  <KV
                    rows={[
                      ["Verdict", k.verdict],
                      ["Avg producer price", fmt(k.avg_producer_price)],
                      ["Avg marginal cost", fmt(k.avg_marginal_cost)],
                      ["Markup over marginal cost", pct(k.markup_over_mc)],
                      ["Lerner index", fmt(k.lerner_index, 3)],
                      ["Markup over competitive price", pct(k.markup_over_competitive)],
                      ["Collusion index (0 = comp., 1 = monopoly)", fmt(k.collusion_index, 3)],
                      ["Ask dispersion early → late", `${pct(k.ask_dispersion_early)} → ${pct(k.ask_dispersion_late)}`],
                      ["Explicit coordination messages", k.explicit_coordination_messages],
                    ]}
                  />
                  {k.example_messages?.length > 0 && (
                    <div style={{ marginTop: 8, fontSize: 12 }}>
                      {k.example_messages.map((m: string) => (
                        <div key={m} className="muted chan">
                          {m}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="card">
                  <h3>Wealth</h3>
                  <KV
                    rows={[
                      ["Gini (final wealth)", fmt(summary.wealth.gini, 3)],
                      ...Object.entries(summary.wealth.by_role).map(
                        ([r, v]: [string, any]) => [`${r} (${v.count}) mean`, fmt(v.mean, 0)] as [string, string],
                      ),
                      ["Consumer surplus", fmt(g.consumer_surplus, 0)],
                      ["Producer surplus", fmt(g.producer_surplus, 0)],
                      ["Speculator P&L", fmt(g.speculator_pnl, 0)],
                      ["Decisions by source", Object.entries(summary.decision_sources).map(([s, n]) => `${s} ${n}`).join(", ")],
                    ]}
                  />
                </div>
              </div>

              {g.tax_effects.length > 0 && (
                <div className="card" style={{ marginTop: 12 }}>
                  <h3>Policy changes: before vs after (10-round windows)</h3>
                  {g.tax_effects.map((t: any) => (
                    <div key={`${t.round}-${t.action.action}`} style={{ marginBottom: 14 }}>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>
                        Round {t.round}: {describeAction(t.action)} <span className="badge">{t.source}</span>
                      </div>
                      <table className="metrics">
                        <thead>
                          <tr>
                            <th>Metric</th>
                            <th>Before</th>
                            <th>After</th>
                            <th>Change</th>
                          </tr>
                        </thead>
                        <tbody>
                          {TAX_KEYS.map(([key, label]) => (
                            <tr key={key}>
                              <td>{label}</td>
                              <td>{fmt(t.before[key])}</td>
                              <td>{fmt(t.after[key])}</td>
                              <td className={t.delta[key] > 0 ? "pos" : t.delta[key] < 0 ? "neg" : ""}>
                                {t.delta[key] > 0 ? "+" : ""}
                                {fmt(t.delta[key])}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
    </div>
  );
}
