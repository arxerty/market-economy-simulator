import { useEffect, useState } from "react";

// Chart colours come from the CSS tokens in styles.css, resolved to concrete values so
// SVG attributes get real colours; re-resolved whenever the theme changes.
const TOKENS = [
  "--series-1", "--series-2", "--series-3", "--series-4", "--series-7",
  "--neutral-line", "--text-muted", "--text-secondary", "--grid", "--axis", "--surface", "--critical", "--good",
] as const;

export type Colors = Record<(typeof TOKENS)[number], string>;

function readColors(): Colors {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t, cs.getPropertyValue(t).trim()])) as Colors;
}

export function useColors(): Colors {
  const [colors, setColors] = useState<Colors>(readColors);
  useEffect(() => {
    const update = () => setColors(readColors());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    const obs = new MutationObserver(update);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", update);
      obs.disconnect();
    };
  }, []);
  return colors;
}

export const roleColor = (c: Colors, role: string): string =>
  ({ buyer: c["--series-1"], seller: c["--series-2"], speculator: c["--series-3"], regulator: c["--series-7"] })[role] ??
  c["--text-muted"];

export const ROLE_LABEL: Record<string, string> = {
  buyer: "Buyers",
  seller: "Sellers",
  speculator: "Speculators",
  regulator: "Regulator",
};

export const axisProps = (c: Colors) => ({
  stroke: c["--axis"],
  tick: { fill: c["--text-muted"], fontSize: 11 },
  tickLine: false,
});

export const fmt = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || Number.isNaN(v)
    ? "–"
    : v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });

export const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? "–" : `${(v * 100).toFixed(digits)}%`;
