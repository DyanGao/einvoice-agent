import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// Written by tests/ui_server.py when it starts: a generated (synthetic) invoice.
const PDF = path.join(__dirname, ".tmp", "rechnung.pdf");
const XML = path.join(__dirname, ".tmp", "rechnung.xml");
const WAITING = path.join(__dirname, ".tmp", "waiting-rechnung.pdf");
const AGAIN = path.join(__dirname, ".tmp", "again-rechnung.pdf");
const XML_COPY = path.join(__dirname, ".tmp", "rechnung-copy.xml");
const NUMBER = "RE-2026-17234";

test.describe.configure({ mode: "serial" });

async function upload(page: Page, file: string) {
  await page.goto("/");
  await page.getByLabel("Invoice files").setInputFiles(file);
  await expect(page.getByRole("list").getByText("uploaded")).toBeVisible();
}

test("an empty queue invites an upload", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
});

test("upload, correct, approve: every step is in the history", async ({ page }) => {
  await upload(page, PDF);
  await page.getByRole("link", { name: NUMBER }).click();

  // What the machine read is in the form, and the uncertain field is marked.
  const number = page.getByLabel("Invoice number");
  await expect(number).toHaveValue(NUMBER);

  // Nothing is saved or approved without a name for the history.
  await number.fill("RE-OHNE-NAMEN");
  await expect(page.getByText("Enter your name to save corrections or approve.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save 1 change" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
  await number.fill(NUMBER);
  await page.getByPlaceholder("Your name").fill("anna");
  await expect(page.getByText("35% sure")).toBeVisible();
  await expect(page.locator("[data-unsure]").getByLabel("Due date")).toBeVisible();
  // Every reason it was not booked without a person is listed.
  const why = page.getByRole("region", { name: "Why review" });
  await expect(why.getByText("Read with low confidence")).toBeVisible();
  // The fields are named by their labels, not their paths.
  await expect(why.getByText(/Due date/)).toBeVisible();
  await expect(why.getByText("Checks need a person")).toBeVisible();
  // The checks ran: the ERP does not know this supplier, and the page says so.
  await expect(page.getByText("no supplier with this VAT ID or name is on file")).toBeVisible();
  await expect(page.getByText("Unknown supplier").first()).toBeVisible();
  // A value no one read from the page is labelled as such.
  await expect(page.getByText("Filled in by a rule, not read from the document")).toBeVisible();

  // A mandatory field that is emptied blocks approval and is named.
  const gross = page.getByLabel("Gross total");
  const grossValue = await gross.inputValue();
  await gross.fill("");
  await page.getByRole("button", { name: "Save 1 change" }).click();
  await expect(page.getByText("Still needed: Gross total.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();

  // German notation is understood; the same amount typed again is no change.
  await gross.fill(grossValue.replace(".", ","));
  await page.getByRole("button", { name: "Save 1 change" }).click();
  await expect(page.getByText("Still needed")).toHaveCount(0);

  await number.fill("RE-KORRIGIERT");
  await page.getByLabel("Net total").fill(await page.getByLabel("Net total").inputValue());
  await page.getByRole("button", { name: "Save 1 change" }).click();

  const history = page.getByRole("list").last();
  await expect(
    history.getByText(`Invoice number changed from ${NUMBER} to RE-KORRIGIERT`),
  ).toBeVisible();
  await expect(page.getByText(`Read as ${NUMBER}`)).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();
  const stamp = page.getByTestId("stamp");
  await expect(stamp).toContainText("Approved");
  await expect(stamp).toContainText("anna");
  await expect(number).toBeDisabled();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(history.getByRole("listitem").last()).toContainText("Approved");
  await expect(history.getByRole("listitem").last()).toContainText("anna");

  // It has left the review queue and shows as approved under all documents.
  await page.getByRole("link", { name: "Incoming invoices" }).click();
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
  await page.getByRole("button", { name: /All documents/ }).click();
  await expect(page.getByRole("row", { name: /RE-KORRIGIERT/ })).toContainText("Approved");
});

test("the same invoice sent again is rejected as a duplicate and never booked", async ({
  page,
}) => {
  await upload(page, AGAIN);
  await page.getByRole("button", { name: /To review/ }).click();
  await page.getByRole("link", { name: NUMBER }).click();
  await page.getByPlaceholder("Your name").fill("anna");

  await page.getByText("Reject instead…").click();
  const reject = page.getByRole("button", { name: "Reject document" });
  await expect(reject).toBeDisabled();
  await page.getByLabel("Reason").selectOption("other");
  await expect(page.getByText("Say which reason.")).toBeVisible();
  await page.getByLabel("Reason").selectOption("duplicate");
  await page.getByLabel(/Note/).fill("sent twice");
  await reject.click();

  await expect(page.getByTestId("stamp")).toContainText("Rejected");
  await expect(page.getByText("Rejected, so it is not booked")).toBeVisible();
  await expect(
    page.getByText("Rejected: Duplicate of an invoice already on file (sent twice)"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  await page.getByRole("link", { name: "Incoming invoices" }).click();
  await page.getByRole("button", { name: /All documents/ }).click();
  await expect(page.locator(".state-rejected")).toHaveText("Rejected");
});

test("a valid e-invoice needs no review and has no page to show", async ({ page }) => {
  await upload(page, XML);
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
  await page.getByRole("button", { name: /All documents/ }).click();
  await page.getByRole("row", { name: /No review needed/ }).getByRole("link").click();
  await expect(page.getByText("can be booked without review")).toBeVisible();
  await expect(page.getByRole("region", { name: "Why review" })).toHaveCount(0);
  // It was matched in the ERP: an order quoted exactly, and the transfer that paid it.
  const matching = page.getByRole("region", { name: "Matching" });
  await expect(matching.getByText(/PO-\d{6}/).first()).toBeVisible();
  await expect(matching.getByText("quoted exactly").first()).toBeVisible();
  await expect(matching.getByText(/TX-\d{6}/).first()).toBeVisible();
  // Asking again gives the same answer and is recorded.
  const checksBefore = await page.getByText("Checked", { exact: true }).count();
  await matching.getByRole("button", { name: "Match and check again" }).click();
  await expect(page.getByText("Checked", { exact: true })).toHaveCount(checksBefore + 1);
  await expect(page.getByRole("link", { name: "Download the original file" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
});

test("an unknown document says so instead of showing an empty form", async ({ page }) => {
  await page.goto("/documents/does-not-exist");
  await expect(page.getByText("document not found")).toBeVisible();
});

test("a document that is still being read updates by itself", async ({ page }) => {
  // tests/ui_server.py holds a file named waiting-* in "received" for a few
  // seconds, like a document the real worker has not got to yet. No request is
  // intercepted: the UI must notice the change on its own.
  await upload(page, WAITING);
  await page.getByRole("button", { name: /All documents/ }).click();
  await expect(page.getByText("Waiting to be read")).toBeVisible();
  await expect(page.getByText("Waiting to be read")).toHaveCount(0, { timeout: 15_000 });
});

test("plain duplicates are rejected together after a confirmation", async ({ page }) => {
  await upload(page, XML_COPY);
  await page.getByRole("button", { name: /To review/ }).click();
  await page.getByRole("button", { name: "Reject 1 plain duplicate…" }).click();
  const panel = page.getByRole("region", { name: "Duplicates" });
  await expect(panel.getByRole("row")).toHaveCount(2); // header and the one copy
  await expect(panel.getByText("The earlier one stays.")).toBeVisible();

  await page.getByPlaceholder("Your name").fill("anna");
  await panel.getByRole("button", { name: "Reject this copy" }).click();
  await expect(page.getByText("1 copy rejected.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Duplicates" })).toHaveCount(0);
});

test("a mistaken rejection is reopened for review, with a note", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /All documents/ }).click();
  await page
    .locator("tr", { has: page.locator(".state-rejected") })
    .getByRole("link", { name: NUMBER })
    .click();
  await page.getByPlaceholder("Your name").fill("ben");

  await page.getByText("Reopen…").click();
  const reopen = page.getByRole("button", { name: "Reopen for review" });
  await expect(reopen).toBeDisabled();
  await page.getByLabel("Why it is reopened").fill("not a copy after all");
  await reopen.click();

  await expect(page.getByText("Reopened for review: not a copy after all")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await expect(page.getByTestId("stamp")).toHaveCount(0);
});

test("the mail page shows what arrived and why a file was refused", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Pages" }).getByRole("link", { name: "Mail" }).click();
  await expect(page.getByRole("heading", { name: "Mail" })).toBeVisible();
  const rows = page.getByRole("row");
  await expect(rows.filter({ hasText: "Frage zur Rechnung" })).toContainText("nothing attached");
  await expect(rows.filter({ hasText: "Ihre Rechnung" })).toContainText(
    "gesperrt.pdf refused: PDF protected by a password",
  );
});

test.describe("in German", () => {
  test.use({ locale: "de-DE" });

  test("a German browser gets the German UI, with German amounts and dates", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Eingangsrechnungen" })).toBeVisible();
    await expect(page.getByLabel("Sprache")).toHaveValue("de");
    // A refusal of the API, in German too: an empty file is not taken.
    await page.getByLabel("Rechnungsdateien").setInputFiles({
      name: "leer.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.alloc(0),
    });
    await expect(page.getByText("wurde nicht hochgeladen: leere Datei")).toBeVisible();
    await page.getByRole("button", { name: /Alle Belege/ }).click();
    // An amount the German way: thousands with a dot, decimals with a comma, then the euro sign.
    await expect(page.locator("td.number").first()).toHaveText(/^\d{1,3}(\.\d{3})*,\d{2}\s€$/);
    await page.getByRole("link", { name: NUMBER }).first().click();
    await expect(page.getByLabel("Rechnungsnummer")).toBeVisible();
    // What the server wrote, in German too (a code with parameters, not its English).
    await expect(
      page.getByText("kein Lieferant mit dieser USt-IdNr. oder diesem Namen hinterlegt"),
    ).toBeVisible();
    await expect(page.getByText(/mit weniger als 97,5\s%\sSicherheit ausgelesen/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Verlauf" })).toBeVisible();
    await expect(page.locator(".audit-what").first()).toHaveText("Eingegangen");
  });
});

test("the language chosen in the masthead is kept after a reload", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Incoming invoices" })).toBeVisible();
  await page.getByLabel("Language").selectOption("de");
  await expect(page.getByRole("heading", { name: "Eingangsrechnungen" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Eingangsrechnungen" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Seiten" })).toContainText("Post");
  await page.getByLabel("Sprache").selectOption("en");
  await expect(page.getByRole("heading", { name: "Incoming invoices" })).toBeVisible();
});
