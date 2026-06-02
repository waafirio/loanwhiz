import { extractPdfFromUrl, truncatePdfText } from "../lib/pdf.js";
import { extractStructured } from "../lib/llm.js";
import type { InvestorReportOutput } from "../types.js";

const SYSTEM_PROMPT = `You are a structured finance analyst specialising in ABS/RMBS investor reports and trustee reports.
Extract the key performance metrics precisely from the provided report.
Focus on: collections waterfall, credit events, tranche balances, reserve fund, and arrears.
Be conservative: if a field is not clearly stated, omit it.
Always cite the source with page references and verbatim excerpts.`;

const SCHEMA = {
  type: "object",
  properties: {
    deal_name: { type: "string" },
    period: { type: "string", description: "Reporting period, e.g. 'April 2026'" },
    collections: {
      type: "object",
      properties: {
        scheduled_principal: { type: "number" },
        unscheduled_principal: { type: "number" },
        interest: { type: "number" },
        total: { type: "number" },
      },
    },
    credit_events: {
      type: "object",
      properties: {
        new_defaults_count: { type: "number" },
        new_default_balance: { type: "number" },
        recoveries: { type: "number" },
        cumulative_losses: { type: "number" },
        cumulative_loss_rate_pct: { type: "number" },
      },
    },
    reserve_fund: {
      type: "object",
      properties: {
        current: { type: "number" },
        required: { type: "number" },
        shortfall: { type: "number" },
      },
    },
    tranche_balances: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          opening_balance: { type: "number" },
          closing_balance: { type: "number" },
          interest_paid: { type: "number" },
        },
        required: ["name"],
      },
    },
    waterfall_summary: {
      type: "string",
      description: "2-3 sentence plain English summary of the payment waterfall for this period",
    },
    citations: {
      type: "array",
      items: {
        type: "object",
        properties: {
          document: { type: "string" },
          page_or_row: { type: "string" },
          excerpt: { type: "string" },
        },
        required: ["document", "excerpt"],
      },
    },
  },
  required: ["deal_name", "period", "collections", "credit_events", "waterfall_summary", "citations"],
};

export async function analyzeInvestorReport(fileUrl: string): Promise<InvestorReportOutput> {
  const pdf = await extractPdfFromUrl(fileUrl);
  const text = truncatePdfText(pdf.text, 60_000);

  return extractStructured<InvestorReportOutput>({
    systemPrompt: SYSTEM_PROMPT,
    userContent: `Investor/Trustee Report (${pdf.pages} pages):\n\n${text}`,
    schema: SCHEMA,
    schemaName: "investor_report_extraction",
  });
}
