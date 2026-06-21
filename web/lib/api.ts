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

/**
 * Ingestion provenance for an ESMA tape — mirrors the backend
 * `EsmaTapeOutput.data_source` (`esma_tape_normaliser.py`, issue #239).
 * `"deeploans"` = fetched through the deeploans ETL backend; `"direct"` = the
 * direct CSV/parquet URL read.
 */
export type DataSource = "deeploans" | "direct";

/**
 * Best-effort read of a tape citation's ingestion provenance. The ESMA tape
 * normaliser records it in the citation excerpt as "(ingested via deeploans)" /
 * "(ingested via direct)"; this parses that marker so the governance surface can
 * show honest provenance without a separate API field. Returns `null` when the
 * citation carries no provenance marker (e.g. a non-tape citation).
 */
export function citationDataSource(c: Citation): DataSource | null {
  if (c.data_source === "deeploans" || c.data_source === "direct") {
    return c.data_source;
  }
  const m = /ingested via (deeploans|direct)/i.exec(c.excerpt ?? "");
  return m ? (m[1].toLowerCase() as DataSource) : null;
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
  /** True iff the pack is internally consistent AND LoanWhiz conforms to the
   *  FINOS control catalogue (see `finos_conformance`). */
  finos_compliant: boolean;
  /** The framework-conformance summary explaining `finos_compliant`. May be an
   *  empty object for packs round-tripped from JSONL before this field existed. */
  finos_conformance?: FinosConformanceSummary | Record<string, never>;
}

export function getGovernance(packId: string): Promise<GovernanceEvidencePack> {
  return request<GovernanceEvidencePack>(
    `/governance/${encodeURIComponent(packId)}`,
  );
}

// ---------------------------------------------------------------------------
// FINOS framework conformance  —  GET /governance/finos-conformance
// (The mapped FINOS AI Governance Framework control catalogue + per-primitive
// conformance — the single source of truth that `finos_compliant` reflects.)
// ---------------------------------------------------------------------------

/** Honest conformance status for one mapped FINOS control. */
export type FinosConformanceStatus =
  | "satisfied"
  | "partial"
  | "not_applicable";

/** One FINOS mitigation control mapped to LoanWhiz (mirrors `FinosControl`). */
export interface FinosControl {
  control_id: string;
  title: string;
  category: "preventative" | "detective";
  addresses_risks: string[];
  status: FinosConformanceStatus;
  rationale: string;
  loanwhiz_evidence: string[];
}

/** Mirrors `finos_conformance_summary()` — the framework verdict + catalogue. */
export interface FinosConformanceSummary {
  framework: string;
  reference: string;
  is_conformant: boolean;
  total_controls: number;
  counts: {
    satisfied: number;
    partial: number;
    not_applicable: number;
  };
  controls: FinosControl[];
  /** Per-primitive control ids — the per-primitive conformance assertion. */
  primitive_conformance: Record<string, string[]>;
}

