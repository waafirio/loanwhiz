import { chat } from "../lib/llm.js";
import { loadEsmaTape } from "./load-esma-tape.js";
import { parseProspectus } from "./parse-prospectus.js";
import { analyzeInvestorReport } from "./analyze-investor-report.js";
import type { DealContext, QueryDealOutput, Citation } from "../types.js";

export async function queryDeal(
  question: string,
  context: DealContext
): Promise<QueryDealOutput> {
  const toolCallsMade: string[] = [];
  const contextParts: string[] = [];
  const allCitations: Citation[] = [];

  // Determine which tools to invoke based on question keywords
  const q = question.toLowerCase();
  const needsTape = /arrear|default|ltv|balance|pool|loan|prepay|perform|rate|epc|geographic|concentrat/.test(q);
  const needsProspectus = /tranche|trigger|waterfall|party|originator|servicer|trustee|structure|rating|attach|detach|covenant|closing|maturity|call/.test(q);
  const needsReports = /collection|payment|recovery|loss|reserve|period|waterfall|distribut|interest paid/.test(q);

  // Always load the most recent tape if tape-related question
  if (needsTape && context.tape_urls?.length) {
    const sorted = [...context.tape_urls].sort((a, b) => b.date.localeCompare(a.date));
    const latest = sorted[0];
    try {
      toolCallsMade.push(`load_esma_tape(${latest.url})`);
      const tape = await loadEsmaTape(latest.url);
      contextParts.push(`ESMA LOAN TAPE (${tape.reporting_date}):\n${JSON.stringify(tape, null, 2)}`);
    } catch (e) {
      contextParts.push(`Note: Could not load loan tape from ${latest.url}: ${e}`);
    }

    // Also load second-most-recent for comparison questions
    if (/compar|vs|versus|chang|trend|over time|month/.test(q) && sorted.length > 1) {
      const prev = sorted[1];
      try {
        toolCallsMade.push(`load_esma_tape(${prev.url})`);
        const prevTape = await loadEsmaTape(prev.url);
        contextParts.push(`ESMA LOAN TAPE - PRIOR PERIOD (${prevTape.reporting_date}):\n${JSON.stringify(prevTape, null, 2)}`);
      } catch (e) {
        // non-fatal
      }
    }
  }

  if (needsProspectus && context.prospectus_url) {
    try {
      toolCallsMade.push(`parse_prospectus(${context.prospectus_url})`);
      const prospectus = await parseProspectus(context.prospectus_url);
      allCitations.push(...(prospectus.citations ?? []));
      contextParts.push(`PROSPECTUS:\n${JSON.stringify(prospectus, null, 2)}`);
    } catch (e) {
      contextParts.push(`Note: Could not parse prospectus: ${e}`);
    }
  }

  if (needsReports && context.investor_report_urls?.length) {
    const sorted = [...context.investor_report_urls].sort((a, b) => b.period.localeCompare(a.period));
    const latest = sorted[0];
    try {
      toolCallsMade.push(`analyze_investor_report(${latest.url})`);
      const report = await analyzeInvestorReport(latest.url);
      allCitations.push(...(report.citations ?? []));
      contextParts.push(`INVESTOR REPORT (${report.period}):\n${JSON.stringify(report, null, 2)}`);
    } catch (e) {
      contextParts.push(`Note: Could not parse investor report: ${e}`);
    }
  }

  if (contextParts.length === 0) {
    return {
      answer: "No relevant data sources could be loaded to answer this question. Please provide tape URLs, prospectus URL, or investor report URLs in the deal context.",
      confidence: "low",
      sources: [],
      tool_calls_made: toolCallsMade,
    };
  }

  const systemPrompt = `You are a structured finance analyst answering questions about a specific deal: ${context.deal_name}.
You have access to the following data sources about this deal. Answer the question precisely using only the provided data.
If you are uncertain, say so. Always ground your answer in specific numbers and cite your sources.`;

  const userContent = `Question: ${question}

Available deal data:
${contextParts.join("\n\n---\n\n")}

Respond with:
1. A clear, precise answer to the question
2. Your confidence level (high/medium/low) and why
3. Specific citations (document name, page/row, relevant excerpt)`;

  const answer = await chat({
    system: systemPrompt,
    messages: [{ role: "user", content: userContent }],
    maxTokens: 2048,
  });

  // Parse confidence from the answer text
  const confidence = answer.toLowerCase().includes("high confidence") ? "high"
    : answer.toLowerCase().includes("low confidence") ? "low"
    : "medium";

  return {
    answer,
    confidence,
    sources: allCitations,
    tool_calls_made: toolCallsMade,
  };
}
