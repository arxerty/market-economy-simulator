import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { GoodRound, RoundSummary, WealthRow } from "../types";
import { axisProps, fmt, roleColor, ROLE_LABEL, useColors } from "../theme";
import { ChartTooltip } from "./ChartTooltip";

const H = 260;

function LegendItem({ color, label, dashed = false }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span>
      <i className={`swatch${dashed ? " dashed" : ""}`} style={dashed ? { borderColor: color } : { background: color }} />
      {label}
    </span>
  );
}

/** Price per round with the fundamental value (and the policy-adjusted equilibrium) overlaid. */
export function PriceChart({ rounds, good, policyRounds = [] }: { rounds: RoundSummary[]; good: string; policyRounds?: number[] }) {
  const c = useColors();
  const data = rounds.map((r) => {
    const g = r.goods[good];
    return {
      round: r.round,
      price: g.vwap ?? null,
      close: g.close,
      fundamental: g.fundamental,
      equilibrium: g.equilibrium_price !== g.fundamental ? g.equilibrium_price : null,
      bid: g.best_bid,
      ask: g.best_ask,
    };
  });
  const showEq = data.some((d) => d.equilibrium !== null);
  return (
    <>
      <div className="legend">
        <LegendItem color={c["--series-1"]} label="Trade price (VWAP)" />
        <LegendItem color={c["--neutral-line"]} label="Fundamental value" dashed />
        {showEq && <LegendItem color={c["--series-2"]} label="Equilibrium under policy" dashed />}
      </div>
      <ResponsiveContainer width="100%" height={H}>
        <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c["--grid"]} vertical={false} />
          <XAxis dataKey="round" {...axisProps(c)} />
          <YAxis {...axisProps(c)} width={52} domain={["auto", "auto"]} tickFormatter={(v) => fmt(v, 0)} />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: c["--axis"] }} />
          {policyRounds.map((r) => (
            <ReferenceLine key={r} x={r} stroke={c["--series-7"]} strokeDasharray="2 3" />
          ))}
          <Line dataKey="fundamental" name="Fundamental" stroke={c["--neutral-line"]} strokeDasharray="5 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          {showEq && (
            <Line dataKey="equilibrium" name="Equilibrium (policy)" stroke={c["--series-2"]} strokeDasharray="5 4" strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls />
          )}
          <Line dataKey="price" name="VWAP" stroke={c["--series-1"]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls activeDot={{ r: 4, stroke: c["--surface"], strokeWidth: 2 }} />
        </ComposedChart>
      </ResponsiveContainer>
    </>
  );
}

export function VolumeChart({ rounds, good }: { rounds: RoundSummary[]; good: string }) {
  const c = useColors();
  const data = rounds.map((r) => ({ round: r.round, volume: r.goods[good].volume, eq: r.goods[good].equilibrium_qty }));
  return (
    <>
      <div className="legend">
        <LegendItem color={c["--series-1"]} label="Units traded" />
        <LegendItem color={c["--neutral-line"]} label="Equilibrium quantity" dashed />
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c["--grid"]} vertical={false} />
          <XAxis dataKey="round" {...axisProps(c)} />
          <YAxis {...axisProps(c)} width={52} allowDecimals={false} />
          <Tooltip content={<ChartTooltip digits={0} />} cursor={{ fill: c["--grid"], opacity: 0.4 }} />
          <Bar dataKey="volume" name="Volume" fill={c["--series-1"]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          <Line dataKey="eq" name="Equilibrium qty" stroke={c["--neutral-line"]} strokeDasharray="5 4" dot={false} isAnimationActive={false} type="stepAfter" />
        </ComposedChart>
      </ResponsiveContainer>
    </>
  );
}

