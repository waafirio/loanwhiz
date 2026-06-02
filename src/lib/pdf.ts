import pdfParse from "pdf-parse";

export interface ExtractedPdf {
  text: string;
  pages: number;
  info: Record<string, unknown>;
}

export async function extractPdfFromUrl(url: string): Promise<ExtractedPdf> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch PDF: ${response.status} ${response.statusText}`);
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  const result = await pdfParse(buffer);
  return {
    text: result.text,
    pages: result.numpages,
    info: result.info as Record<string, unknown>,
  };
}

export async function extractPdfFromBuffer(buffer: Buffer): Promise<ExtractedPdf> {
  const result = await pdfParse(buffer);
  return {
    text: result.text,
    pages: result.numpages,
    info: result.info as Record<string, unknown>,
  };
}

// Truncate to stay within LLM context limits, preserving structure
export function truncatePdfText(text: string, maxChars = 80_000): string {
  if (text.length <= maxChars) return text;
  const half = Math.floor(maxChars / 2);
  return (
    text.slice(0, half) +
    "\n\n[... middle section truncated ...]\n\n" +
    text.slice(text.length - half)
  );
}
