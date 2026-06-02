import { z } from "zod";

export const EsmaTapeOutputSchema = z.object({
  reporting_date: z.string(),
  asset_class: z.string(),
  transaction_name: z.string().optional(),
  loan_count: z.number(),
  pool_balance_eur: z.number(),
  pool_stats: z.object({
    wtd_coupon_pct: z.number().optional(),
    wtd_remaining_term_months: z.number().optional(),
    wtd_current_ltv_pct: z.number().optional(),
    wtd_original_ltv_pct: z.number().optional(),
    wtd_seasoning_months: z.number().optional(),
    wtd_loan_to_income: z.number().optional(),
    avg_loan_balance_eur: z.number().optional(),
  }),
  arrears_breakdown: z.object({
    current_pct: z.number(),
    arrears_1_2m_pct: z.number().optional(),
    arrears_2_3m_pct: z.number().optional(),
    arrears_3m_plus_pct: z.number().optional(),
    default_pct: z.number().optional(),
    foreclosure_pct: z.number().optional(),
  }),
  epc_breakdown: z.record(z.string(), z.number()).optional(),
  rate_type_breakdown: z.record(z.string(), z.number()).optional(),
  property_type_breakdown: z.record(z.string(), z.number()).optional(),
  geographic_breakdown: z.record(z.string(), z.number()).optional(),
});

export type EsmaTapeOutput = z.infer<typeof EsmaTapeOutputSchema>;
