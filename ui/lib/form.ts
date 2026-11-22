// The editable invoice fields, how they are shown, and how edits become the
// {"dotted.path": value} corrections the API expects.

import type { Fields } from "./api";
import type { Translator } from "./i18n";
import type { MessageKey } from "./messages";

export type Kind = "text" | "date" | "amount";

export type FieldSpec = { path: string; label: MessageKey; kind: Kind };

export type Group = { title: MessageKey; fields: FieldSpec[] };

export const GROUPS: Group[] = [
  {
    title: "group.invoice",
    fields: [
      { path: "number", label: "field.number", kind: "text" },
      { path: "issue_date", label: "field.issue_date", kind: "date" },
      { path: "due_date", label: "field.due_date", kind: "date" },
      { path: "type_code", label: "field.type_code", kind: "text" },
      { path: "currency", label: "field.currency", kind: "text" },
      { path: "buyer_reference", label: "field.buyer_reference", kind: "text" },
      { path: "po_ref", label: "field.po_ref", kind: "text" },
      { path: "preceding_ref", label: "field.preceding_ref", kind: "text" },
    ],
  },
  {
    title: "group.seller",
    fields: [
      { path: "seller.name", label: "field.seller.name", kind: "text" },
      { path: "seller.vat_id", label: "field.seller.vat_id", kind: "text" },
    ],
  },
  {
    title: "group.buyer",
    fields: [
      { path: "buyer.name", label: "field.buyer.name", kind: "text" },
      { path: "buyer.vat_id", label: "field.buyer.vat_id", kind: "text" },
    ],
  },
  {
    title: "group.totals",
    fields: [
      { path: "line_net_total", label: "field.line_net_total", kind: "amount" },
      { path: "net_total", label: "field.net_total", kind: "amount" },
      { path: "vat_total", label: "field.vat_total", kind: "amount" },
      { path: "gross_total", label: "field.gross_total", kind: "amount" },
      { path: "amount_due", label: "field.amount_due", kind: "amount" },
    ],
  },
];

export type Column = { key: string; label: MessageKey; kind: Kind };

export const LINE_COLUMNS: Column[] = [
  { key: "line_id", label: "column.line_id", kind: "text" },
  { key: "description", label: "column.description", kind: "text" },
  { key: "quantity", label: "column.quantity", kind: "amount" },
  { key: "unit_price", label: "column.unit_price", kind: "amount" },
  { key: "net", label: "column.net", kind: "amount" },
  { key: "vat_rate", label: "column.vat_rate", kind: "amount" },
];

export const VAT_COLUMNS: Column[] = [
  { key: "category", label: "column.category", kind: "text" },
  { key: "rate", label: "column.rate", kind: "amount" },
  { key: "taxable_amount", label: "column.taxable_amount", kind: "amount" },
  { key: "tax_amount", label: "column.tax_amount", kind: "amount" },
];

type Table = {
  path: string;
  title: MessageKey;
  columns: Column[];
  rowName: MessageKey;
  add: MessageKey;
};

export const TABLES: readonly Table[] = [
  {
    path: "lines",
    title: "table.lines",
    columns: LINE_COLUMNS,
    rowName: "table.lines.row",
    add: "table.lines.add",
  },
  {
    path: "vat_breakdown",
    title: "table.vat_breakdown",
    columns: VAT_COLUMNS,
    rowName: "table.vat_breakdown.row",
    add: "table.vat_breakdown.add",
  },
];

export type Row = Record<string, string>;

export type FormState = {
  scalars: Record<string, string>;
  ibans: string;
  tables: Record<string, Row[]>;
};

// Below this the field is marked for the reviewer. Provisional: the real
// threshold comes from calibration on the eval set.
export const LOW_CONFIDENCE = 0.8;

export function valueAt(fields: Fields | null, path: string): unknown {
  let node: unknown = fields;
  for (const part of path.split(".")) {
    if (node === null || node === undefined) return null;
    node = Array.isArray(node) ? node[Number(part)] : (node as Record<string, unknown>)[part];
  }
  return node ?? null;
}

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function rowsOf(fields: Fields | null, path: string): Record<string, unknown>[] {
  const rows = valueAt(fields, path);
  return Array.isArray(rows) ? (rows as Record<string, unknown>[]) : [];
}

/** A value for its field in the reader's notation: "7864,76" and "29.01.2026" in German. */
export function shownAs(i18n: Translator, value: unknown, kind: Kind): string {
  if (kind === "amount") return i18n.decimal(text(value));
  if (kind === "date") return i18n.dateField(text(value));
  return text(value);
}

/** "29.01.2026", "29.1.2026" or "2026-01-29" → "2026-01-29"; anything else as typed, for the
 * server to refuse with its reason. */
