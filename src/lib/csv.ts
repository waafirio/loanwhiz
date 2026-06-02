import { parse } from "csv-parse/sync";

export type LoanRecord = Record<string, string>;

export async function loadCsvFromUrl(url: string): Promise<LoanRecord[]> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch CSV: ${response.status} ${response.statusText}`);
  }
  const text = await response.text();
  return parseCsvText(text);
}

export function parseCsvText(text: string): LoanRecord[] {
  return parse(text, {
    columns: true,
    skip_empty_lines: true,
    trim: true,
  }) as LoanRecord[];
}

export function numericField(record: LoanRecord, field: string): number | undefined {
  const val = record[field];
  if (!val || val === "" || val === "N/A") return undefined;
  const n = parseFloat(val);
  return isNaN(n) ? undefined : n;
}

export function sumField(records: LoanRecord[], field: string): number {
  return records.reduce((acc, r) => acc + (numericField(r, field) ?? 0), 0);
}

export function wtdAvg(
  records: LoanRecord[],
  valueField: string,
  weightField: string
): number | undefined {
  let weightedSum = 0;
  let totalWeight = 0;
  for (const r of records) {
    const val = numericField(r, valueField);
    const weight = numericField(r, weightField);
    if (val !== undefined && weight !== undefined && weight > 0) {
      weightedSum += val * weight;
      totalWeight += weight;
    }
  }
  return totalWeight > 0 ? weightedSum / totalWeight : undefined;
}

export function groupByField(
  records: LoanRecord[],
  field: string,
  weightField?: string
): Record<string, number> {
  const groups: Record<string, number> = {};
  const totalWeight = weightField ? sumField(records, weightField) : records.length;
  for (const r of records) {
    const key = r[field] ?? "unknown";
    const weight = weightField ? (numericField(r, weightField) ?? 0) : 1;
    groups[key] = (groups[key] ?? 0) + weight;
  }
  // Convert to percentages
  const result: Record<string, number> = {};
  for (const [k, v] of Object.entries(groups)) {
    result[k] = totalWeight > 0 ? Math.round((v / totalWeight) * 10000) / 100 : 0;
  }
  return result;
}
