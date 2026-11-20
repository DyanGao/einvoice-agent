"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type SyntheticEvent } from "react";

import { useReviewer } from "@/components/Reviewer";
import {
  addMember,
  changeMemberRole,
  changeTenantSettings,
  getTenantSettings,
  listMembers,
  removeMember,
  type Member,
  type Role,
  type TenantSettings,
} from "@/lib/api";
import { useI18n, type Translator } from "@/lib/i18n";

const ROLES: Role[] = ["viewer", "reviewer", "admin"];

function message(i18n: Translator, error: unknown): string {
  return error instanceof Error ? i18n.error(error) : i18n.t("admin.failed");
}

function shown(i18n: Translator, value: unknown): string {
  if (value === null || value === undefined || value === "") return "–";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "–";
  if (typeof value === "boolean") return value ? i18n.t("admin.yes") : i18n.t("admin.no");
  return String(value);
}

const settingLabel = (i18n: Translator, key: string) => i18n.tx(`setting.${key}`, key);

export default function AdminPage() {
  const { session } = useReviewer();
  const { t } = useI18n();
  if (session.kind === "open") {
    return (
      <div className="queue">
        <h1>{t("admin.heading")}</h1>
        <p className="empty">{t("admin.off")}</p>
      </div>
    );
  }
  if (session.kind !== "signed-in") return null;
  if (session.me.role !== "admin") {
    return (
      <div className="queue">
        <h1>{t("admin.heading")}</h1>
        <p className="problem" role="alert">
          {t("admin.needsAdmin", { tenant: session.me.tenant.name })}
        </p>
        <Link href="/">{t("admin.backToInvoices")}</Link>
      </div>
    );
  }
  return <Admin myEmail={session.me.email} tenantName={session.me.tenant.name} />;
}

function Admin({ myEmail, tenantName }: { myEmail: string | null; tenantName: string }) {
  const i18n = useI18n();
  const { t } = i18n;
  const [members, setMembers] = useState<Member[] | null>(null);
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listMembers(), getTenantSettings()])
      .then(([list, current]) => {
        setMembers(list.members);
        setSettings(current);
      })
      .catch((error: unknown) => setProblem(message(i18n, error)));
  }, [i18n]);

  useEffect(load, [load]);

  const run = (action: Promise<unknown>, done: string) => {
    setProblem(null);
    setNote(null);
    action
      .then(() => {
        setNote(done);
        load();
      })
      .catch((error: unknown) => setProblem(message(i18n, error)));
  };

  return (
    <div className="queue admin">
      <h1>{t("admin.title", { tenant: tenantName })}</h1>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
      {note && (
        <p className="note" aria-live="polite">
          {note}
        </p>
      )}

      <section aria-labelledby="members-title">
        <h2 id="members-title">{t("admin.members")}</h2>
        {members && (
          <div className="table-scroll">
            <table className="documents">
              <thead>
                <tr>
                  <th>{t("admin.col.name")}</th>
                  <th>{t("admin.col.email")}</th>
                  <th>{t("admin.col.signsIn")}</th>
                  <th>{t("admin.col.role")}</th>
                  <th>
                    <span className="visually-hidden">{t("admin.col.remove")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {members.map((member) => {
                  const me = member.email === myEmail;
                  return (
                    <tr key={member.user_id}>
                      <td>{member.name}</td>
                      <td>{member.email}</td>
                      <td>{member.password ? t("admin.withPassword") : t("admin.throughProvider")}</td>
                      <td>
                        <select
                          aria-label={t("admin.roleOf", { name: member.name })}
                          value={member.role}
                          disabled={me}
                          title={me ? t("admin.ownRole") : undefined}
                          onChange={(event) => {
                            const role = event.target.value as Role;
                            run(
                              changeMemberRole(member.user_id, role),
                              t("admin.roleChanged", {
                                name: member.name,
                                role: i18n.inSentence(t(`role.${role}`)),
                              }),
                            );
                          }}
                        >
                          {ROLES.map((role) => (
                            <option key={role} value={role}>
                              {t(`role.${role}`)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {!me && (
                          <button
                            type="button"
                            className="button-quiet"
                            onClick={() => {
                              const question = t("admin.removeConfirm", {
                                name: member.name,
                                tenant: tenantName,
                              });
                              if (window.confirm(question)) {
                                run(
                                  removeMember(member.user_id),
                                  t("admin.removed", { name: member.name }),
                                );
                              }
                            }}
                          >
                            {t("admin.remove")}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <AddMember
          onAdd={(email, name, role) => run(addMember(email, name, role), t("admin.added", { email }))}
        />
        <ul className="role-hints">
          {ROLES.map((role) => (
            <li key={role}>
              <strong>{t(`role.${role}`)}</strong> {t(`admin.hint.${role}`)}
            </li>
          ))}
        </ul>
      </section>

      {settings && (
        <Settings
          current={settings}
          onSave={(changes) => run(changeTenantSettings(changes), t("admin.saved"))}
        />
      )}
    </div>
  );
}

function AddMember({ onAdd }: { onAdd: (email: string, name: string, role: Role) => void }) {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    onAdd(email.trim(), name.trim(), role);
    setEmail("");
    setName("");
  };
  return (
    <form className="add-member" onSubmit={submit} aria-label={t("admin.addForm")}>
      <label>
        <span>{t("admin.col.email")}</span>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label>
        <span>{t("admin.name")}</span>
        <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
      </label>
      <label>
        <span>{t("admin.role")}</span>
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((option) => (
            <option key={option} value={option}>
              {t(`role.${option}`)}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" className="button" disabled={!email.trim() || !name.trim()}>
        {t("admin.add")}
      </button>
      <p className="blocked">{t("admin.addHint")}</p>
    </form>
  );
}

function Settings({
  current,
  onSave,
}: {
  current: TenantSettings;
  onSave: (changes: Record<string, unknown>) => void;
}) {
  const i18n = useI18n();
  const { t } = i18n;
  const [maxPages, setMaxPages] = useState(String(current.settings.max_pages ?? ""));
  const [byContent, setByContent] = useState(
    Boolean(current.settings.auto_on_order_found_by_content),
  );
  useEffect(() => {
    setMaxPages(String(current.settings.max_pages ?? ""));
    setByContent(Boolean(current.settings.auto_on_order_found_by_content));
  }, [current]);
  const operators = Object.keys(current.settings).filter((key) => !current.editable.includes(key));
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    onSave({ max_pages: Number(maxPages), auto_on_order_found_by_content: byContent });
  };
  return (
    <section aria-labelledby="settings-title">
      <h2 id="settings-title">{t("admin.settings")}</h2>
      <form className="settings-form" onSubmit={submit}>
        <label>
          <span>{t("setting.max_pages")}</span>
          <input
            type="number"
            min={1}
            max={500}
            value={maxPages}
            onChange={(event) => setMaxPages(event.target.value)}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={byContent}
            onChange={(event) => setByContent(event.target.checked)}
          />
          <span>{t("setting.auto_on_order_found_by_content")}</span>
        </label>
        <button type="submit" className="button">
          {t("admin.save")}
        </button>
      </form>
      <h3>{t("admin.operator")}</h3>
      <p className="blocked">{t("admin.operatorHint")}</p>
      <dl className="operator-settings">
        {operators.map((key) => (
          <div key={key}>
            <dt>{settingLabel(i18n, key)}</dt>
            <dd>{shown(i18n, current.settings[key])}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
