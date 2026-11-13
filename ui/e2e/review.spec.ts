import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// Written by tests/ui_server.py when it starts: a generated (synthetic) invoice.
const PDF = path.join(__dirname, ".tmp", "rechnung.pdf");
const XML = path.join(__dirname, ".tmp", "rechnung.xml");
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

test("a valid e-invoice needs no review and has no page to show", async ({ page }) => {
  await upload(page, XML);
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
  await page.getByRole("button", { name: /All documents/ }).click();
  await page.getByRole("row", { name: /No review needed/ }).getByRole("link").click();
  await expect(page.getByText("needs no review")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download the original file" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
});

test("an unknown document says so instead of showing an empty form", async ({ page }) => {
  await page.goto("/documents/does-not-exist");
  await expect(page.getByText("document not found")).toBeVisible();
});

test("a document that is still being read updates by itself", async ({ page }) => {
  // The test API reads documents at once, so the first answer is rewritten to
  // look like the real worker has not got to the document yet.
  let first = true;
  await page.route("**/documents?limit=200", async (route) => {
    if (!first) return route.continue();
    first = false;
    const response = await route.fetch();
    const body = await response.json();
    body.documents[0].status = "received";
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: /All documents/ }).click();
  await expect(page.getByText("Waiting to be read")).toBeVisible();
  await expect(page.getByText("Waiting to be read")).toHaveCount(0);
});
