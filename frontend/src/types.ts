export type Role = "buyer" | "seller" | "speculator" | "regulator";

export interface RunStatus {
  run_id: string;
  running: boolean;
  finished: boolean;
  round: number;
  rounds?: number;
  delay_ms?: number;
  error?: string | null;
  live: boolean;
}

export interface GoodPolicy {
  per_unit_tax: number;
  ad_valorem_tax: number;
  tax_payer: "buyer" | "seller";
  subsidy: number;
  price_floor: number | null;
  price_ceiling: number | null;
}

export interface GoodRound {
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  vwap: number | null;
  volume: number;
  trades: number;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  depth: { bids: [number, number][]; asks: [number, number][] };
  fundamental: number | null;
  equilibrium_price: number | null;
  equilibrium_qty: number;
  competitive_price: number | null;
  monopoly_price: number | null;
  efficient_surplus: number;
  policy_surplus: number;
  realized_surplus: number;
  dwl: number;
  efficiency: number | null;
  buyer_surplus: number;
  producer_profit: number;
  trading_pnl: number;
  tax: number;
  subsidy: number;
  buyer_price: number | null;
  seller_volumes: Record<string, number>;
  seller_asks: Record<string, number>;
  policy: GoodPolicy;
}

export interface WealthRow {
  id: string;
  role: Role;
  wealth: number;
  cash: number;
  inventory: number;
}

export interface RoundSummary {
  round: number;
  goods: Record<string, GoodRound>;
  wealth: WealthRow[];
  government: { tax_revenue: number; subsidies_paid: number; balance: number };
  llm: Record<string, number>;
}

export interface SimEvent {
  seq: number;
  round: number;
  type: string;
  data: any;
}

export interface AgentMeta {
  id: string;
  role: Role;
  strategy: string;
  decision: string;
  info_lag: number;
}

export interface History {
  status: RunStatus;
  config: any;
  agents: AgentMeta[];
  benchmarks: Record<string, { competitive: { price: number | null; quantity: number }; monopoly: { price: number | null; quantity: number } }>;
  rounds: RoundSummary[];
  log: SimEvent[];
}

export interface ExperimentInfo {
  file: string;
  name?: string;
  description?: string;
  experiment_type?: string;
  rounds?: number;
  seed?: number;
  goods?: string[];
  provider?: string;
  model?: string;
  agents?: number;
  error?: string;
}

export interface RunMeta {
  run_id: string;
  name: string;
  experiment_type: string;
  status: string;
  rounds: number;
  rounds_completed: number;
  running?: boolean;
  live?: boolean;
  llm_provider?: string;
}

export interface PolicyActionInput {
  action: "set_tax" | "remove_tax" | "set_price_floor" | "set_price_ceiling" | "set_subsidy" | "clear_controls";
  good: string | null;
  tax_type?: "per_unit" | "ad_valorem";
  rate?: number;
  payer?: "buyer" | "seller";
  value?: number | null;
}

export type Summary = any;
