import { useState } from "react";
import { api } from "../api";
import type { GoodPolicy, PolicyActionInput } from "../types";
import { fmt, pct } from "../theme";

type Action = PolicyActionInput["action"];

const ACTIONS: { value: Action; label: string }[] = [
  { value: "set_tax", label: "Set tax" },
  { value: "remove_tax", label: "Remove taxes" },
  { value: "set_subsidy", label: "Set subsidy" },
  { value: "set_price_floor", label: "Price floor" },
  { value: "set_price_ceiling", label: "Price ceiling" },
  { value: "clear_controls", label: "Clear price controls" },
];

export function describePolicy(p: GoodPolicy | undefined): string {
  if (!p) return "–";
  const parts: string[] = [];
  if (p.per_unit_tax) parts.push(`${fmt(p.per_unit_tax)}/unit tax`);
  if (p.ad_valorem_tax) parts.push(`${pct(p.ad_valorem_tax, 0)} ad valorem`);
  if (p.per_unit_tax || p.ad_valorem_tax) parts.push(`(${p.tax_payer} pays)`);
  if (p.subsidy) parts.push(`${fmt(p.subsidy)}/unit subsidy`);
  if (p.price_floor !== null) parts.push(`floor ${fmt(p.price_floor)}`);
  if (p.price_ceiling !== null) parts.push(`ceiling ${fmt(p.price_ceiling)}`);
  return parts.length ? parts.join(" ") : "No intervention";
}

export function PolicyPanel({
  runId,
  goods,
  disabled,
  policies,
  pending,
}: {
  runId: string;
  goods: string[];
  disabled: boolean;
  policies: Record<string, GoodPolicy>;
  pending: number;
}) {
  const [good, setGood] = useState<string>(goods[0] ?? "");
  const [action, setAction] = useState<Action>("set_tax");
  const [taxType, setTaxType] = useState<"per_unit" | "ad_valorem">("per_unit");
  const [rate, setRate] = useState(10);
  const [payer, setPayer] = useState<"buyer" | "seller">("seller");
  const [value, setValue] = useState(100);
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async () => {
    const body: PolicyActionInput = { action, good: good || null };
    if (action === "set_tax") Object.assign(body, { tax_type: taxType, rate, payer });
    if (action === "set_subsidy" || action === "set_price_floor" || action === "set_price_ceiling") body.value = value;
    try {
      const res = await api.policy(runId, body);
      setMsg(`Queued: applies at round ${res.applies_at_round}`);
    } catch (e: any) {
      setMsg(e.message);
    }
  };

  return (
    <div>
      <div style={{ marginBottom: 10, fontSize: 13 }}>
        {goods.map((g) => (
          <div key={g} className="row" style={{ justifyContent: "space-between" }}>
            <b>{g}</b>
            <span className="muted">{describePolicy(policies[g])}</span>
          </div>
        ))}
      </div>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label className="field">
          Good
          <select value={good} onChange={(e) => setGood(e.target.value)}>
            {goods.map((g) => (
              <option key={g}>{g}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Action
          <select value={action} onChange={(e) => setAction(e.target.value as Action)}>
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        {action === "set_tax" && (
          <>
            <label className="field">
              Type
              <select value={taxType} onChange={(e) => setTaxType(e.target.value as any)}>
                <option value="per_unit">Per unit</option>
                <option value="ad_valorem">Ad valorem</option>
              </select>
            </label>
            <label className="field">
              {taxType === "per_unit" ? "Rate / unit" : "Rate (0–1)"}
              <input type="number" step={taxType === "per_unit" ? 1 : 0.01} value={rate} style={{ width: 80 }} onChange={(e) => setRate(Number(e.target.value))} />
            </label>
            <label className="field">
              Paid by
              <select value={payer} onChange={(e) => setPayer(e.target.value as any)}>
                <option value="seller">Seller</option>
                <option value="buyer">Buyer</option>
              </select>
            </label>
          </>
        )}
        {(action === "set_subsidy" || action === "set_price_floor" || action === "set_price_ceiling") && (
          <label className="field">
            Value
            <input type="number" value={value} style={{ width: 90 }} onChange={(e) => setValue(Number(e.target.value))} />
          </label>
        )}
        <button className="btn primary" disabled={disabled} onClick={submit}>
          Apply next round
        </button>
      </div>
      {(msg || pending > 0) && <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>{pending > 0 ? `${pending} change(s) pending · ` : ""}{msg}</div>}
    </div>
  );
}
