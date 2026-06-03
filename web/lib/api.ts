/**
 * Typed fetch client for the LoanWhiz FastAPI service.
 *
 * Mirrors the request/response shapes defined in
 * `src/loanwhiz/api/main.py` (and the Pydantic primitive models it returns).
 * Plain `fetch` + typed wrappers — no state library, no client framework.
 *
 * Base URL comes from `NEXT_PUBLIC_API_BASE` (default http://localhost:8000).
 * `NEXT_PUBLIC_*` env vars are inlined at build time and safe to read in the
 * browser.
 *
 * Convention for pages (see web/CONTRACT.md): a page is a Client Component
 * that calls one of these wrappers inside `useEffect`, holding the result in
 * `useState`, and renders <Skeleton/> while loading and an error card on
 * failure. No fetching happens at build time — pages render placeholders /
 * loading states until the API is reachable.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** The single deal id the backend serves (see config.GREEN_LION). */
export const DEFAULT_DEAL_ID = "green-lion-2026-1";

/** Thrown when the API responds with a non-2xx status. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    // Always hit the live API — this is a demo dashboard over a moving backend.
    cache: "no-store",
  });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text().catch(() => undefined);
    }
    const message =
      (detail as { detail?: string } | undefined)?.detail ??
      `${res.status} ${res.statusText}`;
    throw new ApiError(message, res.status, detail);
  }

  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Service / health  —  GET /  ·  GET /health
// ---------------------------------------------------------------------------

export interface ServiceInfo {
  service: string;
  version: string;
  deals: string[];
}

export interface HealthStatus {
  status: string;
}

export function getServiceInfo(): Promise<ServiceInfo> {
  return request<ServiceInfo>("/");
}

export function getHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/health");
}

// ---------------------------------------------------------------------------
// Agent query  —  POST /query
// ---------------------------------------------------------------------------

export interface QueryRequest {
  question: string;
  /** Defaults to 0.7 server-side. */
  confidence_threshold?: number;
}

export interface QueryResponse {
  question: string;
  answer: string;
  overall_status: string;
  aggregate_confidence: number;
  human_review_required: boolean;
  reasoning_trace: string[];
  evidence_pack_id: string;
}

export function postQuery(body: QueryRequest): Promise<QueryResponse> {
  return request<QueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Deal model  —  GET /deal/{deal_id}/model
// ---------------------------------------------------------------------------

export interface TapeRef {
  date: string;
  url: string;
}

export interface InvestorReportRef {
  period: string;
  url: string;
}

/** Mirrors `config.GREEN_LION`. */
export interface DealModel {
  deal_name: string;
  prospectus_url: string;
  tape_urls: TapeRef[];
  investor_report_urls: InvestorReportRef[];
  // The endpoint returns the raw deal dict; allow forward-compatible extras.
  [key: string]: unknown;
}

export function getDealModel(
  dealId: string = DEFAULT_DEAL_ID,
): Promise<DealModel> {
  return request<DealModel>(`/deal/${dealId}/model`);
}

// ---------------------------------------------------------------------------
// Compliance  —  GET /deal/{deal_id}/compliance
// (CovenantMonitor → CovenantOutput)
// ---------------------------------------------------------------------------

/** One `TriggerStatus` — a trigger evaluated for one reporting period. */
export interface TriggerStatus {
  trigger_name: string;
  period: string;
  metric_value: number;
  threshold: number | null;
  is_triggered: boolean;
  /** 0–100+: 100 = at threshold, >100 = breached. */
  proximity_pct: number;
  /** "improving" | "deteriorating" | "stable" | "n/a" */
  direction: string;
}

/** Mirrors `CovenantOutput`. */
export interface ComplianceResult {
  trigger_statuses: TriggerStatus[];
  /** Names of triggers breached in the latest period. */
  active_triggers: string[];
  /** Names of triggers approaching their threshold in the latest period. */
  near_miss_triggers: string[];
  summary: string;
}

export function getCompliance(
  dealId: string = DEFAULT_DEAL_ID,
): Promise<ComplianceResult> {
  return request<ComplianceResult>(`/deal/${dealId}/compliance`);
}

// ---------------------------------------------------------------------------
// Projection  —  POST /deal/{deal_id}/project
// (WaterfallRunner → WaterfallOutput per scenario)
// ---------------------------------------------------------------------------

export interface ProjectRequest {
  /** Defaults to ["base", "stress"] server-side. */
  scenarios?: string[];
  /** Defaults to 12 server-side. */
  months?: number;
}

/** One priority step in a payment waterfall. */
export interface WaterfallStep {
  /** Step label from the prospectus, e.g. "(a)". */
  priority: string;
  recipient: string;
  amount_available: number;
  amount_distributed: number;
  shortfall: number;
  condition: string | null;
}

/** Per-tranche distribution summary. */
export interface TrancheDistribution {
  /** "class_a" | "class_b" | "class_c" */
  tranche: string;
  interest_received: number;
  principal_received: number;
  total_received: number;
  opening_balance: number;
  closing_balance: number;
}

/** Mirrors `WaterfallOutput` — one scenario's projected waterfall. */
export interface WaterfallProjection {
  reporting_period: string;
  revenue_waterfall: WaterfallStep[];
  redemption_waterfall: WaterfallStep[];
  tranche_distributions: TrancheDistribution[];
  total_distributed: number;
  shortfall: number;
}

export interface ProjectionResult {
  deal_id: string;
  months: number;
  scenarios: string[];
  /** Keyed by scenario name (e.g. "base", "stress"). */
  projections: Record<string, WaterfallProjection>;
}

export function postProjection(
  body: ProjectRequest = {},
  dealId: string = DEFAULT_DEAL_ID,
): Promise<ProjectionResult> {
  return request<ProjectionResult>(`/deal/${dealId}/project`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
