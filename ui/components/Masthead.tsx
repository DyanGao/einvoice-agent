"use client";

import Link from "next/link";

import { LanguageSwitch, useI18n } from "@/lib/i18n";

import { ReviewerField } from "./Reviewer";

/** Title, pages, language, and who is reviewing. */
export function Masthead() {
  const { t } = useI18n();
  return (
    <header className="masthead">
      <Link href="/" className="masthead-title">
        {t("app.title")}
      </Link>
      <nav className="masthead-nav" aria-label={t("nav.pages")}>
        <Link href="/">{t("nav.invoices")}</Link>
        <Link href="/mail">{t("nav.mail")}</Link>
      </nav>
      <LanguageSwitch />
      <ReviewerField />
    </header>
  );
}
