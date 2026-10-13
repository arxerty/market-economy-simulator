import { useEffect, useState } from "react";
import { api } from "../api";
import { SeriesChart } from "../components/Charts";
import type { RunMeta } from "../types";
import { fmt, useColors } from "../theme";
import { RunSelect } from "./AnalysisPage";

const LABELS: Record<string, string> = {
  avg_price: "Average price",
  price_volatility: "Price volatility",
  total_volume: "Total volume",
  total_surplus: "Total surplus",
  deadweight_loss: "Deadweight loss",
  efficiency: "Efficiency",
  consumer_surplus: "Consumer surplus",
  producer_surplus: "Producer surplus",
  speculator_pnl: "Speculator P&L",
  tax_revenue: "Tax revenue",
  hhi_sellers: "HHI (sellers)",
  "bubble.relative_absolute_deviation": "Bubble: RAD",
  "bubble.max_deviation": "Bubble: max deviation",
  "bubble.peak_round": "Bubble: peak round",
  "bubble.crash_round": "Bubble: crash round",
  "cartel.markup_over_mc": "Markup over MC",
  "cartel.lerner_index": "Lerner index",
  "cartel.collusion_index": "Collusion index",
};

export function ComparePage({ a, b, onChange }: { a: string; b: string; onChange: (a: string, b: string) => void }) {
  const c = useColors();
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.runs().then(setRuns).catch(() => {});
  }, []);
  useEffect(() => {
    setRes(null);
    setErr(null);
    if (a && b) api.compare(a, b).then(setRes).catch((e) => setErr(e.message));
  }, [a, b]);

  return (
    <div>
      <div className="card row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
        <RunSelect runs={runs} value={a} onChange={(v) => onChange(v, b)} label="Run A" />
        <RunSelect runs={runs} value={b} onChange={(v) => onChange(a, v)} label="Run B" />
      </div>
      {err && <div className="error">{err}</div>}
      {!res && !err && <div className="empty">Pick two runs to compare. Runs need at least one shared good.</div>}
      {res && Object.keys(res.goods).length === 0 && <div className="empty">These runs share no goods.</div>}
      {res &&
        Object.entries(res.goods).map(([good, g]: [string, any]) => {
          const n = Math.max(g.series.a.round.length, g.series.b.round.length);
          const rounds = Array.from({ length: n }, (_, i) => i + 1);
          return (
            <div key={good}>
              <div className="section-title">{good}</div>
              <div className="grid two">
                <div className="card">
                  <h3>Metrics</h3>
                  <table className="metrics">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th title={res.a.run_id}>A · {res.a.name}</th>
                        <th title={res.b.run_id}>B · {res.b.name}</th>
                        <th>B − A</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(g.metrics).map(([k, v]: [string, any]) => (
                        <tr key={k}>
                          <td>{LABELS[k] ?? k}</td>
                          <td>{fmt(v.a, 3)}</td>
                          <td>{fmt(v.b, 3)}</td>
                          <td className={v.delta > 0 ? "pos" : v.delta < 0 ? "neg" : ""}>{v.delta === null ? "–" : `${v.delta > 0 ? "+" : ""}${fmt(v.delta, 3)}`}</td>
                        </tr>
                      ))}
                      <tr>
                        <td>Wealth Gini</td>
                        <td>{fmt(res.gini.a, 3)}</td>
                        <td>{fmt(res.gini.b, 3)}</td>
                        <td>{res.gini.a !== null && res.gini.b !== null ? fmt(res.gini.b - res.gini.a, 3) : "–"}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div className="grid" style={{ alignContent: "start" }}>
                  <div className="card">
                    <h3>Trade price (VWAP)</h3>
                    <SeriesChart
                      rounds={rounds}
                      series={[
                        { key: "a", label: "Run A", values: g.series.a.vwap, color: c["--series-1"] },
                        { key: "b", label: "Run B", values: g.series.b.vwap, color: c["--series-2"] },
                        { key: "f", label: "Fundamental (A)", values: g.series.a.fundamental, color: c["--neutral-line"], dashed: true },
                      ]}
                    />
                  </div>
                  <div className="card">
                    <h3>Realised surplus per round</h3>
                    <SeriesChart
                      rounds={rounds}
                      series={[
                        { key: "a", label: "Run A", values: g.series.a.realized_surplus, color: c["--series-1"] },
                        { key: "b", label: "Run B", values: g.series.b.realized_surplus, color: c["--series-2"] },
                      ]}
                    />
                  </div>
                </div>
              </div>
            </div>
          );
        })}
    </div>
  );
}
