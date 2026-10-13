import { useEffect, useMemo, useRef, useState } from "react";
import type { SimEvent } from "../types";
import { roleColor, useColors } from "../theme";

function describeActions(d: any): string {
  const r = d.result ?? {};
  if (d.kind === "policy") {
    const applied = (r.applied ?? []).map((a: any) => `${a.action} ${a.good ?? "all"} ${a.rate ?? a.value ?? ""}`.trim());
    return applied.length ? applied.join(", ") : "hold";
  }
  const orders = (r.orders ?? []).map(
    (o: any) => `${o.side === "buy" ? "BID" : "ASK"} ${o.quantity} ${o.good} @ ${o.price.toFixed(2)}`,
  );
  const cancels = (r.cancels ?? []).length;
  const parts = [...orders];
  if (cancels) parts.push(`cancel ${cancels}`);
  return parts.length ? parts.join(" · ") : "hold";
}

const ROLES = ["all", "buyer", "seller", "speculator", "regulator"] as const;

export function ReasoningLog({ log }: { log: SimEvent[] }) {
  const c = useColors();
  const [role, setRole] = useState<(typeof ROLES)[number]>("all");
  const [hideHeuristic, setHideHeuristic] = useState(false);
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);

  const entries = useMemo(() => {
    return log
      .filter((e) => {
        if (e.type !== "decision") return true;
        if (role !== "all" && e.data.role !== role) return false;
        if (hideHeuristic && e.data.source === "heuristic") return false;
        if (e.data.kind === "message") return false; // messages are shown via message events
        return true;
      })
      .slice(-400);
  }, [log, role, hideHeuristic]);

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [entries, follow]);

  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <select value={role} onChange={(e) => setRole(e.target.value as any)} aria-label="Filter by role">
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r === "all" ? "All roles" : r}
            </option>
          ))}
        </select>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          <input type="checkbox" checked={hideHeuristic} onChange={(e) => setHideHeuristic(e.target.checked)} />
          Hide heuristic agents
        </label>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          Follow
        </label>
      </div>
      <div className="log" ref={box}>
        {entries.length === 0 && <div className="empty">Agent decisions will stream here.</div>}
        {entries.map((e) => {
          if (e.type === "message") {
            return (
              <div className="entry message" key={e.seq}>
                <span className="r">R{e.round}</span>
                <div>
                  <div className="who">
                    <span className="dot" style={{ background: roleColor(c, "seller") }} />
                    {e.data.from} <span className="badge">channel</span>
                  </div>
                  <div className="why chan">“{e.data.text}”</div>
                </div>
              </div>
            );
          }
          if (e.type === "policy_change" || e.type === "policy_rejected") {
            const a = e.data.action;
            return (
              <div className="entry policy" key={e.seq}>
                <span className="r">R{e.round}</span>
                <div>
                  <div className="who">
                    <span className="dot" style={{ background: roleColor(c, "regulator") }} />
                    Policy {e.type === "policy_rejected" ? "rejected" : "change"} <span className="badge">{e.data.source}</span>
                  </div>
                  <div className="acts">
                    {a.action} {a.good ?? "all goods"} {a.action === "set_tax" ? `${a.tax_type} ${a.rate} (${a.payer} pays)` : a.value ?? ""}
                  </div>
                  {e.data.reason && <div className="rej">{e.data.reason}</div>}
                </div>
              </div>
            );
          }
          if (e.type === "error") {
            return (
              <div className="entry" key={e.seq}>
                <span className="r">R{e.round}</span>
                <div className="error">{e.data.error}</div>
              </div>
            );
          }
          const d = e.data;
          const rejected = d.result?.rejected?.length ?? 0;
          return (
            <div className="entry" key={e.seq}>
              <span className="r">R{e.round}</span>
              <div>
                <div className="who">
                  <span className="dot" style={{ background: roleColor(c, d.role) }} />
                  {d.agent}
                  <span className={`badge ${d.source}`} title={(d.errors ?? []).join("\n")}>
                    {d.source}
                  </span>
                </div>
                {d.reasoning && <div className="why">{d.reasoning}</div>}
                <div className="acts">{describeActions(d)}</div>
                {rejected > 0 && (
                  <div className="rej">
                    {rejected} rejected: {d.result.rejected.map((r: any) => r.reason).join("; ")}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
