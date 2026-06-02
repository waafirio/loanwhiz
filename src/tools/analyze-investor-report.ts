import { extractPdfFromUrl, truncatePdfText } from "../lib/pdf.js";

export interface InvestorReportText {
  document_type: "investor_report";
  file_url: string;
  page_count: number;
  char_count: number;
  text: string;
}

export async function analyzeInvestorReport(fileUrl: string): Promise<InvestorReportText> {
  const pdf = await extractPdfFromUrl(fileUrl);
  return {
    document_type: "investor_report",
    file_url: fileUrl,
    page_count: pdf.pages,
    char_count: pdf.text.length,
    text: truncatePdfText(pdf.text, 60_000),
  };
}
