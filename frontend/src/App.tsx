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

      {error && (
        <div className="banner" role="alert">
          <span>{error}</span>
          <button type="button" className="link" onClick={() => setError("")}>Dismiss</button>
        </div>
      )}

      <main className="layout">
        <div className="col left">
          <PromptPanel busy={busy} onSubmit={newDesignFromPrompt} />
          {params && defaults ? (
            <DimensionControls
              params={params}
              committed={revision?.params ?? null}
              limits={defaults.limits}
              busy={busy}
              errorFields={errorFields}
              onApply={(raw) => run(() => api.generate(sessionId, raw, "Dimensions set from controls"))}
            />
          ) : (
            <section className="panel"><h2>Dimensions</h2><p className="muted">{healthError ? "Waiting for the backend…" : "Loading defaults…"}</p></section>
          )}
        </div>

        <div className="col centre">
          <ModelViewer revision={revision} busy={busy} />
        </div>

        <div className="col right">
          <ValidationPanel revision={revision} last={last} />
          <ExportPanel revision={revision} />
          <RevisionPanel revision={revision} last={last} history={history} busy={busy}
            onEdit={(text) => run(() => api.edit(sessionId, text, revision?.params))} />
        </div>
      </main>
    </div>
  );
}