export function getFinosConformance(): Promise<FinosConformanceSummary> {
  return request<FinosConformanceSummary>("/governance/finos-conformance");
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
  /**
   * Ingestion provenance for this period's tape (#239). Optional so the page
   * degrades gracefully against an older API that omits the field.
   */
  data_source?: DataSource;
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

// ---------------------------------------------------------------------------
// Engine validation  —  GET /deal/{deal_id}/validation  (#212, V6)
// (engine_validation_harness → EngineValidationReport: the engine-vs-published
// Notes & Cash Priority of Payments reconciliation, to the cent, with honest
// per-step engine/report-supplied/residual source labels.)
// ---------------------------------------------------------------------------

/** Honesty label for a reconciled step — mirrors `StepReconciliation.source`. */
export type ValidationSource = "engine" | "report-supplied" | "residual";

/**
 * One reconciled priority step — mirrors `StepReconciliationModel`. `source`
 * distinguishes a line the engine COMPUTED from the extracted model with no
 * report input (`engine` — the independent proof) from one whose amount was
 * taken from the report and only routed by the engine (`report-supplied`), or
 * a terminal sweep of the remaining pot (`residual`).
 */
export interface ValidationStep {
  priority: string;
  recipient: string;
  engine_amount: number;
  report_amount: number;
  /** `engine_amount - report_amount` (signed). */
  delta: number;
  source: ValidationSource;
  passed: boolean;
}

/** One waterfall's per-step reconciliation — mirrors `WaterfallReconciliationModel`. */
export interface ValidationWaterfall {
  /** "revenue" | "redemption" */
  waterfall_type: string;
  steps: ValidationStep[];
  engine_total: number;
  report_total: number;
  available_funds: number;
  /** Report's documented "Unapplied … due to rounding" remainder (e.g. €0.69). */
  unapplied_rounding: number;
  steps_passed: number;
  passed: boolean;
}

/** One reporting period's revenue + redemption reconciliation. */
export interface ValidationPeriod {
  reporting_date: string;
  period_label: string;
  revenue: ValidationWaterfall;
  redemption: ValidationWaterfall;
  passed: boolean;
}

/**
 * Mirrors `ValidationResponse` — the engine-validation report for one deal.
 *
 * `available` is `false` for a registered deal with no committed validation
 * fixture (e.g. Green Lion 2023-1): the report fields are then empty and the UI
 * renders an honest "no published proof" state. When `true`, `periods` carries
 * the per-period reconciliation of our waterfall engine against the deal's own
 * published Notes & Cash Priority of Payments, to the cent.
 */
export interface ValidationReport {
  deal_id: string;
  deal_name: string;
  available: boolean;
  note: string | null;

  passed: boolean;
  periods_checked: number;
  periods_passed: number;
  tolerance_eur: number;
  source_note: string | null;
  summary: string | null;
  periods: ValidationPeriod[];
}

export function getValidation(
  dealId: string = DEFAULT_DEAL_ID,
): Promise<ValidationReport> {
  return request<ValidationReport>(`/deal/${dealId}/validation`);
}

// ---------------------------------------------------------------------------
// Cross-deal capability matrix  —  GET /capability-matrix  (#241, C3 / epic #236)
// (build_capability_matrix → CapabilityMatrix: the typed primitives × deals grid
// that makes primitive reusability *visible* across Dutch / Italian / Spanish
// deals. Each cell is `validated` / `ran` / `not-applicable` with an honest
// reason + governance evidence. The C4 /showcase view renders this.)
// ---------------------------------------------------------------------------

/** The three honest cell states — mirrors the backend STATE_* vocabulary. */
export type CapabilityCellState = "validated" | "ran" | "not-applicable";

/**
 * Governance evidence attached to one capability cell — mirrors `CellEvidence`.
 * `confidence` is in `[0,1]` for a cell that ran, or `null` for a
 * `not-applicable` cell (nothing ran, so no confidence). `citation` grounds the
 * evidence (the seed artifact, the published report reconciled against, etc.);
 * `detail` is free-form JSON-serialisable structured detail for the UI.
 */
export interface CellEvidence {
  confidence: number | null;
  citation: string;
  detail: Record<string, unknown>;
}

/**
 * One (capability × deal) cell — mirrors `CapabilityCell`. `reason` is mandatory
 * and non-empty (the honesty contract: every `not-applicable` skip carries its
 * real reason; for `ran` / `validated` it's a short positive note).
 */
export interface CapabilityCell {
  capability_key: string;
  deal_id: string;
  state: CapabilityCellState;
  reason: string;
  evidence: CellEvidence;
}

/** A capability (one matrix row) and its metadata — mirrors `CapabilityRow`. */
export interface CapabilityRow {
  key: string;
  primitive_name: string;
  label: string;
  description: string;
}

/** A deal (one matrix column) and its metadata — mirrors `DealColumn`. */
export interface DealColumn {
  deal_id: string;
  deal_name: string;
  jurisdiction: string;
  has_seed_model: boolean;
  /** Extracted-model completeness in [0,1], or null when no model loaded. */
  completeness_score: number | null;
}

/**
 * The full cross-deal capability matrix — mirrors `CapabilityMatrix`. `cells` is
 * the flat list of every (capability × deal) cell; `tally` is the per-state count
 * across all cells (e.g. `{validated: 1, ran: 9, "not-applicable": 15}`); `note`
 * is the standing honesty disclosure.
 */
export interface CapabilityMatrix {
  capabilities: CapabilityRow[];
  deals: DealColumn[];
  cells: CapabilityCell[];
  tally: Record<string, number>;
  note: string;
}

export function getCapabilityMatrix(): Promise<CapabilityMatrix> {
  return request<CapabilityMatrix>("/capability-matrix");
}

// ---------------------------------------------------------------------------
// Deal comparison  —  GET /compare?deals=a,b,c[&target=a]   (#283, Epic 7)
// (one N-way comparison payload: structural diff aligned by RecipientType /
// MetricType, overlaid performance series, latest-period covenant-proximity
// risk summary, and — with a target — comp-set medians + per-target deviations.
// Mirrors loanwhiz.api.compare.CompareResponse.)
// ---------------------------------------------------------------------------

/** One deal in the comparison set, with provenance/coverage flags. */
export interface CompareDealRef {
  deal_id: string;
  deal_name: string;
  jurisdiction: string;
  /** Origination year parsed from the deal name, or null. */
  vintage: number | null;
  is_target: boolean;
  /** A canonical DealRules was assembled (Panel 1 available for this deal). */
  has_structural: boolean;
  /** A DealStateSeries reconstructed (Panel 2 available for this deal). */
  has_performance: boolean;
  /**
   * Provenance of this deal's Panel-2 series: "reported" when reconstructed
   * from the deal's own tape/report history, "projected" when derived from the
   * canonical model's forward projection (projected-not-reported), or null when
   * no series is available.
   */
  performance_provenance: "reported" | "projected" | null;
  /** One-line honesty note when a panel is unavailable for this deal. */
  note: string | null;
}

/** One deal's value for one aligned structural row (Panel 1 cell). */
export interface StructuralCell {
  deal_id: string;
  present: boolean;
  /** The deal's own (issuer) label for the step/trigger. */
  label: string | null;
  /** Human-readable cell value (recipient basis / threshold). */
  detail: string | null;
  /** Numeric value for benchmarking (a normalised threshold / amount). */
  value: number | null;
  /** False for an `unmapped` recipient/metric — rendered "not comparable". */
  comparable: boolean;
  /** Comp-set median (set on every cell of a benchmarked row). */
  comp_median: number | null;
  /** value − comp_median (signed), set on the target cell only. */
  deviation: number | null;
}

/** One aligned row of the structural-diff table (a recipient or a metric). */
export interface StructuralRow {
  /** Canonical row key: a RecipientType / MetricType value (or a section key). */
  key: string;
  /** 'tranche' | 'waterfall:revenue' | 'waterfall:redemption' | 'trigger' | 'reserve'. */
  section: string;
  label: string;
  /** True when the deals' cells are not all equal — diff-highlight. */
  differs: boolean;
  cells: StructuralCell[];
}

/** One (period, metric-bundle) sample of a deal's overlaid Panel-2 series. */
export interface PerformancePoint {
  reporting_date: string;
  pool_factor: number;
  reserve_balance: number;
  reserve_target: number;
  total_pdl: number;
  cumulative_losses: number;
  cumulative_loss_rate_pct: number;
}

/** One deal's overlaid performance series. */
export interface PerformanceSeries {
  deal_id: string;
  points: PerformancePoint[];
}

/** Latest-period risk snapshot per deal (the triage row above Panel 2). */
export interface RiskSummary {
  deal_id: string;
  latest_period: string | null;
  /** Worst (closest-to-breach) covenant in the latest period. */
  tightest_trigger: string | null;
  tightest_proximity_pct: number | null;
  active_triggers: string[];
  near_miss_triggers: string[];
  latest_pool_factor: number | null;
  latest_cumulative_loss_rate_pct: number | null;
  comp_median_proximity_pct: number | null;
  proximity_deviation: number | null;
}

/** The single comparison payload the view + chat both consume. */
export interface CompareResponse {
  deals: CompareDealRef[];
  target_deal_id: string | null;
  structural_rows: StructuralRow[];
  performance_series: PerformanceSeries[];
  risk_summary: RiskSummary[];
  /** Reporting dates shared by all performance-bearing deals. */
  common_periods: string[];
  /** Registry deal_ids (not in the set) sharing the target's jurisdiction/vintage. */
  comp_suggestions: string[];
  /** Honesty notes about coverage / provenance differences. */
  notes: string[];
}

/**
 * Fetch one N-way comparison payload for `dealIds` (2..N). Pass `target` to
 * enable the benchmark lens (comp-set medians + per-target deviations).
 */
export function getCompare(
  dealIds: string[],
  target?: string,
): Promise<CompareResponse> {
  const params = new URLSearchParams({ deals: dealIds.join(",") });
  if (target) params.set("target", target);
  return request<CompareResponse>(`/compare?${params.toString()}`);
}
