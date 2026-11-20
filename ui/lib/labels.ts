import type { Booking, RejectReason, ReviewState } from "./api";

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
  rejected: "Rejected",
};

export const REJECT_REASONS: Record<RejectReason, string> = {
  duplicate: "Duplicate of an invoice already on file",
  not_addressed_to_us: "Not addressed to us",
  replaced_or_cancelled: "Replaced or cancelled by the supplier",
  other: "Other reason (say which)",
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
  printed_not_read: "On the document, not read",
  low_confidence: "Read with low confidence",
  findings: "Checks need a person",
  not_compared: "Not compared with the ERP",
  order_by_content: "Order not quoted",
  not_delivered: "Nothing delivered",
  corrected: "Changed by a reviewer",
  reader_not_calibrated: "Read by a model not trusted to book alone",
};

export const causeLabel = (cause: string) => CAUSE[cause] ?? cause.replaceAll("_", " ");

const CONNECTOR: Record<string, string> = {
  erp: "the ERP",
  csv_export: "the booking list",
  webhook: "the webhook receiver",
};

export const connectorLabel = (connector: string) =>
  CONNECTOR[connector] ?? connector.replaceAll("_", " ");

const SHORT_CONNECTOR: Record<string, string> = {
  erp: "ERP",
  csv_export: "Booking list",
  webhook: "Webhook",
};

/** For the queue: "ERP no. 5", "Waiting to be booked", "Booking failed", or "". */
export function bookingLabel(bookings: Booking[]): string {
  return bookings
    .map((booking) => {
      const where = SHORT_CONNECTOR[booking.connector] ?? connectorLabel(booking.connector);
      if (booking.status === "delivered") {
        return booking.external_ref ? `${where} no. ${booking.external_ref}` : `${where}: booked`;
      }
      return booking.status === "failed" ? `${where}: booking failed` : `${where}: waiting`;
    })
    .join(", ");
}

const BASIS: Record<string, string> = {
  exact_reference: "quoted exactly",
  similar_reference: "quoted with a slip",
  content: "found by content",
  ambiguous: "several fit, none chosen",
  none: "not found",
};

export const basisLabel = (basis: string) => BASIS[basis] ?? basis;

/** Why a file in a mail was not taken as an invoice (app/mailbox.py). */
const MAIL_REASON: Record<string, string> = {
  too_large: "too large",
  empty: "empty",
  unsupported_type: "not an invoice format (XML, PDF, PNG, JPEG or a ZIP of them)",
  encrypted_pdf: "PDF protected by a password",
  encrypted_zip: "ZIP protected by a password",
  nested_zip: "ZIP inside a ZIP (only one level is opened)",
  zip_bomb: "unpacks to far more than any invoice",
  unreadable: "could not be read",
};

export function mailReasonLabel(reason: string | null): string {
  if (!reason) return "";
  // The code comes first; "zip_too_many_files (61, at most 50)" carries details.
  const space = reason.indexOf(" ");
  const code = space === -1 ? reason : reason.slice(0, space);
  const details = space === -1 ? "" : reason.slice(space + 1);
  if (code === "zip_too_many_files") return `ZIP with too many files ${details}`.trim();
  return MAIL_REASON[code] ?? code.replaceAll("_", " ");
}
