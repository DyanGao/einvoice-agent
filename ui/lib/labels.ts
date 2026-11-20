// Names for the codes the API sends (statuses, kinds, findings, …), in the
// UI's language. Unknown codes are shown as they are, readable.

import type { Booking, RejectReason, ReviewState } from "./api";
import type { Translator } from "./i18n";

const readable = (code: string) => code.replaceAll("_", " ");

export const REJECT_REASONS: RejectReason[] = [
  "duplicate",
  "not_addressed_to_us",
  "replaced_or_cancelled",
  "other",
];

export const rejectReasonLabel = (i18n: Translator, reason: string) =>
  i18n.tx(`reject.${reason}`, readable(reason));
export const statusLabel = (i18n: Translator, status: string) =>
  i18n.tx(`status.${status}`, status);
export const formatLabel = (i18n: Translator, format: string) =>
  i18n.tx(`format.${format}`, format);
export const reviewLabel = (i18n: Translator, state: ReviewState) =>
  i18n.tx(`review.${state}`, state);
export const findingLabel = (i18n: Translator, type: string) =>
  i18n.tx(`finding.${type}`, readable(type));
export const causeLabel = (i18n: Translator, cause: string) =>
  i18n.tx(`cause.${cause}`, readable(cause));
export const basisLabel = (i18n: Translator, basis: string) => i18n.tx(`basis.${basis}`, basis);
export const connectorLabel = (i18n: Translator, connector: string) =>
  i18n.tx(`connector.${connector}`, readable(connector));

/** For the queue: "ERP no. 5", "ERP: waiting", "ERP: booking failed", or "". */
export function bookingLabel(i18n: Translator, bookings: Booking[]): string {
  return bookings
    .map((booking) => {
      const where = i18n.tx(
        `connectorShort.${booking.connector}`,
        connectorLabel(i18n, booking.connector),
      );
      if (booking.status === "delivered") {
        return booking.external_ref
          ? i18n.t("booking.number", { where, ref: booking.external_ref })
          : i18n.t("booking.booked", { where });
      }
      return booking.status === "failed"
        ? i18n.t("booking.failed", { where })
        : i18n.t("booking.waiting", { where });
    })
    .join(", ");
}

/** Why a file in a mail was not taken as an invoice (app/mailbox.py). */
export function mailReasonLabel(i18n: Translator, reason: string | null): string {
  if (!reason) return "";
  // The code comes first; "zip_too_many_files (61, at most 50)" carries details.
  const space = reason.indexOf(" ");
  const code = space === -1 ? reason : reason.slice(0, space);
  const details = space === -1 ? "" : reason.slice(space + 1);
  return i18n.tx(`mailReason.${code}`, readable(code), { details }).trim();
}
