import type { ExperimentInfo, History, PolicyActionInput, RunMeta, RunStatus, Summary } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => req<{ ok: boolean; providers: string[] }>("/health"),
  experiments: () => req<ExperimentInfo[]>("/experiments"),
  runs: () => req<RunMeta[]>("/runs"),
  createRun: (experiment: string, overrides: Record<string, unknown>, autostart = false) =>
    post<RunStatus>("/runs", { experiment, overrides, autostart }),
  history: (id: string) => req<History>(`/runs/${id}/history`),
  start: (id: string) => post<RunStatus>(`/runs/${id}/start`),
  pause: (id: string) => post<RunStatus>(`/runs/${id}/pause`),
  step: (id: string) => post<RunStatus>(`/runs/${id}/step`),
  stop: (id: string) => post<RunStatus>(`/runs/${id}/stop`),
  speed: (id: string, delay_ms: number) => post<RunStatus>(`/runs/${id}/speed`, { delay_ms }),
  policy: (id: string, action: PolicyActionInput) =>
    post<{ applies_at_round: number }>(`/runs/${id}/policy`, action),
  replay: (id: string) => post<RunStatus>(`/runs/${id}/replay?autostart=true`),
  summary: (id: string) => req<Summary>(`/runs/${id}/summary`),
  compare: (a: string, b: string) => req<any>(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
};

export function wsUrl(runId: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/api/runs/${runId}/ws`;
}
