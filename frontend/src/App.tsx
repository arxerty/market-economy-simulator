import { useEffect, useState } from "react";
import { AnalysisPage } from "./pages/AnalysisPage";
import { ComparePage } from "./pages/ComparePage";
import { LivePage } from "./pages/LivePage";

// Hash routes: #/live/<run>, #/analysis/<run>, #/compare/<a>/<b>
function parseHash(): string[] {
  return location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
}

function useHashRoute(): [string[], (parts: string[]) => void] {
  const [parts, setParts] = useState(parseHash);
  useEffect(() => {
    const on = () => setParts(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (p: string[]) => {
    location.hash = "/" + p.map(encodeURIComponent).join("/");
  };
  return [parts, go];
}

function ThemeToggle() {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("theme") ?? "auto";
    } catch {
      return "auto";
    }
  });
  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);
  return (
    <select className="theme-toggle" value={theme} onChange={(e) => setTheme(e.target.value)} aria-label="Theme">
      <option value="auto">System theme</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
    </select>
  );
}

export default function App() {
  const [[page = "live", ...args], go] = useHashRoute();
  const tab = (name: string, label: string, target: string[]) => (
    <a href={"#/" + target.join("/")} className={page === name ? "active" : ""}>
      {label}
    </a>
  );
  const current = page === "live" || page === "analysis" ? args[0] : undefined;
  return (
    <div className="app">
      <header className="top">
        <h1>Market Economy Simulator</h1>
        <nav className="tabs">
          {tab("live", "Live", current ? ["live", current] : ["live"])}
          {tab("analysis", "Analysis", current ? ["analysis", current] : ["analysis"])}
          {tab("compare", "Compare", ["compare"])}
        </nav>
        <ThemeToggle />
      </header>
      {page === "live" && <LivePage runId={args[0] ?? null} onSelectRun={(id) => go(id ? ["live", id] : ["live"])} />}
      {page === "analysis" && <AnalysisPage runId={args[0] ?? null} onSelectRun={(id) => go(id ? ["analysis", id] : ["analysis"])} />}
      {page === "compare" && (
        <ComparePage
          a={args[0] && args[0] !== "_" ? args[0] : ""}
          b={args[1] && args[1] !== "_" ? args[1] : ""}
          onChange={(a, b) => go(["compare", a || "_", b || "_"])}
        />
      )}
    </div>
  );
}
