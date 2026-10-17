import { useEffect, useState } from "react";
import type {
  BracketParams,
  DesignResponse,
  Health,
  HistoryEntry,
  Interpretation,
  Material,
  Revision,
  ValidationReport,
} from "../services/api";
import { fileUrl } from "../services/api";

/* ------------------------------------------------------------------ Header */

export function Header({ health, healthError }: { health: Health | null; healthError: string }) {
  const ai = health?.ai;
  const cad = health?.cad_engine;
  return (
    <header className="app-header">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><path d="M6 4h6v18h16v6H6z" fill="currentColor" /></svg>
        <div>
          <h1>FlowCAD</h1>
          <p className="tagline">From natural language to validated CAD.</p>
        </div>
      </div>
      <div className="status-row" aria-live="polite">
        {healthError ? (
          <span className="status-chip bad"><i />Backend offline</span>
        ) : !health ? (
          <span className="status-chip"><i />Connecting…</span>
        ) : (
          <>
            <span className={`status-chip ${ai?.configured ? "good" : "warn"}`} title={ai?.configured ? `${ai.provider}` : "No DO_INFERENCE_API_KEY set; requests are parsed by the built-in rule parser."}>
              <i />{ai?.configured ? `AI: Gemma 4 · ${ai.model}` : "AI: offline parser"}
            </span>
            <span className={`status-chip ${cad?.available ? "good" : "bad"}`}>
              <i />CAD: {cad?.available ? `${cad.name} ${cad.version}` : "unavailable"}
            </span>
          </>
        )}
      </div>
    </header>
  );
}

/* ------------------------------------------------------------ Prompt panel */

export const EXAMPLE_PROMPT =
  "Create an L-shaped aluminum mounting bracket. The base is 100 mm long and 50 mm wide. The vertical plate is 70 mm tall. Both plates are 5 mm thick. Add two mounting holes to each plate.";

export function PromptPanel({ busy, onSubmit }: { busy: boolean; onSubmit: (text: string) => void }) {
  const [text, setText] = useState("");
  return (
    <section className="panel" aria-labelledby="req-h">
      <h2 id="req-h">Design request</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) onSubmit(text.trim());
        }}
      >
        <label htmlFor="prompt" className="sr-only">Describe the bracket</label>
        <textarea
          id="prompt"
          rows={5}
          value={text}
          placeholder="Describe the L-bracket: lengths, width, height, thickness, holes, material…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) onSubmit(text.trim());
          }}
        />
        <div className="row">
          <button type="button" className="link" onClick={() => setText(EXAMPLE_PROMPT)} disabled={busy}>Use example</button>
          <button type="submit" className="primary" disabled={busy || !text.trim()}>{busy ? "Generating…" : "Generate"}</button>
        </div>
        <p className="fine">Starts a new design. Anything you don't mention uses the documented defaults.</p>
      </form>
    </section>
  );
}

/* ------------------------------------------------------- Dimension controls */

const FIELDS: { key: keyof BracketParams; label: string; unit?: string; step: number }[] = [
  { key: "base_length_mm", label: "Base length", unit: "mm", step: 1 },
  { key: "base_width_mm", label: "Base width", unit: "mm", step: 1 },
  { key: "vertical_height_mm", label: "Vertical height", unit: "mm", step: 1 },
  { key: "thickness_mm", label: "Thickness", unit: "mm", step: 0.5 },
  { key: "hole_diameter_mm", label: "Hole diameter", unit: "mm", step: 0.1 },
  { key: "edge_margin_mm", label: "Edge margin", unit: "mm", step: 0.5 },
  { key: "fillet_radius_mm", label: "Bend fillet", unit: "mm", step: 0.5 },
];

const MATERIAL_LABEL: Record<Material, string> = {
  aluminum_6061: "Aluminium 6061",
  steel_mild: "Mild steel",
  stainless_304: "Stainless 304",
  pla: "PLA (3D print)",
};

type Draft = Record<keyof BracketParams, string>;

const toDraft = (p: BracketParams): Draft =>
  Object.fromEntries(Object.entries(p).map(([k, v]) => [k, String(v)])) as Draft;

