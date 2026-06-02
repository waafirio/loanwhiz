import { z } from "zod";

export const CitationSchema = z.object({
  document: z.string(),
  page_or_row: z.string().optional(),
  excerpt: z.string(),
});

export const TrancheSchema = z.object({
  name: z.string(),
  rating: z.string().optional(),
  size_eur: z.number().optional(),
  attachment_point: z.number().optional(),
  detachment_point: z.number().optional(),
  coupon: z.string().optional(),
});

export const TriggerSchema = z.object({
  name: z.string(),
  description: z.string(),
  threshold: z.number().optional(),
  type: z.enum(["sequential", "pro_rata", "other"]).optional(),
});

export const ProspectusOutputSchema = z.object({
  deal_name: z.string(),
  issuer: z.string().optional(),
  asset_class: z.string(),
  jurisdiction: z.string().optional(),
  tranches: z.array(TrancheSchema),
  triggers: z.array(TriggerSchema),
  parties: z.object({
    originator: z.string().optional(),
    servicer: z.string().optional(),
    trustee: z.string().optional(),
    arranger: z.string().optional(),
  }),
  key_dates: z.object({
    closing: z.string().optional(),
    legal_maturity: z.string().optional(),
    call_date: z.string().optional(),
  }),
  pool_summary: z.string(),
  citations: z.array(CitationSchema),
});

export const PoolStatsSchema = z.object({
  wtd_coupon_pct: z.number().optional(),
  wtd_remaining_term_months: z.number().optional(),
  wtd_current_ltv_pct: z.number().optional(),
  wtd_original_ltv_pct: z.number().optional(),
  wtd_seasoning_months: z.number().optional(),
  wtd_loan_to_income: z.number().optional(),
  avg_loan_balance_eur: z.number().optional(),
});

export const ArrearsBreakdownSchema = z.object({
  current_pct: z.number(),
  arrears_1_2m_pct: z.number().optional(),
  arrears_2_3m_pct: z.number().optional(),
  arrears_3m_plus_pct: z.number().optional(),
  default_pct: z.number().optional(),
  foreclosure_pct: z.number().optional(),
});

export const EsmaTapeOutputSchema = z.object({
  reporting_date: z.string(),
  asset_class: z.string(),
  transaction_name: z.string().optional(),
  loan_count: z.number(),
  pool_balance_eur: z.number(),
  pool_stats: PoolStatsSchema,
  arrears_breakdown: ArrearsBreakdownSchema,
  epc_breakdown: z.record(z.string(), z.number()).optional(),
  rate_type_breakdown: z.record(z.string(), z.number()).optional(),
  property_type_breakdown: z.record(z.string(), z.number()).optional(),
  geographic_breakdown: z.record(z.string(), z.number()).optional(),
});

export const InvestorReportOutputSchema = z.object({
  deal_name: z.string(),
  period: z.string(),
  collections: z.object({
    scheduled_principal: z.number().optional(),
    unscheduled_principal: z.number().optional(),
    interest: z.number().optional(),
    total: z.number().optional(),
  }),
  credit_events: z.object({
    new_defaults_count: z.number().optional(),
    new_default_balance: z.number().optional(),
    recoveries: z.number().optional(),
    cumulative_losses: z.number().optional(),
    cumulative_loss_rate_pct: z.number().optional(),
  }),
  reserve_fund: z.object({
    current: z.number().optional(),
    required: z.number().optional(),
    shortfall: z.number().optional(),
  }).optional(),
  tranche_balances: z.array(z.object({
    name: z.string(),
    opening_balance: z.number().optional(),
    closing_balance: z.number().optional(),
    interest_paid: z.number().optional(),
  })).optional(),
  waterfall_summary: z.string(),
  citations: z.array(CitationSchema),
});

export const PeriodChangeSchema = z.object({
  metric: z.string(),
  period_a_value: z.number(),
  period_b_value: z.number(),
  delta: z.number(),
  delta_pct: z.number(),
  direction: z.enum(["improving", "deteriorating", "stable"]),
});

export const DiffOutputSchema = z.object({
  period_a_date: z.string(),
  period_b_date: z.string(),
  changes: z.array(PeriodChangeSchema),
  narrative: z.string(),
});

export const DealContextSchema = z.object({
  deal_name: z.string(),
  prospectus_url: z.string().optional(),
  tape_urls: z.array(z.object({
    date: z.string(),
    url: z.string(),
  })).optional(),
  investor_report_urls: z.array(z.object({
    period: z.string(),
    url: z.string(),
  })).optional(),
});

export const QueryDealOutputSchema = z.object({
  answer: z.string(),
  confidence: z.enum(["high", "medium", "low"]),
  sources: z.array(CitationSchema),
  tool_calls_made: z.array(z.string()),
});

export type Citation = z.infer<typeof CitationSchema>;
export type Tranche = z.infer<typeof TrancheSchema>;
export type ProspectusOutput = z.infer<typeof ProspectusOutputSchema>;
export type EsmaTapeOutput = z.infer<typeof EsmaTapeOutputSchema>;
export type InvestorReportOutput = z.infer<typeof InvestorReportOutputSchema>;
export type DiffOutput = z.infer<typeof DiffOutputSchema>;
export type DealContext = z.infer<typeof DealContextSchema>;
export type QueryDealOutput = z.infer<typeof QueryDealOutputSchema>;
