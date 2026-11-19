import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// The test accounts of tests/ui_server.py --auth on (made up for these tests).
const ADMIN = { email: "admin@example.test", password: "ui-test-admin-password" };
const VIEWER = { email: "viewer@example.test", password: "ui-test-viewer-password" };
const PDF = path.join(__dirname, ".tmp-signin", "rechnung.pdf");
const NUMBER = "RE-2026-17234";

test.describe.configure({ mode: "serial" });

async function signInWithPassword(page: Page, account: { email: string; password: string }) {
  await page.goto("/");
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  await page.getByRole("button", { name: "Sign in with password" }).click();
}

test("nothing is shown before signing in, and there is no name to type", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in with Test Login" })).toBeVisible();
  await expect(page.getByPlaceholder("Your name")).toHaveCount(0);
  await expect(page.getByText("Nothing is waiting for review.")).toHaveCount(0);
});

test("a wrong password is refused", async ({ page }) => {
  await signInWithPassword(page, { ...ADMIN, password: "not-the-password" });
  await expect(page.locator(".sign-in").getByRole("alert")).toHaveText("Wrong email or password.");
  await expect(page.getByLabel("Password")).toHaveValue("");
});

test("the admin signs in, and the history names them without a typed name", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await expect(page.locator(".account")).toContainText("Signed in as Ada Admin");
  await expect(page.locator(".account")).toContainText("Admin");
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();

  await page.getByLabel("Invoice files").setInputFiles(PDF);
  await expect(page.getByRole("list").getByText("uploaded")).toBeVisible();
  await page.getByRole("link", { name: NUMBER }).click();
  await page.getByText("Reject instead…").click();
  await page.getByLabel("Reason").selectOption("not_addressed_to_us");
  await page.getByRole("button", { name: "Reject document" }).click();
  await expect(page.getByTestId("stamp")).toContainText("Rejected");
  await expect(page.getByRole("list").last().getByRole("listitem").last()).toContainText(
    "Ada Admin",
  );
});

test("signing out ends the session, also after a reload", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a viewer reads, and is told why nothing can be changed", async ({ page }) => {
  await signInWithPassword(page, VIEWER);
  await expect(page.locator(".account")).toContainText("Viewer");
  await page.getByRole("button", { name: /All documents/ }).click();
  await page.getByRole("link", { name: NUMBER }).click();
  await expect(page.getByTestId("stamp")).toContainText("Rejected");
  await page.getByText("Reopen…").click();
  await expect(
    page.getByText("You can read here, but your role (viewer) cannot change anything."),
  ).toBeVisible();
});

test("signing in through the identity provider", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in with Test Login" }).click();
  await expect(page.locator(".account")).toContainText("Signed in as Anna Example");
  await expect(page.locator(".account")).toContainText("Reviewer");
  await expect(page).toHaveURL("http://localhost:3011/");
});

test("a sign-in the provider allowed but this installation refused says why", async ({ page }) => {
  await page.goto("/?signin=unknown_user");
  await expect(page.locator(".sign-in").getByRole("alert")).toHaveText(
    "Your account is not set up here yet. Ask an admin to add your email address.",
  );
});

test("when the session ends while working, the sign-in page comes back", async ({ page }) => {
  await signInWithPassword(page, ADMIN);
  await expect(page.getByText("Nothing is waiting for review.")).toBeVisible();
  // As if the session expired: the next request is answered 401.
  await page.context().clearCookies();
  await page.getByLabel("Invoice files").setInputFiles(PDF);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
