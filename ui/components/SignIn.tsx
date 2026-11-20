"use client";

import { useEffect, useState, type SyntheticEvent } from "react";

import {
  ApiError,
  getSignInOptions,
  PROVIDER_SIGN_IN_URL,
  signIn,
  type Me,
  type SignInOptions,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Why the identity provider's sign-in did not let someone in (`?signin=` from the API). */
function outcomeFromAddress(): string | null {
  return new URLSearchParams(window.location.search).get("signin");
}

function forgetOutcome() {
  const address = new URL(window.location.href);
  if (!address.searchParams.has("signin")) return;
  address.searchParams.delete("signin");
  window.history.replaceState(null, "", address.pathname + address.search + address.hash);
}

export function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const i18n = useI18n();
  const { t } = i18n;
  const [options, setOptions] = useState<SignInOptions | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    setOutcome(outcomeFromAddress());
    getSignInOptions()
      .then(setOptions)
      .catch((error: unknown) => {
        if (error instanceof Error) setProblem(i18n.error(error));
        else setUnreachable(true);
      });
  }, []);

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    signIn(email.trim(), password)
      .then((me) => {
        forgetOutcome();
        onSignedIn(me);
      })
      .catch((error: unknown) => {
        setPassword("");
        setProblem(
          error instanceof ApiError && error.status === 401
            ? t("signin.wrongPassword")
            : error instanceof Error
              ? i18n.error(error)
              : t("signin.failedGeneric"),
        );
      })
      .finally(() => setBusy(false));
  };

  const provider = options?.auth === "on" && options.provider;
  const providerLabel =
    options?.auth === "on" && options.provider_name
      ? t("signin.withProvider", { provider: options.provider_name })
      : t("signin.withCompanyAccount");
  // An outcome code the UI does not know is a sign-in that failed.
  const outcomeText =
    outcome === null ? null : i18n.tx(`signin.outcome.${outcome}`, t("signin.outcome.failed"));

  return (
    <section className="sign-in" aria-labelledby="sign-in-title">
      <h1 id="sign-in-title">{t("signin.title")}</h1>
      <p className="sign-in-lead">{t("signin.lead")}</p>
      {outcomeText && (
        <p className="problem" role="alert">
          {outcomeText}
        </p>
      )}
      {provider && (
        <>
          <a className="button button-primary sign-in-provider" href={PROVIDER_SIGN_IN_URL}>
            {providerLabel}
          </a>
          <p className="sign-in-or">{t("signin.orPassword")}</p>
        </>
      )}
      <form onSubmit={submit} className="sign-in-form">
        <label>
          <span>{t("signin.email")}</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          <span>{t("signin.password")}</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {(problem || unreachable) && (
          <p className="problem" role="alert">
            {problem ?? t("signin.apiUnreachable")}
          </p>
        )}
        <button
          type="submit"
          className={provider ? "button" : "button button-primary"}
          disabled={busy || email.trim() === "" || password === ""}
        >
          {busy ? t("signin.busy") : t("signin.submit")}
        </button>
      </form>
    </section>
  );
}
