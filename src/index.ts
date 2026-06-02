import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { parseProspectus } from "./tools/parse-prospectus.js";
import { loadEsmaTape } from "./tools/load-esma-tape.js";
import { analyzeInvestorReport } from "./tools/analyze-investor-report.js";
import { diffPeriods } from "./tools/diff-periods.js";
import { queryDeal } from "./tools/query-deal.js";
import { DealContextSchema } from "./types.js";

if (!process.env.ANTHROPIC_API_KEY) {
  console.error("Error: ANTHROPIC_API_KEY environment variable is required");
  process.exit(1);
}

const server = new McpServer({
  name: "sf-mcp",
  version: "0.1.0",
});

server.tool(
  "parse_prospectus",
  "Extract structured deal information from an ABS/RMBS/CLO prospectus PDF. Returns tranches, triggers, parties, key dates, and pool summary with source citations.",
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
  "Load and analyse an ESMA-format loan-level tape CSV. Returns pool statistics, arrears breakdown, weighted averages, and geographic/EPC breakdowns.",
  {
    file_url: z.string().url().describe("URL of the ESMA loan tape CSV"),
    reporting_date: z.string().optional().describe("Override reporting date (YYYY-MM-DD) if not in the file"),
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
  "Extract performance data from an ABS/RMBS monthly or quarterly investor/trustee report PDF. Returns collections, credit events, reserve fund status, tranche balances, and waterfall summary.",
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
  "Compare two loan tape snapshots or investor reports period-over-period. Identifies improving vs deteriorating metrics and generates a plain English narrative.",
  {
    period_a: z.record(z.unknown()).describe("Earlier period output from load_esma_tape or analyze_investor_report"),
    period_b: z.record(z.unknown()).describe("Later period output from load_esma_tape or analyze_investor_report"),
  },
  async ({ period_a, period_b }) => {
    const result = await diffPeriods(period_a as any, period_b as any);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  }
);

server.tool(
  "query_deal",
  "Answer a natural language question about a structured finance deal using all available data sources (prospectus, ESMA tapes, investor reports). Returns a grounded answer with confidence level, citations, and an audit trail of which tools were called.",
  {
    question: z.string().describe("Natural language question about the deal"),
    deal_context: DealContextSchema.describe("Deal context with URLs for available data sources"),
  },
  async ({ question, deal_context }) => {
    const result = await queryDeal(question, deal_context);
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
