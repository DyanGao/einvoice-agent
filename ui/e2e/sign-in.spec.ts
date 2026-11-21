import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { translator, type Translator } from "../lib/i18n";

import { BROWSER_LOCALE, expectNoEnglish, L, LOCALE, OTHER } from "./language";

// The test accounts of tests/ui_server.py --auth on (made up for these tests).
const ADMIN = { email: "admin@example.test", password: "ui-test-admin-password" };
const VIEWER = { email: "viewer@example.test", password: "ui-test-viewer-password" };
const PDF = path.join(__dirname, ".tmp-signin", "rechnung.pdf");
const NUMBER = "RE-2026-17234";

// Every test in the browser's language; the texts come from the UI's catalogue (./language.ts).
test.use({ locale: BROWSER_LOCALE });
test.describe.configure({ mode: "serial" });

const { t } = L;
const EMPTY_QUEUE = t("queue.emptyPending");
const PROVIDER = t("signin.withProvider", { provider: "Test Login" });

async function signInWithPassword(
  page: Page,
  account: { email: string; password: string },
  shown: Translator = L, // the sign-in page's language: the browser's until signed in
) {
  await page.goto("/");
  await page.getByLabel(shown.t("signin.email")).fill(account.email);
  await page.getByLabel(shown.t("signin.password")).fill(account.password);
  await page.getByRole("button", { name: shown.t("signin.submit") }).click();
}

test("nothing is shown before signing in, and there is no name to type", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: t("signin.title") })).toBeVisible();
  await expect(page.getByRole("link", { name: PROVIDER })).toBeVisible();
  await expect(page.getByPlaceholder(t("reviewer.placeholder"))).toHaveCount(0);
  await expect(page.getByText(EMPTY_QUEUE)).toHaveCount(0);
});

test("a wrong password is refused", async ({ page }) => {
  await signInWithPassword(page, { ...ADMIN, password: "not-the-password" });
  await expect(page.locator(".sign-in").getByRole("alert")).toHaveText(
    t("signin.wrongPassword"),
  );
  await expect(page.getByLabel(t("signin.password"))).toHaveValue("");
});

test("the admin signs in, and the history names them without a typed name", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await expect(page.locator(".account")).toContainText(`${t("account.signedInAs")} Ada Admin`);
  await expect(page.locator(".account")).toContainText(t("role.admin"));
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();

  await page.getByLabel(t("queue.files")).setInputFiles(PDF);
  await expect(page.getByRole("list").getByText(t("queue.uploaded"))).toBeVisible();
  await page.getByRole("link", { name: NUMBER }).click();
  await page.getByText(t("doc.reject.open")).click();
  await page
    .getByRole("combobox", { name: new RegExp(`^${t("doc.reject.reason")}`) })
    .selectOption("not_addressed_to_us");
  await page.getByRole("button", { name: t("doc.reject.submit") }).click();
  await expect(page.getByTestId("stamp")).toContainText(t("doc.stamp.rejected"));
  await expect(page.getByRole("list").last().getByRole("listitem").last()).toContainText(
    "Ada Admin",
  );
});

test("signing out ends the session, also after a reload", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await page.getByRole("button", { name: t("account.signOut") }).click();
  await expect(page.getByRole("heading", { name: t("signin.title") })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: t("signin.title") })).toBeVisible();
});

test("a viewer reads, and is told why nothing can be changed", async ({ page }) => {
  await signInWithPassword(page, VIEWER);
  await expect(page.locator(".account")).toContainText(t("role.viewer"));
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await page.getByRole("link", { name: NUMBER }).click();
  await expect(page.getByTestId("stamp")).toContainText(t("doc.stamp.rejected"));
  await page.getByText(t("doc.reopen.open")).click();
  await expect(page.getByText(t("reviewer.viewerOnly"))).toBeVisible();
});

test("a viewer has no admin page", async ({ page }) => {
  await signInWithPassword(page, VIEWER);
  await expect(page.locator(".account")).toContainText(t("role.viewer"));
  await expect(page.getByRole("link", { name: t("account.admin") })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page.getByText(t("admin.needsAdmin", { tenant: "Default" }))).toBeVisible();
});

test("signing in through the identity provider", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: PROVIDER }).click();
  await expect(page.locator(".account")).toContainText(`${t("account.signedInAs")} Anna Example`);
  await expect(page.locator(".account")).toContainText(t("role.reviewer"));
  await expect(page).toHaveURL("http://localhost:3011/");
});

test("a sign-in the provider allowed but this installation refused says why", async ({ page }) => {
  await page.goto("/?signin=unknown_user");
  await expect(page.locator(".sign-in").getByRole("alert")).toHaveText(
    t("signin.outcome.unknown_user"),
  );
});

test("when the session ends while working, the sign-in page comes back", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();
  // As if the session expired: the next request is answered 401.
  await page.context().clearCookies();
  await page.getByLabel(t("queue.files")).setInputFiles(PDF);
  await expect(page.getByRole("heading", { name: t("signin.title") })).toBeVisible();
});

