import { useCallback, useEffect, useMemo, useState } from "react";
import { ModelViewer } from "./components/ModelViewer";
import {
  DimensionControls,
  ExportPanel,
  Header,
  PromptPanel,
  RevisionPanel,
  ValidationPanel,
} from "./components/panels";
import {
  api,
  ApiError,
  newSessionId,
  type BracketParams,
  type DesignResponse,
  type Defaults,
  type Health,
  type HistoryEntry,
  type Revision,
} from "./services/api";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState("");
  const [defaults, setDefaults] = useState<Defaults | null>(null);
  const [sessionId, setSessionId] = useState(newSessionId);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [last, setLast] = useState<DesignResponse | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [leftTab, setLeftTab] = useState<"create" | "parameters">("create");
  const [rightTab, setRightTab] = useState<"edit" | "validation" | "export">("edit");

  const checkHealth = useCallback(() => {
    api.health()
      .then((h) => { setHealth(h); setHealthError(""); })
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  useEffect(() => {
    checkHealth();
    const id = setInterval(checkHealth, 15_000);
    return () => clearInterval(id);
  }, [checkHealth]);

  useEffect(() => {
    if (!healthError && !defaults) api.defaults().then(setDefaults).catch(() => undefined);
  }, [healthError, defaults]);

  async function run(call: () => Promise<DesignResponse>) {
    setBusy(true);
    setError("");
    try {
      const res = await call();
      setLast(res);
      setHistory(res.history);
      if (res.status === "committed" && res.revision) setRevision(res.revision);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  function newDesignFromPrompt(text: string) {
    // A design request starts a fresh session so unspecified values come from
    // the documented defaults, not from the previous design.
    const sid = newSessionId();
    setSessionId(sid);
    setRevision(null);
    setHistory([]);
    run(() => api.edit(sid, text, defaults?.params));
  }

  const params: BracketParams | null = revision?.params ?? defaults?.params ?? null;

  const errorFields = useMemo(() => {
    const fields = new Set<string>();
    if (last?.status === "rejected") last.attempt_reports.forEach((r) => r.errors.forEach((e) => e.field && fields.add(e.field)));
    return fields;
  }, [last]);

  return (
    <div className="app">
      <Header health={health} healthError={healthError} />
      <div className="workspace-topline">
        <div className="workspace-crumb"><span className="spark">✳</span> WORKSPACE <span className="crumb-slash">/</span> <strong>UNTITLED PROJECT</strong> <span className="crumb-slash">/</span> BRACKET STUDIO</div>
        <div className="workspace-meta"><span className="meta-pulse" /> {busy ? "PROCESSING" : revision ? `REVISION ${String(revision.number).padStart(2, "0")} · READY` : "AWAITING INPUT"}<span className="topline-divider" /> MM · 3D</div>
      </div>
      {error && <div className="banner" role="alert"><span>{error}</span><button type="button" className="link" onClick={() => setError("")}>Dismiss</button></div>}
      <main className="layout">
        <aside className="col left workspace-side">
          <div className="side-heading"><span className="section-index">01 /</span> DESIGN CONSOLE <span className="side-heading-icon">⌘</span></div>
          <div className="tabs" role="tablist" aria-label="Design controls">
            <button type="button" role="tab" aria-selected={leftTab === "create"} className={leftTab === "create" ? "active" : ""} onClick={() => setLeftTab("create")}>✦ &nbsp; AI PROMPT</button>
            <button type="button" role="tab" aria-selected={leftTab === "parameters"} className={leftTab === "parameters" ? "active" : ""} onClick={() => setLeftTab("parameters")}>▦ &nbsp; PARAMETERS</button>
          </div>
          <div className="side-body">
            <div className="tab-content" hidden={leftTab !== "create"}><PromptPanel busy={busy} onSubmit={(text) => { newDesignFromPrompt(text); setRightTab("validation"); }} /></div>
            <div className="tab-content" hidden={leftTab !== "parameters"}>
              {params && defaults ? <DimensionControls params={params} committed={revision?.params ?? null} limits={defaults.limits} busy={busy} errorFields={errorFields} onApply={(raw) => { run(() => api.generate(sessionId, raw, "Dimensions set from controls")); setRightTab("validation"); }} /> : <section className="panel"><h2>Dimensions</h2><p className="muted">{healthError ? "Waiting for backend…" : "Loading defaults…"}</p></section>}
            </div>
          </div>
          <div className="side-footer"><span className="tiny-square" /> PRECISION ENGINEERING <span>V 0.1</span></div>
        </aside>
        <section className="col centre viewport-zone" aria-label="3D model workspace">
          <div className="viewport-title"><div><span className="section-index">02 /</span> MODEL VIEWPORT <span className="live-tag">● LIVE 3D</span></div><span className="viewport-subtitle">ISOMETRIC VIEW</span></div>
          <ModelViewer revision={revision} busy={busy} />
          <div className="viewport-bottom"><span><span className="cyan">+</span> CAD KERNEL / {health?.cad_engine?.name ?? "CONNECTING"}</span><span>{revision ? `MODEL REV. ${revision.number}` : "NO GEOMETRY LOADED"}</span></div>
        </section>
        <aside className="col right workspace-side inspector">
          <div className="side-heading"><span className="section-index">03 /</span> INSPECTOR <span className="side-heading-icon">◈</span></div>
          <div className="tabs" role="tablist" aria-label="Inspector tools">
            <button type="button" role="tab" aria-selected={rightTab === "edit"} className={rightTab === "edit" ? "active" : ""} onClick={() => setRightTab("edit")}>EDIT</button>
            <button type="button" role="tab" aria-selected={rightTab === "validation"} className={rightTab === "validation" ? "active" : ""} onClick={() => setRightTab("validation")}>CHECKS</button>
            <button type="button" role="tab" aria-selected={rightTab === "export"} className={rightTab === "export" ? "active" : ""} onClick={() => setRightTab("export")}>EXPORT</button>
          </div>
          <div className="side-body">
            <div className="tab-content" hidden={rightTab !== "edit"}><RevisionPanel revision={revision} last={last} history={history} busy={busy} onEdit={(text) => run(() => api.edit(sessionId, text, revision?.params))} /></div>
            <div className="tab-content" hidden={rightTab !== "validation"}><ValidationPanel revision={revision} last={last} /></div>
            <div className="tab-content" hidden={rightTab !== "export"}><ExportPanel revision={revision} /></div>
          </div>
          <div className="side-footer"><span className="tiny-square" /> {revision ? "SAVED REVISION" : "NO ACTIVE DESIGN"} <span>{revision ? `R${String(revision.number).padStart(2, "0")}` : "—"}</span></div>
        </aside>
      </main>
      <footer className="app-bottom"><span>◈ &nbsp; FLOWCAD <span className="bottom-muted">/ INTELLIGENT DESIGN SYSTEM</span></span><span className="bottom-muted">BUILT FOR THE NEXT GENERATION OF CAD &nbsp; ✳</span></footer>
    </div>
  );
}
