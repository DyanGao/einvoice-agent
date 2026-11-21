import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { translator } from "../lib/i18n";

import { AMOUNT, BROWSER_LOCALE, englishOn, expectNoEnglish, L, LOCALE, OTHER } from "./language";

// Written by tests/ui_server.py when it starts: a generated (synthetic) invoice.
const PDF = path.join(__dirname, ".tmp", "rechnung.pdf");
const XML = path.join(__dirname, ".tmp", "rechnung.xml");
const WAITING = path.join(__dirname, ".tmp", "waiting-rechnung.pdf");
const AGAIN = path.join(__dirname, ".tmp", "again-rechnung.pdf");
const XML_COPY = path.join(__dirname, ".tmp", "rechnung-copy.xml");
const NUMBER = "RE-2026-17234";

// Every test in the browser's language; the texts come from the UI's catalogue (./language.ts).
test.use({ locale: BROWSER_LOCALE });
test.describe.configure({ mode: "serial" });

const { t, tn } = L;
const EMPTY_QUEUE = t("queue.emptyPending");
/** The fixed start of a text, before its first parameter. */
const startOf = (text: string) => (text.split("{")[0] ?? text).trim();

async function upload(page: Page, file: string) {
  await page.goto("/");
  await page.getByLabel(t("queue.files")).setInputFiles(file);
  await expect(page.getByRole("list").getByText(t("queue.uploaded"))).toBeVisible();
}

test("an empty queue invites an upload", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();
  await expect(page.getByLabel(t("language.label"))).toHaveValue(LOCALE);
});

test("an upload the API refuses says why, in the reader's language", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(t("queue.files")).setInputFiles({
    name: "leer.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.alloc(0),
  });
  await expect(
    page.getByText(t("queue.notUploaded", { reason: t("server.api.empty_file") })),
  ).toBeVisible();
});

test("upload, correct, approve: every step is in the history", async ({ page }) => {
  await upload(page, PDF);
  await page.getByRole("link", { name: NUMBER }).click();

  // What the machine read is in the form, and the uncertain field is marked.
  const number = page.getByLabel(t("field.number"));
  await expect(number).toHaveValue(NUMBER);

  // Nothing is saved or approved without a name for the history.
  const save = page.getByRole("button", { name: tn("doc.save", 1) });
  const approve = page.getByRole("button", { name: t("doc.approve"), exact: true });
  await number.fill("RE-OHNE-NAMEN");
  await expect(page.getByText(t("reviewer.needName"))).toBeVisible();
  await expect(save).toBeDisabled();
  await expect(approve).toBeDisabled();
  await number.fill(NUMBER);
  await page.getByPlaceholder(t("reviewer.placeholder")).fill("anna");
  await expect(page.getByText(t("hint.sure", { percent: L.percent(0.35) }))).toBeVisible();
  await expect(page.locator("[data-unsure]").getByLabel(t("field.due_date"))).toBeVisible();
  // Every reason it was not booked without a person is listed.
  const why = page.getByRole("region", { name: t("doc.whyReview.region") });
  await expect(why.getByText(t("cause.low_confidence"))).toBeVisible();
  // What the server wrote, in this language: a code with parameters, the
  // threshold as a share and the fields by their labels, not their paths.
  await expect(
    why.getByText(
      t("server.reason.low_confidence", {
        threshold_percent: L.percent(0.975),
        fields: t("field.due_date"),
      }),
    ),
  ).toBeVisible();
  await expect(why.getByText(t("cause.findings"))).toBeVisible();
  // The checks ran: the ERP does not know this supplier, and the page says so.
  await expect(page.getByText(t("server.finding.unknown_supplier"))).toBeVisible();
  await expect(page.getByText(t("finding.unknown_supplier")).first()).toBeVisible();
  // A value no one read from the page is labelled as such.
  await expect(page.getByText(t("hint.derived"))).toBeVisible();

  // A mandatory field that is emptied blocks approval and is named.
  const gross = page.getByLabel(t("field.gross_total"));
  const grossValue = await gross.inputValue();
  await gross.fill("");
  await save.click();
  await expect(
    page.getByText(t("doc.blocked.needed", { fields: t("field.gross_total") })),
  ).toBeVisible();
  await expect(approve).toBeDisabled();

  // German notation is understood; the same amount typed again is no change.
  await gross.fill(grossValue.replace(".", ","));
  await save.click();
  await expect(page.getByText(startOf(t("doc.blocked.needed")))).toHaveCount(0);

  await number.fill("RE-KORRIGIERT");
  const net = page.getByLabel(t("field.net_total"));
  await net.fill(await net.inputValue());
  await save.click();

  const history = page.getByRole("list").last();
  await expect(
    history.getByText(
      t("audit.field_corrected", { field: t("field.number"), old: NUMBER, new: "RE-KORRIGIERT" }),
    ),
  ).toBeVisible();
  await expect(page.getByText(t("hint.readAs", { value: NUMBER }))).toBeVisible();

  await approve.click();
  const stamp = page.getByTestId("stamp");
  await expect(stamp).toContainText(t("doc.stamp.approved"));
  await expect(stamp).toContainText("anna");
  await expect(number).toBeDisabled();
  await expect(approve).toHaveCount(0);
  await expect(history.getByRole("listitem").last()).toContainText(t("audit.approved"));
  await expect(history.getByRole("listitem").last()).toContainText("anna");

  // It has left the review queue and shows as approved under all documents.
  await page.getByRole("link", { name: t("doc.back"), exact: true }).click();
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await expect(page.getByRole("row", { name: /RE-KORRIGIERT/ })).toContainText(
    t("review.approved"),
  );
  // Amounts as this language writes them, from the decimal string.
  await expect(page.locator("td.number").first()).toHaveText(AMOUNT);
});

