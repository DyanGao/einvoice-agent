// Every German text of the UI, for a native speaker to read through
// (v1.0 Stage 4, increment 5), and the glossary its terms follow.
//
//   node scripts/german-texts.mjs          write ../docs/german-texts.md
//   node scripts/german-texts.mjs --check  fail if a German text uses a term the
//                                          glossary rules out, or the list is stale
//
// Plain JavaScript run by Node with type stripping, so it reads lib/messages.ts
// itself rather than a copy of it (`pnpm texts`, `pnpm texts:check`).

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const { de, en } = await import("../lib/messages.ts");

const OUT = fileURLToPath(new URL("../../docs/german-texts.md", import.meta.url));

/** The accounts-payable terms the German texts use, and what they use instead of. */
export const GLOSSARY = [
  { term: "Beleg", english: "document", not: /\bDokument(e|s|en)?\b/, why: "the bookkeeping word for any incoming document" },
  { term: "Eingangsrechnung", english: "incoming invoice", not: /\bEinkaufsrechnung/, why: "what accounts payable calls the invoices it receives" },
  { term: "Lieferant", english: "supplier, seller", not: /Kreditor|Verkäufer|Rechnungssteller/, why: "plain for reviewers; Kreditor is the ledger's word" },
  { term: "Rechnungsempfänger", english: "buyer", not: /\bKäufer\b|\bErwerber/, why: "except Käuferreferenz, the XRechnung name of BT-10" },
  { term: "Freigabe, freigeben", english: "approval, approve", not: /[Gg]enehmig/, why: "Rechnungsfreigabe is the usual word" },
  { term: "buchen, Buchung", english: "book, booking", not: /[Vv]erbuch/, why: "short and usual in AP" },
  { term: "Bestellung", english: "purchase order", not: /\bAuftrag\b|\bBestellauftrag/, why: "the buyer's side; Auftrag is the seller's" },
  { term: "Wareneingang", english: "delivery (goods receipt in the ERP)", not: /\bLieferung(en)?\b/, why: "the record the three-way match compares with" },
  { term: "Umsatzsteuer, USt", english: "VAT", not: /Mehrwertsteuer|\bMwSt\b/, why: "the legal term (UStG)" },
  { term: "USt-IdNr.", english: "VAT ID", not: /\bUSt-ID\b|UStIdNr|Umsatzsteuer-ID\b/, why: "the official abbreviation" },
  { term: "Bankverbindung", english: "payment account", not: /\bBankkonto/, why: "what master data calls it" },
  { term: "Dublette", english: "duplicate", not: /Duplikat/, why: "the usual word for a document entered twice" },
  { term: "Gutschrift", english: "credit note", not: /\bStornorechnung/, why: "type 381; a cancellation is a different case" },
  { term: "Firma", english: "company (tenant)", not: /(?<!Microsoft-)\bMandant/, why: "Mandant only where Microsoft names it so" },
  { term: "Sie", english: "you", not: /\b(du|dich|dir|dein\w*)\b/i, why: "formal address throughout" },
];

/** Where in the UI each part of the catalogue is shown. */
const AREAS = [
  ["app.", "Masthead and pages"], ["nav.", "Masthead and pages"], ["language.", "Masthead and pages"],
  ["reviewer.", "Masthead and pages"], ["account.", "Masthead and pages"], ["api.", "Masthead and pages"],
  ["signin.", "Sign-in"],
  ["queue.", "Queue (incoming invoices)"], ["copies.", "Queue (incoming invoices)"],
  ["kind.", "Document states"], ["format.", "Document states"], ["status.", "Document states"], ["review.", "Document states"], ["result.", "Document states"],
  ["doc.", "Review page"], ["hint.", "Review page"], ["delivery.", "Review page"], ["booking.", "Review page"],
  ["connector.", "Review page"], ["connectorShort.", "Review page"],
  ["reject.", "Rejection reasons"],
  ["finding.", "Findings and causes"], ["cause.", "Findings and causes"], ["basis.", "Findings and causes"],
  ["audit.", "History"], ["value.", "History"],
  ["group.", "Invoice fields"], ["field.", "Invoice fields"], ["table.", "Invoice fields"], ["column.", "Invoice fields"],
  ["mail.", "Mail page"], ["mailReason.", "Mail page"],
  ["admin.", "Admin page"], ["setting.", "Admin page"], ["role.", "Admin page"],
  ["server.", "Written by the server"],
];

