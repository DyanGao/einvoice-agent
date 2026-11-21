import { expect, type Page } from "@playwright/test";

import { translator, type Locale } from "../lib/i18n";
import { de, en } from "../lib/messages";

// The browser suites run once per language (package.json "test:e2e"):
// E2E_LANGUAGE=de gives a German browser, and every text a test looks for is
// taken from the same catalogue the UI uses, in that language.
export const LOCALE: Locale = process.env.E2E_LANGUAGE === "de" ? "de" : "en";
export const BROWSER_LOCALE = LOCALE === "de" ? "de-DE" : "en-GB";
export const OTHER: Locale = LOCALE === "de" ? "en" : "de";
export const L = translator(LOCALE);

/** An amount as this language writes it: "1.234,56 €" or "€1,234.56". */
export const AMOUNT =
  LOCALE === "de" ? /^-?\d{1,3}(\.\d{3})*,\d{2}\s€$/ : /^-?€\d{1,3}(,\d{3})*\.\d{2}$/;

// English that may stand on a German page: names and abbreviations both use.
const SHARED = new Set(["ERP", "PDF", "ZIP", "XML", "IBAN", "BIC", "Name", "Status", "Webhook"]);
// A word that is English and not German (not "was", "an", "will", "not": German has
// them too), to catch a sentence no catalogue has.
const ENGLISH_WORDS =
  /\b(the|and|is|were|with|from|this|that|which|has|have|been|for|of|to|by|or|yes|please|cannot|could|should|would|your|you|it)\b/gi;

function fragments(template: string): string[] {
  // The fixed parts of a text, between its parameters.
  return template
    .split(/\{\w+\}/)
    .map((part) => part.trim().replace(/^[\s.,:;·()…]+|[\s.,:;·()…]+$/g, ""))
    .filter((part) => part.length >= 4 && !SHARED.has(part));
}

/** Every English text of the UI that the German one does not share. */
const ENGLISH_ONLY: string[] = (() => {
  const german = Object.values(de).join("\n");
  const found = new Set<string>();
  for (const [key, english] of Object.entries(en)) {
    if (english === de[key as keyof typeof de]) continue;
    for (const part of fragments(english)) {
      // One word that German uses too ("Admin", "Mail") is no proof of English.
      if (!part.includes(" ") || german.includes(part)) continue;
      found.add(part);
    }
  }
  return [...found];
})();

// A number with a decimal point, as English writes it ("7864.76", "0.5500"). Not
// three digits after the point: "2.048" is how German groups thousands. Not part
// of a date ("20.11.2026") or a German amount ("1.234,56").
const POINT_DECIMAL = /(?<![\d.,])\d+\.(?:\d{1,2}|\d{4,})(?![\d.,])/g;

/** What on this page reads as English: texts of the English catalogue, English
 * words, and numbers with a decimal point (in the text and in the fields). */
export async function englishOn(page: Page, allowed: (string | RegExp)[] = []): Promise<string[]> {
  let text = await page.locator("body").innerText();
  for (const data of allowed) text = text.replaceAll(data, " ");
  const phrases = ENGLISH_ONLY.filter((part) => text.includes(part));
  const words = [...new Set(text.match(ENGLISH_WORDS) ?? [])];
  const values = await page
    .locator("input, textarea")
    .evaluateAll((fields) => fields.map((field) => (field as HTMLInputElement).value));
  const numbers = [...new Set([text, ...values].flatMap((part) => part.match(POINT_DECIMAL) ?? []))];
  return [...phrases, ...words, ...numbers.map((number) => `number ${number}`)];
}

/** No English sentence on this German page (data the page shows may be `allowed`). */
export async function expectNoEnglish(page: Page, allowed: (string | RegExp)[] = []) {
  expect(await englishOn(page, allowed), `English on ${page.url()}`).toEqual([]);
}
