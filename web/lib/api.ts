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
// Deal registry  —  GET /deals
// (config-driven DEAL_REGISTRY → list of DealSummary, see #131)
// ---------------------------------------------------------------------------

/**
 * One available deal — id + display name — from `GET /deals`. The `id` is the
 * value to thread into the `/deal/{id}/...` routes; the deal selector in the
 * top bar populates from this list (see web/components/deal-selector.tsx).
 */
export interface DealSummary {
  id: string;
  name: string;
}

export function getDeals(): Promise<DealSummary[]> {
  return request<DealSummary[]>("/deals");
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
// Governance evidence pack  —  GET /governance/{pack_id}
// (EvidencePackLogger.load → GovernanceEvidencePackResponse: the auditable
// trail behind one /query answer — tool-call trace, per-tool/aggregate
// confidence, deduplicated citations, human-review flag, FINOS metadata.)
// ---------------------------------------------------------------------------

/**
 * One source reference attached to a tool call or aggregated on the pack.
 * Mirrors the backend `Citation` shape (`{document, page_or_row, excerpt}`);
 * fields are optional/extensible since the API types citations as bare dicts.
 */
export interface Citation {
  document?: string;
  page_or_row?: string;
  excerpt?: string;
  [key: string]: unknown;
}

/** One agent tool call within a query (mirrors `ToolCallRecordModel`). */
export interface ToolCallRecord {
  /** 0-based position in the tool-call sequence. */
  call_index: number;
  tool_name: string;
  input_summary: string;
  output_summary: string;
  /** Confidence score for this call, [0.0, 1.0]. */
  confidence: number;
  citations: Citation[];
  duration_ms: number;
  timestamp: string;
}

/**
 * Mirrors `GovernanceEvidencePackResponse` — the complete, auditable evidence
 * behind one `/query` answer: the audit trail (query/answer/timestamp +
 * ordered tool-call records), per-tool and aggregate confidence, the
 * deduplicated citation trail, the human-review flag, and FINOS metadata.
 */
export interface GovernanceEvidencePack {
  pack_id: string;
  query: string;
  answer: string;
  timestamp: string;

  tool_calls: ToolCallRecord[];
  /** Min of all tool-call confidences (1.0 when no tools ran), [0.0, 1.0]. */
  aggregate_confidence: number;
  all_citations: Citation[];
  /** True when aggregate_confidence < 0.7. */
  human_review_required: boolean;

  model_used: string;
  framework_version: string;
  finos_compliant: boolean;
}

export function getGovernance(packId: string): Promise<GovernanceEvidencePack> {
  return request<GovernanceEvidencePack>(
    `/governance/${encodeURIComponent(packId)}`,
  );
}

// ---------------------------------------------------------------------------
// Deal model  —  GET /deal/{deal_id}/model
// (DealModelResponse: deal config + cached extracted DealModel)
// ---------------------------------------------------------------------------

export interface TapeRef {
  date: string;
  url: string;
}

export interface InvestorReportRef {
  period: string;
  url: string;
}

/**
 * One note class in the extracted capital structure, from the assembler's
 * `DealModel.tranche_structure` (`{name, size_eur, rating, rate, seniority}`,
 * ordered senior→junior). Sizes / ratings / coupons are `null` when only the
 * seniority skeleton could be derived (degraded extraction).
 */
export interface Tranche {
  name: string;
  size_eur: number | null;
  rating: string | null;
  rate: string | null;
  /** 0-based seniority — 0 is most senior (Class A). */
  seniority: number;
}

/** Provenance / quality metadata, from `DealModelMetadata`. */
export interface DealModelMetadata {
  deal_name: string;
  prospectus_url: string;
  extracted_at: string;
  extraction_duration_sec: number;
  sections_found: string[];
  /** 0–1: fraction of expected prospectus sections found. */
  completeness_score: number;
  cache_path: string;
  [key: string]: unknown;
}

/**
 * The extracted `DealModel` (`assembler.DealModel.model_dump()`), nested under
 * `deal_model` on a cache hit. Only the fields the frontend renders are typed;
 * the rest (definitions, waterfalls, covenants) are left as forward-compatible
 * extras.
 */
export interface ExtractedDealModel {
  metadata: DealModelMetadata;
  tranche_structure: Tranche[];
  trigger_names: string[];
  [key: string]: unknown;
}

/**
 * Mirrors `DealModelResponse` — the deal config (name + document URLs) plus the
 * cached extracted model when present. On a cache miss the endpoint returns
 * `extraction_status: "not_cached"` with `deal_model: null` rather than blocking
 * on a cold (~10min) extraction.
 */
export interface DealModel {
  deal_name: string;
  prospectus_url: string;
  tape_urls: TapeRef[];
  investor_report_urls: InvestorReportRef[];

  /** "cached" | "not_cached" */
  extraction_status: string;
  completeness_score: number | null;
  trigger_names: string[] | null;
  deal_model: ExtractedDealModel | null;
}

export function getDealModel(
  dealId: string = DEFAULT_DEAL_ID,
): Promise<DealModel> {
  return request<DealModel>(`/deal/${dealId}/model`);
}

// ---------------------------------------------------------------------------
// Tape analytics  —  GET /deal/{deal_id}/tape-analytics
// (EsmaTapeNormaliser → list of EsmaTapeOutput, one per reporting period)
// ---------------------------------------------------------------------------

/**
 * Per-period pool analytics for one ESMA tape — mirrors `TapeAnalyticsPeriod`
 * (the `EsmaTapeOutput` fields plus the deal's registered `tape_date`).
 *
 * `pool_stats` carries balance-weighted averages keyed by stat name
 * (`wtd_coupon_pct`, `wtd_ltv`, `wtd_seasoning`, `wtd_remaining_term`); a key
 * is absent when the source column wasn't present in the tape. `*_breakdown`
 * maps are percentage distributions (0–100) and are `null` when the relevant
 * column is missing from the annex.
 */
export interface TapeAnalyticsPeriod {
  tape_date: string;
  reporting_date: string;
  asset_class: string;
  transaction_name: string | null;
  loan_count: number;
  pool_balance_eur: number;
  pool_stats: Record<string, number>;
  arrears_breakdown: Record<string, number>;
  epc_breakdown: Record<string, number> | null;
  rate_type_breakdown: Record<string, number> | null;
  property_type_breakdown: Record<string, number> | null;
  geographic_breakdown: Record<string, number> | null;
  annex_detected: string;
}

export function getTapeAnalytics(
  dealId: string = DEFAULT_DEAL_ID,
): Promise<TapeAnalyticsPeriod[]> {
  return request<TapeAnalyticsPeriod[]>(`/deal/${dealId}/tape-analytics`);
}

// ---------------------------------------------------------------------------
// Compliance  —  GET /deal/{deal_id}/compliance
// (CovenantMonitor → CovenantOutput)
// ---------------------------------------------------------------------------

/** One `TriggerStatus` — a trigger evaluated for one reporting period. */
export interface TriggerStatus {
  trigger_name: string;
  period: string;
  /** `null` when the metric could not be resolved (`evaluable` false). */
  metric_value: number | null;
  threshold: number | null;
  is_triggered: boolean;
  /**
   * 0–100+: 100 = at threshold, >100 = breached. `null` when not evaluable —
   * an honest "couldn't measure" reads differently from a genuine 0.
   */
  proximity_pct: number | null;
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
// Waterfall  —  GET /deal/{deal_id}/waterfall
// (CollectionsAggregator → WaterfallRunner, latest reported period)
// ---------------------------------------------------------------------------

/**
 * Mirrors `WaterfallResponse` — the 11-step Revenue Priority of Payments
 * cascade and the per-tranche (Class A/B/C) distributions for the deal's latest
 * reported period, plus the Available Revenue / Principal Funds it ran on.
 */
export interface WaterfallResult {
  deal_id: string;
  reporting_period: string;
  available_revenue_funds: number;
  available_principal_funds: number;
  revenue_waterfall: WaterfallStep[];
  tranche_distributions: TrancheDistribution[];
  total_distributed: number;
  shortfall: number;
}

export function getWaterfall(
  dealId: string = DEFAULT_DEAL_ID,
): Promise<WaterfallResult> {
  return request<WaterfallResult>(`/deal/${dealId}/waterfall`);
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

/**
 * Mirrors `WaterfallOutput` — one scenario's projected waterfall, with the
 * Class A weighted-average life (WAL) the `/project` endpoint now surfaces
 * additively on each scenario.
 */
export interface WaterfallProjection {
  reporting_period: string;
  revenue_waterfall: WaterfallStep[];
  redemption_waterfall: WaterfallStep[];
  tranche_distributions: TrancheDistribution[];
  total_distributed: number;
  shortfall: number;
  /** Class A weighted-average life in months over the projection horizon. */
  wal_class_a_months: number;
  /** `wal_class_a_months / 12`. */
  wal_class_a_years: number;
}

/** Class A WAL for one scenario — mirrors `ScenarioWal`. */
export interface ScenarioWal {
  wal_class_a_months: number;
  wal_class_a_years: number;
}

export interface ProjectionResult {
  deal_id: string;
  months: number;
  scenarios: string[];
  /** Keyed by scenario name (e.g. "base", "stress"). */
  projections: Record<string, WaterfallProjection>;
  /** Per-scenario Class A WAL, keyed by scenario name. */
  wal: Record<string, ScenarioWal>;
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

// ---------------------------------------------------------------------------
// Primitive registry catalogue  —  GET /primitives  (#137)
// (PRIMITIVE_REGISTRY.describe() + each primitive class's typed I/O schemas)
// ---------------------------------------------------------------------------

/**
 * A minimal slice of a Pydantic-emitted JSON Schema — enough to render a
 * primitive's typed I/O contract (field names + types) without a full schema
 * viewer. The catalogue carries the standard `model_json_schema()` shape:
 * `type: "object"` with a `properties` map and an optional `required` list;
 * `$defs` holds referenced sub-models. Left forward-compatible with extras.
 */
export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  anyOf?: JsonSchema[];
  enum?: unknown[];
  [key: string]: unknown;
}

/**
 * One primitive in the registry catalogue — mirrors `PrimitiveCatalogueEntry`
 * in `src/loanwhiz/api/main.py`. Combines registry metadata (name, version,
 * description, author, tags, implementing class) with the primitive's typed
 * input/output JSON schemas and a note on the framework's confidence semantics.
 */
export interface PrimitiveCatalogueEntry {
  name: string;
  version: string;
  description: string;
  author: string;
  tags: string[];
  class_name: string;
  /**
   * Whether the primitive is reachable in the live path: `"live"` (called by a
   * REST endpoint and/or exposed as an agent tool) or `"library-only"`
   * (registered and importable, but reached by no endpoint or agent tool).
   * Optional so the page degrades gracefully against an older API that omits
   * the field — the catalogue then treats it as `library-only`.
   */
  reachability?: "live" | "library-only";
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  confidence: string;
}

export function getPrimitives(): Promise<PrimitiveCatalogueEntry[]> {
  return request<PrimitiveCatalogueEntry[]>("/primitives");
}
