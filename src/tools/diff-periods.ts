import type { EsmaTapeOutput, InvestorReportOutput, DiffOutput, PeriodChangeSchema } from "../types.js";
import { z } from "zod";
import { chat } from "../lib/llm.js";

type PeriodChange = z.infer<typeof PeriodChangeSchema>;

type SnapshottableOutput = EsmaTapeOutput | InvestorReportOutput;

function isEsmaTape(o: SnapshottableOutput): o is EsmaTapeOutput {
  return "loan_count" in o;
}

function isInvestorReport(o: SnapshottableOutput): o is InvestorReportOutput {
  return "waterfall_summary" in o;
}

function getDate(o: SnapshottableOutput): string {
  if (isEsmaTape(o)) return o.reporting_date;
  if (isInvestorReport(o)) return o.period;
  return "unknown";
}

function extractMetrics(o: SnapshottableOutput): Record<string, number> {
  const metrics: Record<string, number> = {};

  if (isEsmaTape(o)) {
    metrics["pool_balance_eur"] = o.pool_balance_eur;
    metrics["loan_count"] = o.loan_count;
    metrics["current_pct"] = o.arrears_breakdown.current_pct;
    if (o.arrears_breakdown.default_pct != null) metrics["default_pct"] = o.arrears_breakdown.default_pct;
    if (o.arrears_breakdown.arrears_3m_plus_pct != null) metrics["arrears_3m_plus_pct"] = o.arrears_breakdown.arrears_3m_plus_pct;
    if (o.pool_stats.wtd_current_ltv_pct != null) metrics["wtd_current_ltv_pct"] = o.pool_stats.wtd_current_ltv_pct;
    if (o.pool_stats.wtd_coupon_pct != null) metrics["wtd_coupon_pct"] = o.pool_stats.wtd_coupon_pct;
    if (o.pool_stats.avg_loan_balance_eur != null) metrics["avg_loan_balance_eur"] = o.pool_stats.avg_loan_balance_eur;
  }

  if (isInvestorReport(o)) {
    if (o.collections.total != null) metrics["collections_total"] = o.collections.total;
    if (o.collections.scheduled_principal != null) metrics["scheduled_principal"] = o.collections.scheduled_principal;
    if (o.credit_events.cumulative_losses != null) metrics["cumulative_losses"] = o.credit_events.cumulative_losses;
    if (o.credit_events.cumulative_loss_rate_pct != null) metrics["cumulative_loss_rate_pct"] = o.credit_events.cumulative_loss_rate_pct;
    if (o.reserve_fund?.current != null) metrics["reserve_fund_current"] = o.reserve_fund.current;
    if (o.reserve_fund?.shortfall != null) metrics["reserve_fund_shortfall"] = o.reserve_fund.shortfall;
  }

  return metrics;
}

// For credit metrics, higher = worse (deteriorating); for balance/current_pct, higher = better
const DETERIORATION_ON_INCREASE = new Set([
  "default_pct", "arrears_3m_plus_pct", "cumulative_losses",
  "cumulative_loss_rate_pct", "reserve_fund_shortfall", "wtd_current_ltv_pct",
]);

const DETERIORATION_ON_DECREASE = new Set([
  "current_pct", "pool_balance_eur", "collections_total",
  "reserve_fund_current", "loan_count",
]);

function direction(metric: string, delta: number): "improving" | "deteriorating" | "stable" {
  if (Math.abs(delta) < 0.001) return "stable";
  if (DETERIORATION_ON_INCREASE.has(metric)) return delta > 0 ? "deteriorating" : "improving";
  if (DETERIORATION_ON_DECREASE.has(metric)) return delta < 0 ? "deteriorating" : "improving";
  return "stable";
}

export async function diffPeriods(
  periodA: SnapshottableOutput,
  periodB: SnapshottableOutput
): Promise<DiffOutput> {
  const dateA = getDate(periodA);
  const dateB = getDate(periodB);
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
      period_a_value: a,
      period_b_value: b,
      delta: Math.round(delta * 100) / 100,
      delta_pct: Math.round(deltaPct * 100) / 100,
      direction: direction(key, delta),
    });
  }

  const changesText = changes
    .map(c => `${c.metric}: ${c.period_a_value} → ${c.period_b_value} (${c.delta_pct > 0 ? "+" : ""}${c.delta_pct}%, ${c.direction})`)
    .join("\n");

  const narrative = await chat({
    system: "You are a structured finance analyst. Write a concise 3-4 sentence narrative summarising the period-over-period changes in a loan pool.",
    messages: [{
      role: "user",
      content: `Period A: ${dateA}\nPeriod B: ${dateB}\n\nMetric changes:\n${changesText}\n\nWrite a plain English summary for an investor.`,
    }],
    maxTokens: 512,
  });

  return { period_a_date: dateA, period_b_date: dateB, changes, narrative };
}
