"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import Link from "next/link";

import { getMe, SIGNED_OUT_EVENT, signOut, switchTenant, type Me } from "@/lib/api";
import { useI18n, type Translator } from "@/lib/i18n";

import { SignIn } from "./SignIn";

const STORAGE_KEY = "einvoice.reviewer";

/**
 * Who is reviewing. With sign-in on (AUTH=on) that is the signed-in user, and
 * the API records them itself. With sign-in off it is a name typed into the
 * masthead, sent along for the history and marked there as unverified.
 */
type Session =
  | { kind: "checking" }
  | { kind: "open" } // sign-in off, or the API cannot be reached: the pages say so
  | { kind: "signed-out" }
  | { kind: "signed-in"; me: Me };

type Reviewer = {
  session: Session;
  /** The name to send with a change: empty when signed in. */
  name: string;
  setName: (name: string) => void;
  /** Why this person cannot change anything now, or null if they can. */
  problem: string | null;
  signedIn: (me: Me) => void;
  signOut: () => void;
};

const ReviewerContext = createContext<Reviewer>({
  session: { kind: "checking" },
  name: "",
  setName: () => {},
  problem: null,
  signedIn: () => {},
  signOut: () => {},
});

/** Why this name cannot be recorded, or null if it can. */
function problemWith(i18n: Translator, name: string): string | null {
  if (name.trim() === "") return i18n.t("reviewer.needName");
  if (name.length > 100) return i18n.t("reviewer.tooLong");
  // The name travels in an HTTP header, which only carries Latin-1.
  if ([...name].some((char) => char.charCodeAt(0) > 255)) return i18n.t("reviewer.latinOnly");
  return null;
}

function problemFor(i18n: Translator, session: Session, name: string): string | null {
  if (session.kind === "signed-in") {
    return session.me.role === "viewer" ? i18n.t("reviewer.viewerOnly") : null;
  }
  return problemWith(i18n, name);
}

export function ReviewerProvider({ children }: { children: ReactNode }) {
  const i18n = useI18n();
  const [name, setNameState] = useState("");
  const [session, setSession] = useState<Session>({ kind: "checking" });

  useEffect(() => {
    try {
      setNameState(window.localStorage.getItem(STORAGE_KEY) ?? "");
    } catch {
      // Storage blocked: the name simply is not remembered between visits.
    }
  }, []);

  useEffect(() => {
    getMe()
      .then((me) => {
        if (me === null) setSession({ kind: "signed-out" });
        else if (me.auth === "off") setSession({ kind: "open" });
        else setSession({ kind: "signed-in", me });
      })
      // Not reachable: show the pages, which explain how to start the API.
      .catch(() => setSession({ kind: "open" }));
  }, []);

  // The session expired or was ended elsewhere: ask to sign in again.
  useEffect(() => {
    const expired = () =>
      setSession((current) => (current.kind === "signed-in" ? { kind: "signed-out" } : current));
    window.addEventListener(SIGNED_OUT_EVENT, expired);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, expired);
  }, []);

  const setName = (next: string) => {
    setNameState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // See above.
    }
  };

  const signedIn = useCallback((me: Me) => setSession({ kind: "signed-in", me }), []);
  const leave = useCallback(() => {
    signOut()
      .catch(() => {
        // Already gone on the server; signed out here either way.
      })
      .finally(() => setSession({ kind: "signed-out" }));
  }, []);

  return (
    <ReviewerContext.Provider
      value={{
        session,
        name: session.kind === "signed-in" ? "" : name,
        setName,
        problem: problemFor(i18n, session, name),
        signedIn,
        signOut: leave,
      }}
    >
      {children}
    </ReviewerContext.Provider>
  );
}

export const useReviewer = () => useContext(ReviewerContext);

/** The masthead's right side: the name field, or who is signed in. */
export function ReviewerField() {
  const { session, name, setName, signOut } = useReviewer();
  const { t } = useI18n();
  if (session.kind === "signed-in") {
    const { me } = session;
    return (
      <div className="account">
        <TenantChoice me={me} />
        <span>
          {t("account.signedInAs")} <strong>{me.name}</strong>
          <span className="account-role">{t(`role.${me.role}`)}</span>
        </span>
        {me.role === "admin" && (
          <Link href="/admin" className="account-link">
            {t("account.admin")}
          </Link>
        )}
        <button type="button" className="button-quiet" onClick={signOut}>
          {t("account.signOut")}
        </button>
      </div>
    );
  }
  if (session.kind !== "open") return null;
  return (
    <label className="reviewer">
      <span>{t("reviewer.label")}</span>
      <input
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder={t("reviewer.placeholder")}
        autoComplete="name"
        maxLength={100}
      />
    </label>
  );
}

/** The company this session works in; a choice for someone in several. */
function TenantChoice({ me }: { me: Me }) {
  const { t } = useI18n();
  const [problem, setProblem] = useState<string | null>(null);
  if (me.tenants.length < 2) {
    return <span className="account-tenant">{me.tenant.name}</span>;
  }
  const choose = (tenantId: string) => {
    setProblem(null);
    switchTenant(tenantId)
      // Every page loads again, for the other company.
      .then(() => window.location.assign("/"))
      .catch((error: unknown) =>
        setProblem(error instanceof Error ? error.message : t("account.switchFailed")),
      );
  };
  return (
    <label className="account-tenant">
      <span className="visually-hidden">{t("account.company")}</span>
      <select
        value={me.tenant.id}
        onChange={(event) => choose(event.target.value)}
        aria-label={t("account.company")}
        title={problem ?? undefined}
      >
        {me.tenants.map((tenant) => (
          <option key={tenant.id} value={tenant.id}>
            {tenant.name}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The pages, once it is known who may see them; the sign-in page otherwise. */
export function SignedIn({ children }: { children: ReactNode }) {
  const { session, signedIn } = useReviewer();
  if (session.kind === "checking") return null;
  if (session.kind === "signed-out") return <SignIn onSignedIn={signedIn} />;
  return <>{children}</>;
}