test("the same invoice sent again is rejected as a duplicate and never booked", async ({
  page,
}) => {
  await upload(page, AGAIN);
  await page.getByRole("button", { name: new RegExp(t("queue.toReview")) }).click();
  await page.getByRole("link", { name: NUMBER }).click();
  await page.getByPlaceholder(t("reviewer.placeholder")).fill("anna");

  await page.getByText(t("doc.reject.open")).click();
  const reject = page.getByRole("button", { name: t("doc.reject.submit") });
  const reason = page.getByRole("combobox", { name: new RegExp(`^${t("doc.reject.reason")}`) });
  await expect(reject).toBeDisabled();
  await reason.selectOption("other");
  await expect(page.getByText(t("doc.reject.needNote"))).toBeVisible();
  await reason.selectOption("duplicate");
  await page.getByLabel(new RegExp(t("doc.reject.note"))).fill("sent twice");
  await reject.click();

  await expect(page.getByTestId("stamp")).toContainText(t("doc.stamp.rejected"));
  await expect(page.getByText(startOf(t("doc.rejectedNotBooked")))).toBeVisible();
  await expect(
    page.getByText(t("audit.rejectedNote", { reason: t("reject.duplicate"), note: "sent twice" })),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: t("doc.approve"), exact: true })).toHaveCount(0);

  await page.getByRole("link", { name: t("doc.back"), exact: true }).click();
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await expect(page.locator(".state-rejected")).toHaveText(t("review.rejected"));
});

