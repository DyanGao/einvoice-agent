"use client";

// The UI's language (v1.0 Stage 4): German or English, chosen in the
// masthead, remembered in this browser, at first taken from the browser's own
// language. Someone signed in has it kept with their account too
// (components/Reviewer.tsx), and that choice wins in any browser. Texts come from lib/messages.ts; amounts, dates and percentages
// are formatted the German or the British way.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { API_URL, ApiError, type Said } from "./api";
import { labelFor } from "./form";
import { de, en, type MessageKey } from "./messages";

export type Locale = "de" | "en";
export type Params = Record<string, string | number>;

export const LOCALES: { value: Locale; label: string }[] = [
  { value: "de", label: "Deutsch" },
  { value: "en", label: "English" },
];

const CATALOGUES: Record<Locale, Record<MessageKey, string>> = { de, en };
const INTL: Record<Locale, string> = { de: "de-DE", en: "en-GB" };
const STORAGE_KEY = "einvoice.language";
const DECIMAL = /^-?\d+(\.\d+)?$/;

function fill(text: string, params?: Params): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  );
}

export type Translator = {
  locale: Locale;
  /** A text of the catalogue, with its parameters filled in. */
  t: (key: MessageKey, params?: Params) => string;
  /** A text whose key is made at run time (a code from the server); `fallback` if unknown. */
  tx: (key: string, fallback: string, params?: Params) => string;
  /** The `….one` or `….other` text for `count`. */
  tn: (key: string, count: number, params?: Params) => string;
  /** An amount from its decimal string, never through a float: "1234.5" → "1.234,50 €". */
  money: (amount: string | null, currency: string | null) => string;
  /** A timestamp from the API (UTC) in local time. */
  when: (timestamp: string) => string;
  /** 0.35 → "35 %" or "35%". */
  percent: (fraction: number) => string;
  /** A label inside a sentence: lower-case in English; German nouns keep their capital. */
  inSentence: (label: string) => string;
  /** A text the server wrote, in this language; its English sentence where there is no code. */
  server: (said: Said | null | undefined, english: string) => string;
  /** What went wrong, for the reader: the API's refusal in this language where it has a code. */
  error: (problem: unknown) => string;
};

export function translator(locale: Locale): Translator {
  const catalogue = CATALOGUES[locale];
  const intl = INTL[locale];
  const dates = new Intl.DateTimeFormat(intl, { dateStyle: "medium", timeStyle: "short" });
  const percents = new Intl.NumberFormat(intl, { style: "percent", maximumFractionDigits: 1 });
  const t = (key: MessageKey, params?: Params) => fill(catalogue[key], params);
  const tx = (key: string, fallback: string, params?: Params) =>
    // An unknown key shows the fallback as it is: it is already a sentence.
    key in catalogue ? fill(catalogue[key as MessageKey], params) : fallback;
  const translatorFor: Translator = {
    locale,
    t,
    tx,
    tn: (key, count, params) =>
      tx(`${key}.${count === 1 ? "one" : "other"}`, key, { count, ...params }),
    money: (amount, currency) => {
      if (amount === null) return "";
      if (!DECIMAL.test(amount)) return currency ? `${amount} ${currency}` : amount;
      const digits = amount.split(".")[1]?.length ?? 0;
      const value = amount as `${number}`;
      if (!currency) {
        return new Intl.NumberFormat(intl, { minimumFractionDigits: digits }).format(value);
      }
      try {
        return new Intl.NumberFormat(intl, {
          style: "currency",
          currency,
          // Never fewer digits than the invoice has: nothing is rounded away.
          maximumFractionDigits: Math.max(digits, 2),
        }).format(value);
      } catch {
        return `${amount} ${currency}`; // not a currency code Intl knows
      }
    },
    when: (timestamp) => {
      const zoned = /[zZ]|[+-]\d\d:\d\d$/.test(timestamp) ? timestamp : `${timestamp}Z`;
      const date = new Date(zoned);
      return Number.isNaN(date.getTime()) ? timestamp : dates.format(date);
    },
    percent: (fraction) => percents.format(fraction),
    inSentence: (label) => (locale === "en" ? label.toLowerCase() : label),
    // Rendered with this same translator, for the parameters too.
    server: (said, english) => (said ? serverText(translatorFor, said) : english),
    error: (problem) => {
      if (problem instanceof ApiError) {
        if (problem.status === 0) return t("api.unreachable", { url: API_URL });
        return problem.said ? serverText(translatorFor, problem.said) : problem.message;
      }
      return problem instanceof Error ? problem.message : String(problem);
    },
  };
  return translatorFor;
}

function isSaid(value: unknown): value is Said {
  return typeof value === "object" && value !== null && "code" in value && "text" in value;
}

/** A parameter of a server text, formatted for the reader (see app/texts.py). */
function serverParam(i18n: Translator, name: string, value: unknown): string {
  if (isSaid(value)) return serverText(i18n, value);
  if (Array.isArray(value)) {
    if (value.every(isSaid)) return value.map((item) => serverText(i18n, item)).join("; ");
    const items = value.map(String);
    return (name === "fields" ? items.map((path) => labelFor(i18n, path)) : items).join(", ");
  }
  if (name.endsWith("_amount") && value !== null) return i18n.money(String(value), null);
  if (name.endsWith("_percent") && value !== null) return i18n.percent(Number(value));
  return value === null || value === undefined ? "" : String(value);
}

function serverText(i18n: Translator, said: Said): string {
  const params = Object.fromEntries(
    Object.entries(said.params).map(([name, value]) => [name, serverParam(i18n, name, value)]),
  );
  return i18n.tx(`server.${said.code}`, said.text, params);
}

function browserLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "de" || stored === "en") return stored;
  } catch {
    // Storage blocked: the browser's language decides each time.
  }
  return navigator.language.toLowerCase().startsWith("de") ? "de" : "en";
}

type LocaleState = Translator & { setLocale: (locale: Locale) => void };

const LocaleContext = createContext<LocaleState>({ ...translator("en"), setLocale: () => {} });

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  useEffect(() => {
    setLocaleState(browserLocale());
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // See browserLocale.
    }
  }, []);

  const value = useMemo(() => ({ ...translator(locale), setLocale }), [locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export const useI18n = () => useContext(LocaleContext);

/** The masthead's language switch. `choose` replaces a plain change, to keep the choice elsewhere too. */
export function LanguageSwitch({ choose }: { choose?: (locale: Locale) => void }) {
  const { locale, setLocale, t } = useI18n();
  const change = choose ?? setLocale;
  return (
    <label className="language">
      <span className="visually-hidden">{t("language.label")}</span>
      <select
        aria-label={t("language.label")}
        value={locale}
        onChange={(event) => change(event.target.value as Locale)}
      >
        {LOCALES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
