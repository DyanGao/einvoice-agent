"use client";

// The UI's language (v1.0 Stage 4): German or English, chosen in the
// masthead, remembered in this browser, at first taken from the browser's own
// language. Texts come from lib/messages.ts; amounts, dates and percentages
// are formatted the German or the British way.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

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
};

export function translator(locale: Locale): Translator {
  const catalogue = CATALOGUES[locale];
  const intl = INTL[locale];
  const dates = new Intl.DateTimeFormat(intl, { dateStyle: "medium", timeStyle: "short" });
  const percents = new Intl.NumberFormat(intl, { style: "percent", maximumFractionDigits: 0 });
  const t = (key: MessageKey, params?: Params) => fill(catalogue[key], params);
  const tx = (key: string, fallback: string, params?: Params) =>
    fill(key in catalogue ? catalogue[key as MessageKey] : fallback, params);
  return {
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
  };
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

/** The masthead's language switch. */
export function LanguageSwitch() {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className="language">
      <span className="visually-hidden">{t("language.label")}</span>
      <select
        aria-label={t("language.label")}
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
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
