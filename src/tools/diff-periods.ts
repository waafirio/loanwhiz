import type { EsmaTapeOutput } from "../types.js";

export interface PeriodChange {
  metric: string;
  period_a_value: number;
  period_b_value: number;
  delta: number;
  delta_pct: number;
  direction: "improving" | "deteriorating" | "stable";
}

export interface DiffOutput {
  period_a_date: string;
  period_b_date: string;
  changes: PeriodChange[];
}

const DETERIORATION_ON_INCREASE = new Set([
  "default_pct", "arrears_1_2m_pct", "arrears_3m_plus_pct",
  "wtd_current_ltv_pct",
]);

const DETERIORATION_ON_DECREASE = new Set([
  "current_pct", "pool_balance_eur", "loan_count",
]);

function direction(metric: string, delta: number): "improving" | "deteriorating" | "stable" {
  if (Math.abs(delta) < 0.001) return "stable";
  if (DETERIORATION_ON_INCREASE.has(metric)) return delta > 0 ? "deteriorating" : "improving";
  if (DETERIORATION_ON_DECREASE.has(metric)) return delta < 0 ? "deteriorating" : "improving";
  return "stable";
}

function extractMetrics(o: EsmaTapeOutput): Record<string, number> {
  return {
    pool_balance_eur: o.pool_balance_eur,
    loan_count: o.loan_count,
    current_pct: o.arrears_breakdown.current_pct,
    ...(o.arrears_breakdown.default_pct != null && { default_pct: o.arrears_breakdown.default_pct }),
    ...(o.arrears_breakdown.arrears_1_2m_pct != null && { arrears_1_2m_pct: o.arrears_breakdown.arrears_1_2m_pct }),
    ...(o.arrears_breakdown.arrears_3m_plus_pct != null && { arrears_3m_plus_pct: o.arrears_breakdown.arrears_3m_plus_pct }),
    ...(o.pool_stats.wtd_current_ltv_pct != null && { wtd_current_ltv_pct: o.pool_stats.wtd_current_ltv_pct }),
    ...(o.pool_stats.wtd_coupon_pct != null && { wtd_coupon_pct: o.pool_stats.wtd_coupon_pct }),
    ...(o.pool_stats.wtd_seasoning_months != null && { wtd_seasoning_months: o.pool_stats.wtd_seasoning_months }),
    ...(o.pool_stats.avg_loan_balance_eur != null && { avg_loan_balance_eur: o.pool_stats.avg_loan_balance_eur }),
  };
}

export function diffPeriods(periodA: EsmaTapeOutput, periodB: EsmaTapeOutput): DiffOutput {
  const metricsA = extractMetrics(periodA);
  const metricsB = extractMetrics(periodB);
  const allKeys = new Set([...Object.keys(metricsA), ...Object.keys(metricsB)]);

  const changes: PeriodChange[] = [];
  for (const key of allKeys) {
    const a = metricsA[key];
    const b = metricsB[key];
    if (a === undefined || b === undefined) continue;
    const delta = b - a;
    const deltaPct = a !== 0 ? (delta / Math.abs(a)) * 100 : 0;
    changes.push({
      metric: key,
      period_a_value: Math.round(a * 1000) / 1000,
      period_b_value: Math.round(b * 1000) / 1000,
      delta: Math.round(delta * 1000) / 1000,
      delta_pct: Math.round(deltaPct * 100) / 100,
      direction: direction(key, delta),
    });
  }

  return {
    period_a_date: periodA.reporting_date,
    period_b_date: periodB.reporting_date,
    changes,
  };
}
