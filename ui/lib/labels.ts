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

const FINDING: Record<string, string> = {
  totals_mismatch: "Totals do not add up",
  vat_rate_implausible: "Unusual VAT rate",
  vat_id_invalid: "Invalid VAT ID",
  credit_note: "Credit note",
  unusual_document_type: "Not an ordinary invoice",
  duplicate: "Duplicate",
  near_duplicate: "Possible duplicate",
  delivery_already_billed: "Delivery already billed",
  unknown_supplier: "Unknown supplier",
  iban_changed: "Bank account not on file",
  missing_po: "No purchase order",
  po_not_found: "Purchase order not found",
  price_deviation: "Price above order",
  quantity_deviation: "Quantity above order",
  master_data_not_checked: "Not checked against the ERP",
  po_not_quoted: "Order found, not quoted correctly",
};

export const findingLabel = (type: string) => FINDING[type] ?? type.replaceAll("_", " ");

const CAUSE: Record<string, string> = {
  not_read: "Not validated or read",
  missing_fields: "Fields missing",
  assumed_value: "Value assumed",
  low_confidence: "Read with low confidence",
  findings: "Checks need a person",
  not_compared: "Not compared with the ERP",
  order_by_content: "Order not quoted",
  not_delivered: "Nothing delivered",
  corrected: "Changed by a reviewer",
};

export const causeLabel = (cause: string) => CAUSE[cause] ?? cause.replaceAll("_", " ");

const BASIS: Record<string, string> = {
  exact_reference: "quoted exactly",
  similar_reference: "quoted with a slip",
  content: "found by content",
  ambiguous: "several fit, none chosen",
  none: "not found",
};

export const basisLabel = (basis: string) => BASIS[basis] ?? basis;