test("a valid e-invoice needs no review and has no page to show", async ({ page }) => {
  await upload(page, XML);
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await page.getByRole("row", { name: new RegExp(t("review.not_required")) }).getByRole("link").click();
  await expect(page.getByText(t("doc.notRequired"))).toBeVisible();
  await expect(page.getByRole("region", { name: t("doc.whyReview.region") })).toHaveCount(0);
  // It was matched in the ERP: an order quoted exactly, and the transfer that paid it.
  const matching = page.getByRole("region", { name: t("doc.match.region") });
  await expect(matching.getByText(/PO-\d{6}/).first()).toBeVisible();
  await expect(matching.getByText(t("basis.exact_reference")).first()).toBeVisible();
  await expect(matching.getByText(/TX-\d{6}/).first()).toBeVisible();
  // Asking again gives the same answer and is recorded.
  const checked = page.getByText(t("audit.checked"), { exact: true });
  const checksBefore = await checked.count();
  await matching.getByRole("button", { name: t("doc.match.again") }).click();
  await expect(checked).toHaveCount(checksBefore + 1);
  await expect(page.getByRole("link", { name: t("doc.download") })).toBeVisible();
  await expect(page.getByRole("button", { name: t("doc.approve"), exact: true })).toHaveCount(0);
});

test("an unknown document says so instead of showing an empty form", async ({ page }) => {
  await page.goto("/documents/does-not-exist");
  await expect(page.getByText(t("server.api.document_not_found"))).toBeVisible();
});

test("a document that is still being read updates by itself", async ({ page }) => {
  // tests/ui_server.py holds a file named waiting-* in "received" for a few
  // seconds, like a document the real worker has not got to yet. No request is
  // intercepted: the UI must notice the change on its own.
  await upload(page, WAITING);
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await expect(page.getByText(t("status.received"))).toBeVisible();
  await expect(page.getByText(t("status.received"))).toHaveCount(0, { timeout: 15_000 });
});

test("plain duplicates are rejected together after a confirmation", async ({ page }) => {
  await upload(page, XML_COPY);
  await page.getByRole("button", { name: new RegExp(t("queue.toReview")) }).click();
  await page.getByRole("button", { name: tn("copies.offer", 1) }).click();
  const panel = page.getByRole("region", { name: t("copies.region") });
  await expect(panel.getByRole("row")).toHaveCount(2); // header and the one copy
  await expect(panel.getByText(tn("copies.explain", 1))).toBeVisible();

  await page.getByPlaceholder(t("reviewer.placeholder")).fill("anna");
  await panel.getByRole("button", { name: t("copies.confirm.one") }).click();
  await expect(page.getByText(tn("copies.done", 1))).toBeVisible();
  await expect(page.getByRole("region", { name: t("copies.region") })).toHaveCount(0);
});

test("a mistaken rejection is reopened for review, with a note", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await page
    .locator("tr", { has: page.locator(".state-rejected") })
    .getByRole("link", { name: NUMBER })
    .click();
  await page.getByPlaceholder(t("reviewer.placeholder")).fill("ben");

  await page.getByText(t("doc.reopen.open")).click();
  const reopen = page.getByRole("button", { name: t("doc.reopen.submit") });
  await expect(reopen).toBeDisabled();
  await page.getByLabel(t("doc.reopen.why")).fill("not a copy after all");
  await reopen.click();

  await expect(
    page.getByText(t("audit.reopened", { note: "not a copy after all" })),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: t("doc.approve"), exact: true })).toBeVisible();
  await expect(page.getByTestId("stamp")).toHaveCount(0);
});

test("the mail page shows what arrived and why a file was refused", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("navigation", { name: t("nav.pages") })
    .getByRole("link", { name: t("nav.mail") })
    .click();
  await expect(page.getByRole("heading", { name: t("mail.title") })).toBeVisible();
  const rows = page.getByRole("row");
  await expect(rows.filter({ hasText: "Frage zur Rechnung" })).toContainText(
    t("mail.nothingAttached"),
  );
  await expect(rows.filter({ hasText: "Ihre Rechnung" })).toContainText(
    `gesperrt.pdf ${t("mail.refused", { reason: t("mailReason.encrypted_pdf") })}`,
  );
});

test("the language chosen in the masthead is kept after a reload", async ({ page }) => {
  const other = OTHER;
  const there = translator(other);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: t("queue.title") })).toBeVisible();
  await page.getByLabel(t("language.label")).selectOption(other);
  await expect(page.getByRole("heading", { name: there.t("queue.title") })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: there.t("queue.title") })).toBeVisible();
  await expect(page.getByRole("navigation", { name: there.t("nav.pages") })).toContainText(
    there.t("nav.mail"),
  );
  await page.getByLabel(there.t("language.label")).selectOption(LOCALE);
  await expect(page.getByRole("heading", { name: t("queue.title") })).toBeVisible();
});