function isoDate(value: string): string {
  const german = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(value);
  if (german) {
    const [, day = "", month = "", year = ""] = german;
    return `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
  }
  return value;
}

export function formFrom(fields: Fields | null, i18n: Translator): FormState {
  const scalars: Record<string, string> = {};
  for (const group of GROUPS) {
    for (const field of group.fields) {
      scalars[field.path] = shownAs(i18n, valueAt(fields, field.path), field.kind);
    }
  }
  const tables: Record<string, Row[]> = {};
  for (const table of TABLES) {
    tables[table.path] = rowsOf(fields, table.path).map((row) =>
      Object.fromEntries(
        table.columns.map((column) => [column.key, shownAs(i18n, row[column.key], column.kind)]),
      ),
    );
  }
  const ibans = valueAt(fields, "payment_ibans");
  return { scalars, ibans: Array.isArray(ibans) ? ibans.join("\n") : "", tables };
}

/** What the reviewer typed, as the value sent to the API. Empty means "no value". */
export function parseInput(raw: string, kind: Kind): string | null {
  const value = raw.trim();
  if (value === "") return null;
  if (kind === "date") return isoDate(value);
  if (kind !== "amount") return value;
  // Either notation, whatever the UI's language: the last of comma and dot is
  // the decimal sign and the other only groups thousands ("1.234,56", "1,234.56").
  // A lone comma is a decimal sign ("12,5").
  const comma = value.lastIndexOf(",");
  const dot = value.lastIndexOf(".");
  if (comma > dot) return value.replaceAll(".", "").replace(",", ".");
  if (comma !== -1) return value.replaceAll(",", "");
  return value;
}

/** The kind of the field at `path` ("net_total", "lines.0.quantity"); text if unknown. */
export function kindAt(path: string): Kind {
  for (const group of GROUPS) {
    const field = group.fields.find((candidate) => candidate.path === path);
    if (field) return field.kind;
  }
  const [head, , key] = path.split(".");
  const column = TABLES.find((table) => table.path === head)?.columns.find((c) => c.key === key);
  return column?.kind ?? "text";
}

/** The form with every amount and date written again in the reader's notation (after a language switch). */
export function inNotation(form: FormState, i18n: Translator): FormState {
  const again = (raw: string, kind: Kind) =>
    kind === "text" ? raw : shownAs(i18n, parseInput(raw, kind) ?? "", kind);
  const scalars = { ...form.scalars };
  for (const group of GROUPS) {
    for (const field of group.fields) {
      scalars[field.path] = again(scalars[field.path] ?? "", field.kind);
    }
  }
  const tables: Record<string, Row[]> = {};
  for (const table of TABLES) {
    tables[table.path] = (form.tables[table.path] ?? []).map((row) => {
      const next = { ...row };
      for (const column of table.columns) next[column.key] = again(row[column.key] ?? "", column.kind);
      return next;
    });
  }
  return { ...form, scalars, tables };
}

export function parseIbans(raw: string): string[] {
  return raw
    .split(/[\n,;]/)
    .map((iban) => iban.replaceAll(/\s/g, "").toUpperCase())
    .filter((iban) => iban !== "");
}

/** "012.50" → "12.5": the digits that matter, compared as text so nothing is rounded. */
function canonicalAmount(value: string): string | null {
  const match = /^(-?)0*(\d+)(?:\.(\d*?)0*)?$/.exec(value);
  if (!match) return null;
  const [, sign, whole, fraction] = match;
  return `${sign}${whole}${fraction ? `.${fraction}` : ""}`;
}

function sameAmount(a: string, b: string): boolean {
  const canonical = canonicalAmount(a);
  return canonical !== null && canonical === canonicalAmount(b);
}

function unchanged(next: string | null, before: unknown, kind: Kind): boolean {
  const previous = before === null || before === undefined ? null : String(before);
  if (next === previous) return true;
  // "12.5" for a stored "12.50" is not a correction.
  return kind === "amount" && next !== null && previous !== null && sameAmount(next, previous);
}

/** The corrections that turn `fields` into what the form now shows. */
export function changesFrom(fields: Fields | null, form: FormState): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const group of GROUPS) {
    for (const field of group.fields) {
      const next = parseInput(form.scalars[field.path] ?? "", field.kind);
      if (!unchanged(next, valueAt(fields, field.path), field.kind)) changes[field.path] = next;
    }
  }
  const ibans = parseIbans(form.ibans);
  const storedIbans = valueAt(fields, "payment_ibans");
  if (JSON.stringify(ibans) !== JSON.stringify(Array.isArray(storedIbans) ? storedIbans : [])) {
    changes["payment_ibans"] = ibans;
  }
  for (const table of TABLES) {
    const rows = form.tables[table.path] ?? [];
    const stored = rowsOf(fields, table.path);
    const parsed = rows.map((row) =>
      Object.fromEntries(
        table.columns.map((column) => [column.key, parseInput(row[column.key] ?? "", column.kind)]),
      ),
    );
    if (rows.length !== stored.length) {
      // Rows were added or removed: the API takes the whole list in that case.
      changes[table.path] = parsed;
      continue;
    }
    parsed.forEach((row, index) => {
      for (const column of table.columns) {
        const next = row[column.key] ?? null;
        if (!unchanged(next, stored[index]?.[column.key], column.kind)) {
          changes[`${table.path}.${index}.${column.key}`] = next;
        }
      }
    });
  }
  return changes;
}

const LABELS: Record<string, MessageKey> = Object.fromEntries([
  ...GROUPS.flatMap((group) => group.fields.map((field) => [field.path, field.label])),
  ["payment_ibans", "field.payment_ibans"],
  ["lines", "table.lines"],
  ["vat_breakdown", "table.vat_breakdown"],
]);

/** "lines.0.net" → "Line 1, Net" (or "Position 1, Netto"). Unknown paths are shown as they are. */
export function labelFor(i18n: Translator, path: string): string {
  const known = LABELS[path];
  if (known) return i18n.t(known);
  const [head, index, key] = path.split(".");
  const table = TABLES.find((candidate) => candidate.path === head);
  if (!table || index === undefined) return path;
  const column = table.columns.find((candidate) => candidate.key === key);
  const row = { row: i18n.t(table.rowName), number: Number(index) + 1 };
  return column
    ? i18n.t("field.cell", { ...row, column: i18n.t(column.label) })
    : i18n.t("field.row", row);
}
