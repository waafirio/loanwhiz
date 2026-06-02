import { extractPdfFromUrl, truncatePdfText } from "../lib/pdf.js";
import { extractStructured } from "../lib/llm.js";
import type { ProspectusOutput } from "../types.js";

const SYSTEM_PROMPT = `You are a structured finance analyst specialising in ABS/RMBS/CLO prospectus analysis.
Extract the deal structure precisely from the provided prospectus text.
Be conservative: if a field is not clearly stated, omit it rather than guessing.
Always include citations with page references and verbatim excerpts.`;

const SCHEMA = {
  type: "object",
  properties: {
    deal_name: { type: "string" },
    issuer: { type: "string" },
    asset_class: { type: "string", description: "e.g. RMBS, ABS, CLO, SME" },
    jurisdiction: { type: "string" },
    tranches: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          rating: { type: "string" },
          size_eur: { type: "number" },
          attachment_point: { type: "number" },
          detachment_point: { type: "number" },
          coupon: { type: "string" },
        },
        required: ["name"],
      },
    },
    triggers: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          description: { type: "string" },
          threshold: { type: "number" },
          type: { type: "string", enum: ["sequential", "pro_rata", "other"] },
        },
        required: ["name", "description"],
      },
    },
    parties: {
      type: "object",
      properties: {
        originator: { type: "string" },
        servicer: { type: "string" },
        trustee: { type: "string" },
        arranger: { type: "string" },
      },
    },
    key_dates: {
      type: "object",
      properties: {
        closing: { type: "string" },
        legal_maturity: { type: "string" },
        call_date: { type: "string" },
      },
    },
    pool_summary: {
      type: "string",
      description: "2-3 sentence summary of the underlying asset pool",
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
  required: ["deal_name", "asset_class", "tranches", "triggers", "parties", "key_dates", "pool_summary", "citations"],
};

export async function parseProspectus(fileUrl: string): Promise<ProspectusOutput> {
  const pdf = await extractPdfFromUrl(fileUrl);
  const text = truncatePdfText(pdf.text);

  return extractStructured<ProspectusOutput>({
    systemPrompt: SYSTEM_PROMPT,
    userContent: `Prospectus document (${pdf.pages} pages):\n\n${text}`,
    schema: SCHEMA,
    schemaName: "prospectus_extraction",
  });
}