test("the scan for English finds it on an English page", async ({ page }) => {
  // The control for the scan below: without it, a scan that finds nothing proves nothing.
  test.skip(LOCALE !== "en", "an English page to scan");
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await expect(page.getByRole("link", { name: NUMBER }).first()).toBeVisible();
  const found = await englishOn(page);
  expect(found).toContain(t("queue.title"));
  expect(found).toContain("the");
  // And amounts with a decimal point, in the fields of a review page.
  await page.getByRole("link", { name: NUMBER }).first().click();
  await expect(page.getByLabel(t("field.gross_total"))).toHaveValue(/^\d+\.\d{2}$/);
  expect((await englishOn(page)).some((item) => item.startsWith("number "))).toBe(true);
});

test("amounts in the fields are written the reader's way, also after a switch", async ({
  page,
}) => {
  const there = translator(OTHER);
  const notation = (locale: string) => (locale === "de" ? /^\d+,\d{2}$/ : /^\d+\.\d{2}$/);
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await page.getByRole("link", { name: NUMBER }).first().click();
  const gross = page.getByLabel(t("field.gross_total"));
  await expect(gross).toHaveValue(notation(LOCALE));
  const value = await gross.inputValue();

  // A thousands separator, in the other notation: the same amount, so no change.
  const [whole, fraction] = value.split(/[.,]/) as [string, string];
  const grouped = (separator: string, decimal: string) =>
    `${whole.slice(0, -3)}${separator}${whole.slice(-3)}${decimal}${fraction}`;
  await gross.fill(LOCALE === "de" ? grouped(",", ".") : grouped(".", ","));
  await expect(page.getByRole("button", { name: t("doc.saveNone") })).toBeVisible();

  await page.getByLabel(t("language.label")).selectOption(OTHER);
  const grossThere = page.getByLabel(there.t("field.gross_total"));
  await expect(grossThere).toHaveValue(notation(OTHER));
  await expect(page.getByRole("button", { name: there.t("doc.saveNone") })).toBeVisible();
  await page.getByLabel(there.t("language.label")).selectOption(LOCALE);
  await expect(gross).toHaveValue(value);
});

test.describe("no English on a German page", () => {
  test.skip(LOCALE !== "de", "the scan reads German pages");
  // What the pages show from the invoices and mails themselves: data, not the UI's text.
  const DATA = ["sent twice", "not a copy after all", "anna", "ben"];

  test("the queue, every document, the mail page", async ({ page }) => {
    await page.goto("/");
    // The page is drawn in English first and switches once the browser's language is known.
    await expect(page.getByRole("heading", { name: t("queue.title") })).toBeVisible();
    await expectNoEnglish(page, DATA);
    await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
    await expect(page.getByRole("link", { name: NUMBER }).first()).toBeVisible();
    await expectNoEnglish(page, DATA);
    await page.goto("/mail");
    await expect(page.getByRole("heading", { name: t("mail.title") })).toBeVisible();
    await expectNoEnglish(page, DATA);
    await page.goto("/documents/does-not-exist");
    await expect(page.getByText(t("server.api.document_not_found"))).toBeVisible();
    await expectNoEnglish(page, DATA);
  });

  test("every document's review page", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
    const links = page.locator("td a[href^='/documents/']");
    await expect(links.first()).toBeVisible();
    const pages = await links.evaluateAll((all) => all.map((a) => a.getAttribute("href")));
    expect(pages.length).toBeGreaterThan(3);
    for (const href of pages) {
      await page.goto(href!);
      await expect(page.getByRole("heading", { name: t("doc.history") })).toBeVisible();
      await expectNoEnglish(page, DATA);
    }
  });
});
