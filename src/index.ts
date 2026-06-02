import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { parseProspectus } from "./tools/parse-prospectus.js";
import { loadEsmaTape } from "./tools/load-esma-tape.js";
import { analyzeInvestorReport } from "./tools/analyze-investor-report.js";
import { diffPeriods } from "./tools/diff-periods.js";
import { EsmaTapeOutputSchema } from "./types.js";

const server = new McpServer({
  name: "sf-mcp",
  version: "0.1.0",
});

server.tool(
  "parse_prospectus",
  "Extract the full text from an ABS/RMBS/CLO prospectus PDF. Returns the raw document text for analysis — look for tranches, triggers, parties, key dates, pool description, and waterfall mechanics.",
  { file_url: z.string().url().describe("URL of the prospectus PDF") },
  async ({ file_url }) => {
    const result = await parseProspectus(file_url);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  }
);

server.tool(
  "load_esma_tape",
  "Load and analyse an ESMA-format loan-level tape CSV. Computes pool statistics, weighted averages, arrears breakdown, and geographic/EPC/rate-type distributions. No LLM needed — results are deterministic.",
  {
    file_url: z.string().url().describe("URL of the ESMA loan tape CSV"),
    reporting_date: z.string().optional().describe("Override reporting date (YYYY-MM-DD) if not present in the file"),
  },
  async ({ file_url, reporting_date }) => {
    const result = await loadEsmaTape(file_url, reporting_date);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  }
);

server.tool(
  "analyze_investor_report",
  "Extract the full text from an ABS/RMBS monthly or quarterly investor/trustee report PDF. Returns the raw document text — look for collections, waterfall payments, credit events, reserve fund status, and tranche balances.",
  { file_url: z.string().url().describe("URL of the investor or trustee report PDF") },
  async ({ file_url }) => {
    const result = await analyzeInvestorReport(file_url);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  }
);

server.tool(
  "diff_periods",
  "Compare two ESMA loan tape snapshots period-over-period. Returns metric-by-metric changes with direction labels (improving/deteriorating/stable). Pass the full JSON output from two load_esma_tape calls.",
  {
    period_a: EsmaTapeOutputSchema.describe("Earlier period — full output from load_esma_tape"),
    period_b: EsmaTapeOutputSchema.describe("Later period — full output from load_esma_tape"),
  },
  async ({ period_a, period_b }) => {
    const result = diffPeriods(period_a, period_b);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  }
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("sf-mcp server running on stdio");
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
