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

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "viewer", label: "Viewer", hint: "reads" },
  { value: "reviewer", label: "Reviewer", hint: "corrects, approves, rejects, uploads" },
  { value: "admin", label: "Admin", hint: "everything, and this page" },
];

/** How each setting is called on this page. */
const SETTING_LABELS: Record<string, string> = {
  max_pages: "Pages read automatically, at most",
  auto_on_order_found_by_content: "Book on an order found by its contents alone",
  erp_url: "ERP",
  erp_timeout_seconds: "ERP timeout (seconds)",
  erp_channel: "Booking in the ERP through",
  erp_ui_url: "ERP screens",
  browser_allowed_hosts: "Browser may reach",
  export_dir: "Booking lists (CSV) folder",
  webhook_url: "Webhook",
  webhook_secret_env: "Webhook secret, held in",
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "–";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "–";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

export default function AdminPage() {
  const { session } = useReviewer();
  if (session.kind === "open") {
    return (
      <div className="queue">
        <h1>Admin</h1>
        <p className="empty">
          Sign-in is off (AUTH=off): members and settings are managed with the einvoice command
          line.
        </p>
      </div>
    );
  }
  if (session.kind !== "signed-in") return null;
  if (session.me.role !== "admin") {
    return (
      <div className="queue">
        <h1>Admin</h1>
        <p className="problem" role="alert">
          This page needs the admin role in {session.me.tenant.name}.
        </p>
        <Link href="/">Back to the invoices</Link>
      </div>
    );
  }
  return <Admin myEmail={session.me.email} tenantName={session.me.tenant.name} />;
}

function Admin({ myEmail, tenantName }: { myEmail: string | null; tenantName: string }) {
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
      .catch((error: unknown) => setProblem(message(error)));
  }, []);

  useEffect(load, [load]);

  const run = (action: Promise<unknown>, done: string) => {
    setProblem(null);
    setNote(null);
    action
      .then(() => {
        setNote(done);
        load();
      })
      .catch((error: unknown) => setProblem(message(error)));
  };

  return (
    <div className="queue admin">
      <h1>Admin · {tenantName}</h1>
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
        <h2 id="members-title">Members</h2>
        {members && (
          <div className="table-scroll">
            <table className="documents">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Signs in</th>
                  <th>Role</th>
                  <th>
                    <span className="visually-hidden">Remove</span>
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
                      <td>{member.password ? "with password" : "through the provider"}</td>
                      <td>
                        <select
                          aria-label={`Role of ${member.name}`}
                          value={member.role}
                          disabled={me}
                          title={me ? "Another admin can change your role." : undefined}
                          onChange={(event) =>
                            run(
                              changeMemberRole(member.user_id, event.target.value as Role),
                              `${member.name} is now ${event.target.value}.`,
                            )
                          }
                        >
                          {ROLES.map((role) => (
                            <option key={role.value} value={role.value}>
                              {role.label}
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
                              if (
                                window.confirm(
                                  `Remove ${member.name} from ${tenantName}? Their sessions and tokens here stop working at once.`,
                                )
                              ) {
                                run(removeMember(member.user_id), `${member.name} was removed.`);
                              }
                            }}
                          >
                            Remove
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
        <AddMember onAdd={(email, name, role) => run(addMember(email, name, role), `${email} was added.`)} />
        <ul className="role-hints">
          {ROLES.map((role) => (
            <li key={role.value}>
              <strong>{role.label}</strong> {role.hint}
            </li>
          ))}
        </ul>
      </section>

      {settings && (
        <Settings
          current={settings}
          onSave={(changes) => run(changeTenantSettings(changes), "Settings saved.")}
        />
      )}
    </div>
  );
}

function AddMember({ onAdd }: { onAdd: (email: string, name: string, role: Role) => void }) {
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
    <form className="add-member" onSubmit={submit} aria-label="Add a member">
      <label>
        <span>Email</span>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label>
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
      </label>
      <label>
        <span>Role</span>
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" className="button" disabled={!email.trim() || !name.trim()}>
        Add member
      </button>
      <p className="blocked">
        They sign in through the identity provider with this email address.
      </p>
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
  const [maxPages, setMaxPages] = useState(String(current.settings.max_pages ?? ""));
  const [byContent, setByContent] = useState(Boolean(current.settings.auto_on_order_found_by_content));
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
      <h2 id="settings-title">Settings</h2>
      <form className="settings-form" onSubmit={submit}>
        <label>
          <span>{SETTING_LABELS.max_pages}</span>
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
          <span>{SETTING_LABELS.auto_on_order_found_by_content}</span>
        </label>
        <button type="submit" className="button">
          Save settings
        </button>
      </form>
      <h3>Set by the server&apos;s operator</h3>
      <p className="blocked">
        These reach the server&apos;s network and files, so they are changed with{" "}
        <code>einvoice tenant-set</code>, not here.
      </p>
      <dl className="operator-settings">
        {operators.map((key) => (
          <div key={key}>
            <dt>{SETTING_LABELS[key] ?? key}</dt>
            <dd>{shown(current.settings[key])}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
