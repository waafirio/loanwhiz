import { loadCsvFromUrl, sumField, wtdAvg, groupByField, numericField } from "../lib/csv.js";
import type { EsmaTapeOutput } from "../types.js";

// ESMA field mappings for Dutch RMBS (Green Lion schema)
const FIELDS = {
  current_balance: "current_balance",
  original_balance: "original_balance",
  current_rate: "current_interest_rate_pct",
  remaining_term: "remaining_term_months",
  current_ltv: "cltomv_current",
  original_ltv: "oltomv_original",
  seasoning: "seasoning_months",
  loan_to_income: "loan_to_income",
  performing_status: "performing_status",
  arrears_bucket: "arrears_bucket",
  default_flag: "default_crr_flag",
  foreclosure_flag: "foreclosure_flag",
  epc_label: "epc_label",
  rate_type: "rate_type",
  property_type: "property_type",
  province: "province",
  transaction_name: "transaction_name",
  reporting_date: "reporting_date",
};

function detectAssetClass(headers: string[]): string {
  if (headers.includes("property_type") || headers.includes("epc_label")) return "RMBS";
  if (headers.includes("vehicle_type")) return "Auto ABS";
  if (headers.includes("company_type")) return "SME";
  return "ABS";
}

function computeArrearsBreakdown(records: Record<string, string>[]): EsmaTapeOutput["arrears_breakdown"] {
  const total = records.length;
  if (total === 0) return { current_pct: 0 };

  const pct = (n: number) => Math.round((n / total) * 10000) / 100;

  const current = records.filter(r =>
    r[FIELDS.performing_status] === "Non-defaulted" && r[FIELDS.arrears_bucket] === "Performing"
  ).length;
  const arrears29d = records.filter(r => r[FIELDS.arrears_bucket] === "<29d").length;
  const arrears180d = records.filter(r => r[FIELDS.arrears_bucket] === "180+d").length;
  const defaulted = records.filter(r =>
    r[FIELDS.default_flag] === "Y" || r[FIELDS.performing_status] === "Defaulted"
  ).length;
  const foreclosed = records.filter(r => r[FIELDS.foreclosure_flag] === "Y").length;

  return {
    current_pct: pct(current),
    arrears_1_2m_pct: pct(arrears29d),   // <29d bucket
    arrears_2_3m_pct: 0,
    arrears_3m_plus_pct: pct(arrears180d),
    default_pct: pct(defaulted),
    foreclosure_pct: pct(foreclosed),
  };
}

export async function loadEsmaTape(
  fileUrl: string,
  reportingDateOverride?: string
): Promise<EsmaTapeOutput> {
  const records = await loadCsvFromUrl(fileUrl);
  if (records.length === 0) throw new Error("Empty loan tape");

  const headers = Object.keys(records[0]);
  const assetClass = detectAssetClass(headers);

  const poolBalance = sumField(records, FIELDS.current_balance);
  const reportingDate =
    reportingDateOverride ??
    records[0][FIELDS.reporting_date] ??
    "unknown";

  const transactionName = records[0][FIELDS.transaction_name];

  const poolStats: EsmaTapeOutput["pool_stats"] = {
    wtd_coupon_pct: wtdAvg(records, FIELDS.current_rate, FIELDS.current_balance),
    wtd_remaining_term_months: wtdAvg(records, FIELDS.remaining_term, FIELDS.current_balance),
    wtd_current_ltv_pct: wtdAvg(records, FIELDS.current_ltv, FIELDS.current_balance),
    wtd_original_ltv_pct: wtdAvg(records, FIELDS.original_ltv, FIELDS.original_balance),
    wtd_seasoning_months: wtdAvg(records, FIELDS.seasoning, FIELDS.current_balance),
    wtd_loan_to_income: wtdAvg(records, FIELDS.loan_to_income, FIELDS.current_balance),
    avg_loan_balance_eur: poolBalance / records.length,
  };

  return {
    reporting_date: reportingDate,
    asset_class: assetClass,
    transaction_name: transactionName,
    loan_count: records.length,
    pool_balance_eur: poolBalance,
    pool_stats: poolStats,
    arrears_breakdown: computeArrearsBreakdown(records),
    epc_breakdown: headers.includes(FIELDS.epc_label)
      ? groupByField(records, FIELDS.epc_label, FIELDS.current_balance)
      : undefined,
    rate_type_breakdown: headers.includes(FIELDS.rate_type)
      ? groupByField(records, FIELDS.rate_type, FIELDS.current_balance)
      : undefined,
    property_type_breakdown: headers.includes(FIELDS.property_type)
      ? groupByField(records, FIELDS.property_type, FIELDS.current_balance)
      : undefined,
    geographic_breakdown: headers.includes(FIELDS.province)
      ? groupByField(records, FIELDS.province, FIELDS.current_balance)
      : undefined,
  };
}
