import type { ReviewState } from "./api";

const STATUS: Record<string, string> = {
  received: "Waiting to be read",
  valid: "Valid e-invoice",
  invalid: "E-invoice with rule violations",
  unrecognized: "E-invoice of an unknown kind",
  parse_failed: "E-invoice that could not be read",
  validation_failed: "Validator did not answer",
  extracted: "Read from the page",
  extraction_failed: "Nothing could be read",
  unsupported: "Not an invoice format",
};

const FORMAT: Record<string, string> = {
  ubl: "XRechnung (UBL)",
  cii: "XRechnung (CII)",
  zugferd_pdf: "ZUGFeRD PDF",
  pdf_text: "PDF",
  image: "Scan",
  unknown: "Unknown",
};

const REVIEW: Record<ReviewState, string> = {
  pending: "To review",
  approved: "Approved",
  not_required: "No review needed",
};

export const statusLabel = (status: string) => STATUS[status] ?? status;
export const formatLabel = (format: string) => FORMAT[format] ?? format;
export const reviewLabel = (state: ReviewState) => REVIEW[state];

const WHEN = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

export function when(timestamp: string): string {
  // The API stores UTC; some databases return it without a zone.
  const zoned = /[zZ]|[+-]\d\d:\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`;
  const date = new Date(zoned);
  return Number.isNaN(date.getTime()) ? timestamp : WHEN.format(date);
}

export function money(amount: string | null, currency: string | null): string {
  if (amount === null) return "";
  return currency ? `${amount} ${currency}` : amount;
}
