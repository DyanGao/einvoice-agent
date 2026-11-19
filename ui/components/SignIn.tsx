"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  getSignInOptions,
  PROVIDER_SIGN_IN_URL,
  signIn,
  type Me,
  type SignInOptions,
} from "@/lib/api";

/** Why the identity provider's sign-in did not let someone in (`?signin=` from the API). */
const OUTCOMES: Record<string, string> = {
  unknown_user: "Your account is not set up here yet. Ask an admin to add your email address.",
  unverified_email:
    "The sign-in service did not confirm your email address, so it cannot be matched to an account here.",
  disabled: "Your account here is disabled. Ask an admin.",
  other_account:
    "Your email address here is linked to another account at the sign-in service. Sign in with that one, or ask an admin.",
  failed: "Sign-in did not work. Please try again.",
  cancelled: "Sign-in was cancelled.",
};

function outcomeFromAddress(): string | null {
  const outcome = new URLSearchParams(window.location.search).get("signin");
  return outcome === null ? null : (OUTCOMES[outcome] ?? OUTCOMES.failed ?? null);
}

function forgetOutcome() {
  const address = new URL(window.location.href);
  if (!address.searchParams.has("signin")) return;
  address.searchParams.delete("signin");
  window.history.replaceState(null, "", address.pathname + address.search + address.hash);
}

export function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [options, setOptions] = useState<SignInOptions | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    setOutcome(outcomeFromAddress());
    getSignInOptions()
      .then(setOptions)
      .catch((error: unknown) =>
        setProblem(error instanceof Error ? error.message : "The API cannot be reached."),
      );
  }, []);

  const submit = (event: FormEvent) => {
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
            ? "Wrong email or password."
            : error instanceof Error
              ? error.message
              : "Sign-in did not work.",
        );
      })
      .finally(() => setBusy(false));
  };

  const provider = options?.auth === "on" && options.provider;
  const providerLabel =
    options?.auth === "on" && options.provider_name
      ? `Sign in with ${options.provider_name}`
      : "Sign in with your company account";

  return (
    <section className="sign-in" aria-labelledby="sign-in-title">
      <h1 id="sign-in-title">Sign in</h1>
      <p className="sign-in-lead">to check and approve incoming invoices.</p>
      {outcome && (
        <p className="problem" role="alert">
          {outcome}
        </p>
      )}
      {provider && (
        <>
          <a className="button button-primary sign-in-provider" href={PROVIDER_SIGN_IN_URL}>
            {providerLabel}
          </a>
          <p className="sign-in-or">or, for the built-in admin account</p>
        </>
      )}
      <form onSubmit={submit} className="sign-in-form">
        <label>
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {problem && (
          <p className="problem" role="alert">
            {problem}
          </p>
        )}
        <button
          type="submit"
          className={provider ? "button" : "button button-primary"}
          disabled={busy || email.trim() === "" || password === ""}
        >
          {busy ? "Signing in…" : "Sign in with password"}
        </button>
      </form>
    </section>
  );
}
