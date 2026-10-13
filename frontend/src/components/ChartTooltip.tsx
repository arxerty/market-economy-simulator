import { fmt } from "../theme";

interface Item {
  name?: string;
  value?: number | string | null;
  color?: string;
  stroke?: string;
  fill?: string;
  dataKey?: string | number;
}

export function ChartTooltip({
  active,
  payload,
  label,
  labelPrefix = "Round ",
  digits = 2,
}: {
  active?: boolean;
  payload?: Item[];
  label?: string | number;
  labelPrefix?: string;
  digits?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="t">
        {labelPrefix}
        {typeof label === "number" ? fmt(label) : label}
      </div>
      {payload
        .filter((p) => p.value !== null && p.value !== undefined)
        .map((p) => (
          <div className="line" key={String(p.dataKey ?? p.name)}>
            <span className="row" style={{ gap: 6 }}>
              <span className="dot" style={{ background: p.stroke ?? p.color ?? p.fill }} />
              {p.name}
            </span>
            <b>{typeof p.value === "number" ? fmt(p.value, digits) : p.value}</b>
          </div>
        ))}
    </div>
  );
}
