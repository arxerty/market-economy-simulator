import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "../api";
import type { History, RoundSummary, RunStatus, SimEvent } from "../types";

const MAX_LOG = 1500;

export interface RunData {
  history: History | null;
  status: RunStatus | null;
  rounds: RoundSummary[];
  log: SimEvent[];
  connected: boolean;
  error: string | null;
  pendingPolicies: number;
}

/**
 * Loads a run's history and, for live runs, keeps it current over a WebSocket.
 * Finished runs on disk are loaded once from /history (static replay of the log).
 */
export function useRunStream(runId: string | null): RunData {
  const [history, setHistory] = useState<History | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [rounds, setRounds] = useState<RoundSummary[]>([]);
  const [log, setLog] = useState<SimEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingPolicies, setPending] = useState(0);
  const buffer = useRef<{ rounds: RoundSummary[]; log: SimEvent[] }>({ rounds: [], log: [] });

  useEffect(() => {
    setHistory(null);
    setStatus(null);
    setRounds([]);
    setLog([]);
    setError(null);
    setPending(0);
    if (!runId) return;

    let ws: WebSocket | null = null;
    let cancelled = false;
    let flushTimer: number | undefined;

    // Batch high-frequency events into one render per animation frame.
    const flush = () => {
      flushTimer = undefined;
      const { rounds: r, log: l } = buffer.current;
      buffer.current = { rounds: [], log: [] };
      if (r.length) setRounds((prev) => [...prev, ...r]);
      if (l.length) setLog((prev) => [...prev, ...l].slice(-MAX_LOG));
    };
    const schedule = () => {
      if (flushTimer === undefined) flushTimer = window.setTimeout(flush, 50);
    };

    const init = (h: History) => {
      setHistory(h);
      setStatus(h.status);
      setRounds(h.rounds);
      setLog(h.log);
    };

    api
      .history(runId)
      .then((h) => {
        if (cancelled) return;
        init(h);
        if (!h.status.live) return;
        ws = new WebSocket(wsUrl(runId));
        ws.onopen = () => setConnected(true);
        ws.onclose = () => setConnected(false);
        ws.onerror = () => setError("WebSocket connection failed");
        ws.onmessage = (msg) => {
          const data = JSON.parse(msg.data);
          if (data.type === "init") {
            buffer.current = { rounds: [], log: [] };
            init(data.history);
          } else if (data.type === "status") {
            setStatus(data.status);
          } else if (data.type === "policy_queued") {
            setPending((n) => n + 1);
          } else if (data.type === "event") {
            const ev: SimEvent = data.event;
            if (ev.type === "round_summary") {
              buffer.current.rounds.push(ev.data);
              setStatus((s) => (s ? { ...s, round: ev.round } : s));
            } else if (ev.type === "policy_change") {
              setPending(0);
              buffer.current.log.push(ev);
            } else if (["decision", "message", "policy_rejected", "error"].includes(ev.type)) {
              buffer.current.log.push(ev);
            } else if (ev.type === "run_finished") {
              setStatus((s) => (s ? { ...s, finished: true, running: false } : s));
            }
            schedule();
          } else if (data.type === "error") {
            setError(data.error);
          }
        };
      })
      .catch((e) => !cancelled && setError(String(e.message ?? e)));

    return () => {
      cancelled = true;
      if (flushTimer !== undefined) window.clearTimeout(flushTimer);
      ws?.close();
    };
  }, [runId]);

  return { history, status, rounds, log, connected, error, pendingPolicies };
}