/** Cumulative order book depth at the end of the latest round. */
export function DepthChart({ latest }: { latest: GoodRound | undefined }) {
  const c = useColors();
  if (!latest) return <div className="empty">No order book yet</div>;
  const { bids, asks } = latest.depth;
  if (!bids.length && !asks.length) return <div className="empty">Book empty at end of round (all orders filled or expired)</div>;
  let cum = 0;
  const bidPts = bids.map(([p, q]) => ({ price: p, bids: (cum += q) })).reverse();
  cum = 0;
  const askPts = asks.map(([p, q]) => ({ price: p, asks: (cum += q) }));
  const data = [...bidPts, ...askPts];
  return (
    <>
      <div className="legend">
        <LegendItem color={c["--series-1"]} label="Bids (cumulative)" />
        <LegendItem color={c["--series-2"]} label="Asks (cumulative)" />
        <span className="muted">spread {fmt(latest.spread)}</span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c["--grid"]} vertical={false} />
          <XAxis dataKey="price" type="number" domain={["dataMin", "dataMax"]} {...axisProps(c)} tickFormatter={(v) => fmt(v, 1)} />
          <YAxis {...axisProps(c)} width={40} allowDecimals={false} />
          <Tooltip content={<ChartTooltip labelPrefix="Price " digits={0} />} />
          <Area dataKey="bids" name="Bid depth" type="stepAfter" stroke={c["--series-1"]} fill={c["--series-1"]} fillOpacity={0.18} strokeWidth={2} isAnimationActive={false} connectNulls={false} />
          <Area dataKey="asks" name="Ask depth" type="stepBefore" stroke={c["--series-2"]} fill={c["--series-2"]} fillOpacity={0.18} strokeWidth={2} isAnimationActive={false} connectNulls={false} />
        </AreaChart>
      </ResponsiveContainer>
    </>
  );
}

/** Wealth per agent, sorted, coloured by role. */
export function WealthChart({ wealth }: { wealth: WealthRow[] | undefined }) {
  const c = useColors();
  if (!wealth?.length) return <div className="empty">No data yet</div>;
  const data = [...wealth].sort((a, b) => b.wealth - a.wealth);
  const roles = Array.from(new Set(data.map((d) => d.role)));
  return (
    <>
      <div className="legend">
        {roles.map((r) => (
          <span key={r}>
            <span className="dot" style={{ background: roleColor(c, r) }} />
            {ROLE_LABEL[r] ?? r}
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }} barCategoryGap={2}>
          <CartesianGrid stroke={c["--grid"]} vertical={false} />
          <XAxis dataKey="id" {...axisProps(c)} interval={data.length > 30 ? "preserveStartEnd" : 0} angle={data.length > 16 ? -60 : 0} textAnchor={data.length > 16 ? "end" : "middle"} height={data.length > 16 ? 44 : 24} />
          <YAxis {...axisProps(c)} width={60} tickFormatter={(v) => fmt(v, 0)} />
          <Tooltip content={<ChartTooltip labelPrefix="" digits={0} />} cursor={{ fill: c["--grid"], opacity: 0.4 }} />
          <Bar dataKey="wealth" name="Wealth" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.id} fill={roleColor(c, d.role)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

/** Single-measure line chart over rounds (used on the analysis page). */
export function SeriesChart({
  rounds,
  series,
  height = 220,
  digits = 0,
  policyRounds = [],
}: {
  rounds: number[];
  series: { key: string; label: string; values: (number | null)[]; color: string; dashed?: boolean }[];
  height?: number;
  digits?: number;
  policyRounds?: number[];
}) {
  const c = useColors();
  const data = rounds.map((r, i) => Object.fromEntries([["round", r], ...series.map((s) => [s.key, s.values[i] ?? null])]));
  return (
    <>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <LegendItem key={s.key} color={s.color} label={s.label} dashed={s.dashed} />
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c["--grid"]} vertical={false} />
          <XAxis dataKey="round" {...axisProps(c)} />
          <YAxis {...axisProps(c)} width={60} tickFormatter={(v) => fmt(v, digits)} domain={["auto", "auto"]} />
          <Tooltip content={<ChartTooltip digits={2} />} cursor={{ stroke: c["--axis"] }} />
          {policyRounds.map((r) => (
            <ReferenceLine key={r} x={r} stroke={c["--series-7"]} strokeDasharray="2 3" />
          ))}
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={s.dashed ? 1.5 : 2} strokeDasharray={s.dashed ? "5 4" : undefined} dot={false} connectNulls isAnimationActive={false} />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </>
  );
}
