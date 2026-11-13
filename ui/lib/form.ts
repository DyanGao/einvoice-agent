// The editable invoice fields, how they are shown, and how edits become the
// {"dotted.path": value} corrections the API expects.

import type { Fields } from "./api";

export type Kind = "text" | "date" | "amount";

export type FieldSpec = { path: string; label: string; kind: Kind };

export type Group = { title: string; fields: FieldSpec[] };

export const GROUPS: Group[] = [
  {
    title: "Invoice",
    fields: [
      { path: "number", label: "Invoice number", kind: "text" },
      { path: "issue_date", label: "Invoice date", kind: "date" },
      { path: "due_date", label: "Due date", kind: "date" },
      { path: "type_code", label: "Document type code", kind: "text" },
      { path: "currency", label: "Currency", kind: "text" },
      { path: "buyer_reference", label: "Buyer reference (Leitweg-ID)", kind: "text" },
      { path: "po_ref", label: "Purchase order", kind: "text" },
    ],
  },
  {
    title: "Seller",
    fields: [
      { path: "seller.name", label: "Seller name", kind: "text" },
      { path: "seller.vat_id", label: "Seller VAT ID", kind: "text" },
    ],
  },
  {
    title: "Buyer",
    fields: [
      { path: "buyer.name", label: "Buyer name", kind: "text" },
      { path: "buyer.vat_id", label: "Buyer VAT ID", kind: "text" },
    ],
  },
  {
    title: "Totals",
    fields: [
      { path: "line_net_total", label: "Sum of line amounts", kind: "amount" },
      { path: "net_total", label: "Net total", kind: "amount" },
      { path: "vat_total", label: "VAT total", kind: "amount" },
      { path: "gross_total", label: "Gross total", kind: "amount" },
      { path: "amount_due", label: "Amount due", kind: "amount" },
    ],
  },
];

export type Column = { key: string; label: string; kind: Kind };

export const LINE_COLUMNS: Column[] = [
  { key: "line_id", label: "No.", kind: "text" },
  { key: "description", label: "Description", kind: "text" },
  { key: "quantity", label: "Quantity", kind: "amount" },
  { key: "unit_price", label: "Unit price", kind: "amount" },
  { key: "net", label: "Net", kind: "amount" },
  { key: "vat_rate", label: "VAT %", kind: "amount" },
];

export const VAT_COLUMNS: Column[] = [
  { key: "category", label: "Category", kind: "text" },
  { key: "rate", label: "Rate %", kind: "amount" },
  { key: "taxable_amount", label: "Taxable amount", kind: "amount" },
  { key: "tax_amount", label: "Tax", kind: "amount" },
];

export const TABLES = [
  { path: "lines", title: "Line items", columns: LINE_COLUMNS, rowName: "Line", add: "Add line" },
  {
    path: "vat_breakdown",
    title: "VAT breakdown",
    columns: VAT_COLUMNS,
    rowName: "VAT row",
    add: "Add VAT row",
  },
] as const;

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

export function formFrom(fields: Fields | null): FormState {
  const scalars: Record<string, string> = {};
  for (const group of GROUPS) {
    for (const field of group.fields) scalars[field.path] = text(valueAt(fields, field.path));
  }
  const tables: Record<string, Row[]> = {};
  for (const table of TABLES) {
    tables[table.path] = rowsOf(fields, table.path).map((row) =>
      Object.fromEntries(table.columns.map((column) => [column.key, text(row[column.key])])),
    );
  }
  const ibans = valueAt(fields, "payment_ibans");
  return { scalars, ibans: Array.isArray(ibans) ? ibans.join("\n") : "", tables };
}

/** What the reviewer typed, as the value sent to the API. Empty means "no value". */
export function parseInput(raw: string, kind: Kind): string | null {
  const value = raw.trim();
  if (value === "") return null;
  if (kind !== "amount") return value;
  // German notation: a comma is the decimal sign and dots only group thousands.
  return value.includes(",") ? value.replaceAll(".", "").replace(",", ".") : value;
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

const LABELS: Record<string, string> = Object.fromEntries([
  ...GROUPS.flatMap((group) => group.fields.map((field) => [field.path, field.label])),
  ["payment_ibans", "Payment IBANs"],
  ["lines", "Line items"],
  ["vat_breakdown", "VAT breakdown"],
]);

/** "lines.0.net" → "Line 1, Net". Unknown paths are shown as they are. */
export function labelFor(path: string): string {
  const known = LABELS[path];
  if (known) return known;
  const [head, index, key] = path.split(".");
  const table = TABLES.find((candidate) => candidate.path === head);
  if (!table || index === undefined) return path;
  const column = table.columns.find((candidate) => candidate.key === key);
  const row = `${table.rowName} ${Number(index) + 1}`;
  return column ? `${row}, ${column.label}` : row;
}
