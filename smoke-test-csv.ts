// Tape-only smoke test — no ANTHROPIC_API_KEY needed
// Run with: npx tsx smoke-test-csv.ts
import { loadEsmaTape } from "./src/tools/load-esma-tape.js";
import { diffPeriods } from "./src/tools/diff-periods.js";

const HF_BASE = "https://huggingface.co/datasets/Algoritmica/green-lion-2026/resolve/main/Hackathon_Data";

async function run() {
  console.log("\n=== CSV SMOKE TEST: Green Lion 2026-1 loan tapes ===\n");

  console.log("Loading Feb tape...");
  const feb = await loadEsmaTape(`${HF_BASE}/green_lion_202602_1_synthetic_loan_tape.csv`);
  console.log(`Feb: ${feb.loan_count} loans | €${(feb.pool_balance_eur / 1e6).toFixed(2)}m`);

  console.log("Loading Mar tape...");
  const mar = await loadEsmaTape(`${HF_BASE}/green_lion_202603_1_synthetic_loan_tape.csv`);
  console.log(`Mar: ${mar.loan_count} loans | €${(mar.pool_balance_eur / 1e6).toFixed(2)}m`);

  console.log("Loading Apr tape...");
  const apr = await loadEsmaTape(`${HF_BASE}/green_lion_2026_1_synthetic_loan_tape.csv`);
  console.log(`Apr: ${apr.loan_count} loans | €${(apr.pool_balance_eur / 1e6).toFixed(2)}m`);

  console.log("\nFull Apr tape output:");
  console.log(JSON.stringify(apr, null, 2));

  console.log("\nDiff Feb → Apr (no LLM narrative)...");
  // Manually diff without LLM to avoid needing API key
  const metrics = (t: typeof apr) => ({
    pool_balance_eur: t.pool_balance_eur,
    loan_count: t.loan_count,
    current_pct: t.arrears_breakdown.current_pct,
    default_pct: t.arrears_breakdown.default_pct ?? 0,
    wtd_ltv: t.pool_stats.wtd_current_ltv_pct ?? 0,
  });
  const mFeb = metrics(feb);
  const mApr = metrics(apr);
  for (const [k, v] of Object.entries(mApr)) {
    const prev = mFeb[k as keyof typeof mFeb];
    const delta = v - prev;
    const pct = prev !== 0 ? ((delta / prev) * 100).toFixed(2) : "N/A";
    console.log(`  ${k}: ${prev} → ${v} (${delta > 0 ? "+" : ""}${pct}%)`);
  }

  console.log("\n=== CSV TESTS PASSED ===");
}

run().catch((err) => {
  console.error("Failed:", err.message);
  process.exit(1);
});
