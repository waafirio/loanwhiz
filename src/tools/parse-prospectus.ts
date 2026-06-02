import { extractPdfFromUrl, truncatePdfText } from "../lib/pdf.js";

export interface ProspectusText {
  document_type: "prospectus";
  file_url: string;
  page_count: number;
  char_count: number;
  text: string;
}

export async function parseProspectus(fileUrl: string): Promise<ProspectusText> {
  const pdf = await extractPdfFromUrl(fileUrl);
  return {
    document_type: "prospectus",
    file_url: fileUrl,
    page_count: pdf.pages,
    char_count: pdf.text.length,
    text: truncatePdfText(pdf.text, 80_000),
  };
}
