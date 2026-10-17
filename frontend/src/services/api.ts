// Typed client for the FlowCAD API. Shapes mirror backend/app/schemas.

export type Material = "aluminum_6061" | "steel_mild" | "stainless_304" | "pla";

export interface BracketParams {
  base_length_mm: number;
  base_width_mm: number;
  vertical_height_mm: number;
  thickness_mm: number;
  hole_diameter_mm: number;
  holes_per_plate: number;
  edge_margin_mm: number;
  fillet_radius_mm: number;
  material: Material;
}

export interface Issue {
  code: string;
  message: string;
  field: string | null;
  severity: "error" | "warning";
}

export interface ValidationReport {
  stage: "parameters" | "geometry" | "export";
  ok: boolean;
  errors: Issue[];
  warnings: Issue[];
  checks_run: string[];
}

export interface DownloadFile {
  format: "stl" | "step";
  filename: string;
  size_bytes: number;
  url: string;
}

export interface ModelMetadata {
  volume_mm3: number;
  mass_g_estimate: number;
  bounding_box_mm: [number, number, number];
  hole_count: number;
  engine: string;
  generation_ms: number;
}

export interface Revision {
  number: number;
  params: BracketParams;
  design_id: string;
  files: DownloadFile[];
  metadata: ModelMetadata;
  reports: ValidationReport[];
  source: "prompt" | "controls" | "default";
  note: string | null;
  created_at: string;
}

export interface Interpretation {
  status: "ok" | "clarify" | "unsupported";
  source: "gemma" | "rules";
  message: string;
  changes: Partial<BracketParams>;
  changed_fields: string[];
  proposed_params: Record<string, unknown> | null;
  parameter_report: ValidationReport | null;
  ai_error: string | null;
  latency_ms: number | null;
}

export interface HistoryEntry {
  revision: number | null;
  status: "committed" | "rejected";
  params: Record<string, unknown> | null;
  summary: string;
  created_at: string;
}

export interface DesignResponse {
  status: "committed" | "rejected" | "clarify" | "unsupported";
  message: string;
  revision: Revision | null;
  attempt_reports: ValidationReport[];
  interpretation: Interpretation | null;
  history: HistoryEntry[];
}

export interface Health {
  status: "ok";
  cad_engine: { available: boolean; name: string; version: string | null; kernel: string | null; formats: string[] };
  ai: { provider: string; configured: boolean; model: string; fallback: string };
}

export interface Defaults {
  params: BracketParams;
  limits: Record<string, { min: number; max: number }>;
  materials: Material[];
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export const fileUrl = (path: string, download = false) => `${BASE}${path}${download ? "?download=1" : ""}`;

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 90_000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    throw new ApiError(
      (err as Error).name === "AbortError"
        ? "The server took too long to respond. Try again."
        : "Can't reach the FlowCAD server. Check that the backend is running on port 8000.",
    );
  } finally {
    clearTimeout(timer);
  }
  const body = await res.json().catch(() => null);
  // Design endpoints return a full DesignResponse with 422 when a design is rejected.
  if (res.ok || (res.status === 422 && body && typeof body === "object" && "status" in body && "revision" in body)) {
    return body as T;
  }
  const detail = body?.detail;
  const msg = Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join("; ") : detail;
  throw new ApiError(msg || `Request failed (HTTP ${res.status}).`);
}

export const api = {
  health: () => request<Health>("/health", undefined, 8_000),
  defaults: () => request<Defaults>("/api/design/default"),
  edit: (session_id: string, text: string, current_params?: BracketParams) =>
    request<DesignResponse>("/api/design/edit", { method: "POST", body: JSON.stringify({ session_id, text, current_params }) }),
  generate: (session_id: string, params: Record<string, unknown>, note?: string) =>
    request<DesignResponse>("/api/design/generate", { method: "POST", body: JSON.stringify({ session_id, params, note }) }),
};

export async function fetchStl(url: string): Promise<ArrayBuffer> {
  let res: Response;
  try {
    res = await fetch(fileUrl(url));
  } catch {
    throw new ApiError("The model file could not be downloaded.");
  }
  if (!res.ok) throw new ApiError(`The model file could not be loaded (HTTP ${res.status}).`);
  return res.arrayBuffer();
}

export function newSessionId(): string {
  return (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40);
}