export function DimensionControls({
  params, committed, limits, busy, errorFields, onApply,
}: {
  params: BracketParams;
  committed: BracketParams | null;
  limits: Record<string, { min: number; max: number }>;
  busy: boolean;
  errorFields: Set<string>;
  onApply: (raw: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<Draft>(toDraft(params));
  useEffect(() => setDraft(toDraft(params)), [params]);

  const dirty = committed ? (Object.keys(draft) as (keyof BracketParams)[]).filter((k) => draft[k] !== String(committed[k])) : [];

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const raw: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(draft)) {
      if (k === "material") raw[k] = v;
      else if (v.trim() === "") raw[k] = null;
      else raw[k] = k === "holes_per_plate" ? Number.parseInt(v, 10) : Number(v);
    }
    onApply(raw);
  }

  return (
    <section className="panel" aria-labelledby="dim-h">
      <h2 id="dim-h">Dimensions</h2>
      <form onSubmit={submit} className="dims">
        {FIELDS.map((f) => {
          const lim = limits[f.key];
          return (
            <div key={f.key} className={`field ${errorFields.has(f.key) ? "has-error" : ""} ${dirty.includes(f.key) ? "is-dirty" : ""}`}>
              <label htmlFor={`f-${f.key}`}>{f.label}</label>
              <div className="input-unit">
                <input
                  id={`f-${f.key}`}
                  type="number"
                  inputMode="decimal"
                  step={f.step}
                  min={lim?.min}
                  max={lim?.max}
                  value={draft[f.key]}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                />
                <span>{f.unit}</span>
              </div>
            </div>
          );
        })}
        <div className={`field ${dirty.includes("holes_per_plate") ? "is-dirty" : ""} ${errorFields.has("holes_per_plate") ? "has-error" : ""}`}>
          <label htmlFor="f-holes">Holes per plate</label>
          <select id="f-holes" value={draft.holes_per_plate} onChange={(e) => setDraft({ ...draft, holes_per_plate: e.target.value })}>
            {[0, 1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <div className={`field ${dirty.includes("material") ? "is-dirty" : ""}`}>
          <label htmlFor="f-material">Material</label>
          <select id="f-material" value={draft.material} onChange={(e) => setDraft({ ...draft, material: e.target.value })}>
            {(Object.keys(MATERIAL_LABEL) as Material[]).map((m) => <option key={m} value={m}>{MATERIAL_LABEL[m]}</option>)}
          </select>
        </div>
        <div className="row full">
          <button type="button" className="link" disabled={busy || !dirty.length} onClick={() => committed && setDraft(toDraft(committed))}>Revert</button>
          <button type="submit" className="primary" disabled={busy || (!!committed && !dirty.length)}>
            {committed ? (dirty.length ? `Apply ${dirty.length} change${dirty.length > 1 ? "s" : ""}` : "No changes") : "Generate from dimensions"}
          </button>
        </div>
      </form>
    </section>
  );
}

/* ---------------------------------------------------------- Validation panel */

const STAGES: { key: ValidationReport["stage"]; label: string }[] = [
  { key: "parameters", label: "Parameter validation" },
  { key: "geometry", label: "Geometry generation & CAD validity" },
  { key: "export", label: "Export" },
];

const CHECK_LABEL: Record<string, string> = {
  schema: "types & required values", ranges: "limits", flange_length: "flange length", fillet_feasibility: "fillet",
  hole_vs_face: "hole vs face size", edge_margin_along_flange: "edge margin (length)", edge_margin_across_width: "edge margin (width)",
  hole_centres_inside_faces: "hole positions", recommended_edge_distance: "1.5d edge distance",
  build: "build", non_null: "non-null", brep_valid: "B-rep valid", single_solid: "one solid", bounding_box: "bounding box",
  volume: "volume", hole_topology: "hole topology", stl_written: "STL written", stl_triangles: "STL triangles",
  step_written: "STEP written", step_header: "STEP header",
};

export function ValidationPanel({ revision, last }: { revision: Revision | null; last: DesignResponse | null }) {
  const rejected = last?.status === "rejected";
  const reports = rejected ? last.attempt_reports : revision?.reports ?? [];
  const warnings = reports.flatMap((r) => r.warnings);

  return (
    <section className="panel" aria-labelledby="val-h">
      <h2 id="val-h">Validation{rejected ? " · last attempt" : revision ? ` · rev ${revision.number}` : ""}</h2>
      {!reports.length ? (
        <p className="muted">Checks appear here after the first generation. Nothing is reported as passed unless it ran.</p>
      ) : (
        <ul className="stages">
          {STAGES.map(({ key, label }) => {
            const r = reports.find((x) => x.stage === key);
            const state = !r ? "skip" : r.ok ? "pass" : "fail";
            return (
              <li key={key} className={`stage ${state}`}>
                <span className="badge">{state === "skip" ? "NOT RUN" : state.toUpperCase()}</span>
                <div>
                  <p className="stage-name">{label}</p>
                  {r && <p className="checks">{r.checks_run.map((c) => CHECK_LABEL[c] ?? c).join(" · ")}</p>}
                  {r?.errors.map((e, i) => <p key={i} className="issue error">{e.message}</p>)}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {warnings.length > 0 && (
        <div className="warnings">
          {warnings.map((w, i) => <p key={i} className="issue warning">{w.message}</p>)}
        </div>
      )}
    </section>
  );
}

/* --------------------------------------------------------------- Exports */

export function ExportPanel({ revision }: { revision: Revision | null }) {
  if (!revision) {
    return (
      <section className="panel" aria-labelledby="exp-h">
        <h2 id="exp-h">Export</h2>
        <p className="muted">Downloads appear once a design has passed validation.</p>
      </section>
    );
  }
  const m = revision.metadata;
  return (
    <section className="panel" aria-labelledby="exp-h">
      <h2 id="exp-h">Export · rev {revision.number}</h2>
      <div className="downloads">
        {revision.files.map((f) => (
          <a key={f.format} className="download" href={fileUrl(f.url, true)} download>
            <span>Download {f.format.toUpperCase()}</span>
            <small>{(f.size_bytes / 1024).toFixed(1)} KB · {f.format === "stl" ? "mesh for printing/viewing" : "solid for CAD tools"}</small>
          </a>
        ))}
      </div>
      <dl className="meta">
        <dt>Volume</dt><dd>{Math.round(m.volume_mm3).toLocaleString()} mm³</dd>
        <dt>Mass (estimate)</dt><dd>{m.mass_g_estimate} g</dd>
        <dt>Holes</dt><dd>{m.hole_count}</dd>
        <dt>Build time</dt><dd>{m.generation_ms} ms</dd>
      </dl>
    </section>
  );
}

/* ---------------------------------------------------------- Revision panel */

const FIELD_LABEL: Record<string, string> = {
  base_length_mm: "length", base_width_mm: "width", vertical_height_mm: "height", thickness_mm: "thickness",
  hole_diameter_mm: "hole Ø", holes_per_plate: "holes/plate", edge_margin_mm: "edge margin",
  fillet_radius_mm: "fillet", material: "material",
};

export function InterpretationNote({ interp }: { interp: Interpretation | null }) {
  if (!interp) return null;
  return (
    <div className="interp">
      <p>
        Understood by <b>{interp.source === "gemma" ? "Gemma 4" : "rule parser"}</b>
        {interp.latency_ms != null && ` in ${(interp.latency_ms / 1000).toFixed(1)} s`}
        {interp.ai_error && <span className="muted"> (Gemma unavailable: {interp.ai_error.split(":")[0].replace("_", " ")})</span>}
      </p>
      {Object.keys(interp.changes).length > 0 && (
        <div className="chips" aria-label="Values taken from the request">
          {Object.entries(interp.changes).map(([f, v]) => (
            <span key={f} className={`chip ${interp.changed_fields.includes(f) ? "" : "same"}`}
              title={interp.changed_fields.includes(f) ? "Changed" : "Same as before"}>
              {FIELD_LABEL[f] ?? f} {String(v)}{f.endsWith("_mm") ? " mm" : ""}
            </span>
          ))}
        </div>
      )}
      {interp.message && interp.status === "ok" && <p className="muted">{interp.message}</p>}
    </div>
  );
}

export function RevisionPanel({
  revision, last, history, busy, onEdit,
}: {
  revision: Revision | null;
  last: DesignResponse | null;
  history: HistoryEntry[];
  busy: boolean;
  onEdit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const examples = ["Increase the base length to 120 mm", "Change the thickness to 6 mm", "Make the holes 10 mm wide", "Move the holes farther from the edge"];

  return (
    <section className="panel" aria-labelledby="rev-h">
      <h2 id="rev-h">Edit · {revision ? `revision ${revision.number}` : "no revision yet"}</h2>

      {last && last.status !== "committed" && (
        <div className={`notice ${last.status === "rejected" ? "bad" : "info"}`} role="status">
          <p className="notice-title">
            {last.status === "rejected" ? "Edit rejected" : last.status === "clarify" ? "Need more detail" : "Not supported"}
          </p>
          <p>{last.message}</p>
          {last.status === "rejected" && revision && <p className="muted">Showing revision {revision.number}, the last valid design. Its downloads still work.</p>}
        </div>
      )}
      {last?.status === "committed" && <InterpretationNote interp={last.interpretation} />}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) {
            onEdit(text.trim());
            setText("");
          }
        }}
      >
        <label htmlFor="edit" className="sr-only">Describe a change</label>
        <div className="edit-row">
          <input id="edit" value={text} onChange={(e) => setText(e.target.value)} placeholder={revision ? "e.g. make the base 20 mm longer" : "Generate a design first"} disabled={!revision} />
          <button type="submit" className="primary" disabled={busy || !revision || !text.trim()}>Apply</button>
        </div>
      </form>
      {revision && (
        <div className="chips">
          {examples.map((x) => <button key={x} type="button" className="chip button" disabled={busy} onClick={() => onEdit(x)}>{x}</button>)}
        </div>
      )}

      {history.length > 0 && (
        <ol className="history" reversed>
          {[...history].reverse().slice(0, 8).map((h, i) => (
            <li key={i} className={h.status}>
              <span className="rev">{h.revision ? `r${h.revision}` : "✕"}</span>
              <span className="sum">{h.summary}</span>
              <time dateTime={h.created_at}>{new Date(h.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