test("the admin manages members, and the settings that are theirs", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await page.getByRole("link", { name: t("account.admin") }).click();
  await expect(
    page.getByRole("heading", { name: t("admin.title", { tenant: "Default" }) }),
  ).toBeVisible();
  const members = page.getByRole("table");
  await expect(members.getByText("viewer@example.test")).toBeVisible();
  // An admin cannot take their own role away, nor remove themselves.
  await expect(page.getByLabel(t("admin.roleOf", { name: "Ada Admin" }))).toBeDisabled();

  const add = page.getByRole("form", { name: t("admin.addForm") });
  await add.getByLabel(t("admin.col.email")).fill("nina@example.test");
  await add.getByLabel(t("admin.name"), { exact: true }).fill("Nina Neu");
  await add.getByRole("button", { name: t("admin.add") }).click();
  await expect(page.getByText(t("admin.added", { email: "nina@example.test" }))).toBeVisible();
  await expect(members.getByText("Nina Neu")).toBeVisible();

  await page.getByLabel(t("admin.roleOf", { name: "Nina Neu" })).selectOption("viewer");
  await expect(
    page.getByText(t("admin.roleChanged", { name: "Nina Neu", role: L.inSentence(t("role.viewer")) })),
  ).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await members
    .getByRole("row", { name: /Nina Neu/ })
    .getByRole("button", { name: t("admin.remove") })
    .click();
  await expect(page.getByText(t("admin.removed", { name: "Nina Neu" }))).toBeVisible();
  await expect(members.getByText("Nina Neu")).toHaveCount(0);

  const maxPages = page.getByLabel(t("setting.max_pages"));
  await maxPages.fill("5");
  await page.getByRole("button", { name: t("admin.save") }).click();
  await expect(page.getByText(t("admin.saved"))).toBeVisible();
  await page.reload();
  await expect(maxPages).toHaveValue("5");
  // What reaches the server's network and files is only shown.
  await expect(page.getByText(t("admin.operator"))).toBeVisible();
  await expect(page.locator(".operator-settings")).toContainText(t("setting.erp_url"));
});

test("someone in two companies switches between them", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  const company = page.getByLabel(t("account.company"));
  await expect(company).toHaveValue("default");
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await expect(page.getByRole("link", { name: NUMBER })).toBeVisible();

  await company.selectOption({ label: "Acme GmbH" });
  await expect(company).toHaveValue(/.+/);
  await expect(company.locator("option:checked")).toHaveText("Acme GmbH");
  // None of the other company's invoices.
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();
  await page.getByRole("button", { name: new RegExp(t("queue.all")) }).click();
  await expect(page.getByRole("link", { name: NUMBER })).toHaveCount(0);

  await company.selectOption({ label: "Default" });
  await expect(company.locator("option:checked")).toHaveText("Default");
});

test("the language one chooses is kept with the account, in another browser too", async ({
  page,
  browser,
}) => {
  const there = translator(OTHER);
  await signInWithPassword(page, VIEWER);
  await expect(page.locator(".account")).toContainText(t("role.viewer"));
  await page.getByLabel(t("language.label")).selectOption(OTHER);
  await expect(page.getByRole("heading", { name: there.t("queue.title") })).toBeVisible();

  // Another browser, set to this suite's language and with nothing remembered:
  // the sign-in page follows the browser, the account's choice wins once signed in.
  const elsewhere = await browser.newContext({ locale: BROWSER_LOCALE });
  const other = await elsewhere.newPage();
  await signInWithPassword(other, VIEWER);
  await expect(other.locator(".account")).toContainText(there.t("role.viewer"));
  await expect(other.getByRole("heading", { name: there.t("queue.title") })).toBeVisible();
  await expect(other.getByLabel(there.t("language.label"))).toHaveValue(OTHER);

  // Back to this suite's language, for the account too.
  await other.getByLabel(there.t("language.label")).selectOption(LOCALE);
  await expect(other.getByRole("heading", { name: t("queue.title") })).toBeVisible();
  await elsewhere.close();
  // The first browser still remembers the other language until signed in again.
  await page.context().clearCookies();
  await signInWithPassword(page, VIEWER, there);
  await expect(page.getByRole("heading", { name: t("queue.title") })).toBeVisible();
});

test("no English on the German sign-in and admin pages", async ({ page }) => {
  test.skip(LOCALE !== "de", "the scan reads German pages");
  // What the pages show of the accounts themselves: data, not the UI's text.
  const DATA = ["Test Login", "Ada Admin", "Vera Viewer", "Anna Example", "Acme GmbH"];
  await page.goto("/");
  await expect(page.getByRole("heading", { name: t("signin.title") })).toBeVisible();
  await expectNoEnglish(page, DATA);
  await page.goto("/?signin=unknown_user");
  await expect(page.locator(".sign-in").getByRole("alert")).toBeVisible();
  await expectNoEnglish(page, DATA);
  await signInWithPassword(page, ADMIN);
  await expect(page.getByText(EMPTY_QUEUE)).toBeVisible();
  await expectNoEnglish(page, DATA);
  await page.getByRole("link", { name: t("account.admin") }).click();
  await expect(page.getByLabel(t("setting.max_pages"))).toBeVisible();
  await expectNoEnglish(page, DATA);
});