function areaOf(key) {
  for (const [prefix, area] of AREAS) {
    if (key.startsWith(prefix)) {
      if (prefix !== "server.") return area;
      const part = key.split(".")[1];
      return `${area}: ${part}`;
    }
  }
  return "Other";
}

function glossaryProblems() {
  const problems = [];
  for (const [key, text] of Object.entries(de)) {
    for (const rule of GLOSSARY) {
      const found = text.match(rule.not);
      if (found) problems.push(`${key}: "${found[0]}" where the glossary says "${rule.term}" — ${text}`);
    }
  }
  return problems;
}

const cell = (text) => text.replaceAll("|", "\\|").replaceAll("\n", " ");

function reviewList() {
  // In the order of AREAS: the pages a reviewer goes through, then the server's texts.
  const order = (key) => AREAS.findIndex(([prefix]) => key.startsWith(prefix));
  const keys = Object.keys(de).sort((a, b) => order(a) - order(b));
  const areas = new Map();
  for (const key of keys) {
    const area = areaOf(key);
    if (!areas.has(area)) areas.set(area, []);
    areas.get(area).push(key);
  }
  const lines = [
    "# German texts of the review UI",
    "",
    "Every German text the review UI shows, next to its English, for a native speaker to read through",
    "(v1.0 Stage 4, increment 5). Generated from `ui/lib/messages.ts` by `make german-texts`; CI fails when",
    "this list is out of date or a German text uses a term the glossary rules out. To change a text, change",
    "it in `ui/lib/messages.ts` (texts under `server.` also need `app/texts.py` only if the English changes)",
    "and run `make german-texts`.",
    "",
    "`{name}` is filled in when the text is shown: an amount, a date, a field's label, another text.",
    "",
    `${Object.keys(de).length} texts.`,
    "",
    "## Glossary",
    "",
    "The accounts-payable terms the German texts use. `make german-texts-check` (run in CI) rejects the",
    "words in the third column.",
    "",
    "| Term | English | Not | Why |",
    "| --- | --- | --- | --- |",
    ...GLOSSARY.map(
      (rule) =>
        `| ${rule.term} | ${rule.english} | \`${cell(rule.not.source)}\` | ${cell(rule.why)} |`,
    ),
    "",
    "## Contents",
    "",
    ...[...areas.keys()].map((area) => `- ${area} (${areas.get(area).length})`),
    "",
  ];
  for (const [area, keys] of areas) {
    lines.push(`## ${area}`, "", "| Key | English | Deutsch |", "| --- | --- | --- |");
    for (const key of keys) lines.push(`| \`${key}\` | ${cell(en[key])} | ${cell(de[key])} |`);
    lines.push("");
  }
  return lines.join("\n");
}

const problems = glossaryProblems();
const list = reviewList();
if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(OUT, "utf8");
  } catch {
    // Missing: stale as well.
  }
  if (current !== list) problems.push("docs/german-texts.md is out of date: run make german-texts");
  if (problems.length > 0) {
    console.error(problems.join("\n"));
    process.exit(1);
  }
  console.log(`${Object.keys(de).length} German texts follow the glossary; docs/german-texts.md is current`);
} else {
  if (problems.length > 0) console.error(problems.join("\n"));
  writeFileSync(OUT, list);
  console.log(`wrote docs/german-texts.md (${Object.keys(de).length} texts)`);
  if (problems.length > 0) process.exit(1);
}
