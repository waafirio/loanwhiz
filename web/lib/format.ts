/**
 * Tiny pure formatting helpers shared by the five backend pages.
 *
 * Lean by design (see web/CONTRACT.md): no dependencies, no locale config
 * beyond the demo's EUR deal currency. Keep these dumb and side-effect free.
 */

const eur = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

/** Format a EUR amount with no decimals (deal amounts are large). */
export function formatCurrency(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return eur.format(value);
}

/** Format a number as a percentage string, e.g. 96.4 → "96.4%". */
export function formatPct(value: number, fractionDigits = 1): string {
  if (!Number.isFinite(value)) return "—";
  return `${value.toFixed(fractionDigits)}%`;
}

/** Title-case a snake_case tranche/scenario key, e.g. "class_a" → "Class A". */
export function humanize(key: string): string {
  return key
    .split(/[_\s]+/)
    .map((w) => (w.length <= 1 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)))
    .join(" ");
}
