"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useReviewer } from "@/components/Reviewer";
import {
  approveDocument,
  contentUrl,
  correctFields,
  getDocument,
  type AuditEvent,
  type Detail,
  type Violation,
} from "@/lib/api";
import {
  changesFrom,
  formFrom,
  GROUPS,
  labelFor,
  LOW_CONFIDENCE,
  TABLES,
  valueAt,
  type FormState,
  type Kind,
} from "@/lib/form";
import { formatLabel, statusLabel, when } from "@/lib/labels";

const REFRESH_MS = 3000;

const NOTHING = "nothing";

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return NOTHING;
  return Array.isArray(value) || typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** What the reviewer should know about one field, taken from the server's answer. */
function marksFor(detail: Detail, path: string) {
  const confidence = (detail.fields?.field_confidence as Record<string, number> | undefined)?.[
    path
  ];
  const original = valueAt(detail.original, path);
  const current = valueAt(detail.fields, path);
  const derived = (detail.fields?.derived_fields as string[] | undefined) ?? [];
  return {
    // Still the value a rule filled in, not one the reviewer has since typed.
    derived: derived.includes(path) && JSON.stringify(original) === JSON.stringify(current),
    missing: detail.missing.includes(path),
    confidence: confidence !== undefined && confidence < LOW_CONFIDENCE ? confidence : null,
    was:
      detail.original !== null && JSON.stringify(original) !== JSON.stringify(current)
        ? shown(original)
        : null,
  };
}

function Hints({ marks }: { marks: ReturnType<typeof marksFor> }) {
  return (
    <>
      {marks.confidence !== null && (
        <span className="hint hint-unsure">{Math.round(marks.confidence * 100)}% sure</span>
      )}
      {marks.derived && (
        <span className="hint hint-unsure">Filled in by a rule, not read from the document</span>
      )}
      {marks.missing && <span className="hint hint-missing">Needed for approval</span>}
      {marks.was !== null && (
        <span className="hint">
          {marks.was === NOTHING ? "Nothing was read here" : `Read as ${marks.was}`}
        </span>
      )}
    </>
  );
}

function inputProps(kind: Kind) {
  if (kind === "date") return { type: "date" } as const;
  if (kind === "amount") return { type: "text", inputMode: "decimal" } as const;
  return { type: "text" } as const;
}

/** The validator repeats the rule in its message ("[BR-DE-15] Das Element …"); show it once. */
function withoutRulePrefix(violation: Violation): string {
  const prefix = `[${violation.rule_id}]`;
  return violation.message.startsWith(prefix)
    ? violation.message.slice(prefix.length).trimStart()
    : violation.message;
}

function auditText(event: AuditEvent): string {
  const detail = event.detail ?? {};
  if (event.event === "field_corrected") {
    return `${labelFor(String(detail.field))} changed from ${shown(detail.old)} to ${shown(detail.new)}`;
  }
  if (event.event === "approved") return "Approved";
  const name = event.event.replaceAll("_", " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const reviewer = useReviewer();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const show = useCallback((next: Detail) => {
    setDetail(next);
    setForm(formFrom(next.fields));
  }, []);

  const load = useCallback(async () => {
    try {
      show(await getDocument(id));
      setLoadError(null);
    } catch (problem) {
      setLoadError(problem instanceof Error ? problem.message : String(problem));
    }
  }, [id, show]);

  useEffect(() => {
    void load();
  }, [load]);

  const waiting = detail?.status === "received";
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [waiting, load]);

  const changes = useMemo(
    () => (detail && form ? changesFrom(detail.fields, form) : {}),
    [detail, form],
  );
  const changeCount = Object.keys(changes).length;

  useEffect(() => {
    if (changeCount === 0) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [changeCount]);

  if (loadError && !detail) {
    return (
      <div className="review-missing">
        <p className="problem" role="alert">
          {loadError}
        </p>
        <Link href="/">Back to incoming invoices</Link>
      </div>
    );
  }
  if (!detail || !form) return <p className="empty">Loading the document…</p>;

  const approved = detail.review_state === "approved";
  const editable = detail.review_state === "pending" && !waiting;
  const approval = detail.audit.findLast((event) => event.event === "approved");

  async function run(action: () => Promise<Detail>) {
    setBusy(true);
    setActionError(null);
    try {
      show(await action());
    } catch (problem) {
      setActionError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  }

  const save = () => run(() => correctFields(id, changes, reviewer.name.trim()));
  const approve = () => run(() => approveDocument(id, reviewer.name.trim()));

  const setScalar = (path: string, value: string) =>
    setForm({ ...form, scalars: { ...form.scalars, [path]: value } });
  const setRows = (path: string, rows: Record<string, string>[]) =>
    setForm({ ...form, tables: { ...form.tables, [path]: rows } });

  let blocked: string | null = null;
  if (reviewer.problem) blocked = reviewer.problem;
  else if (changeCount > 0) blocked = "Save your changes before approving.";
  else if (detail.fields === null) blocked = "Fill in the invoice fields before approving.";
  else if (detail.missing.length > 0) {
    blocked = `Still needed: ${detail.missing.map(labelFor).join(", ")}.`;
  }

  const ibanMarks = marksFor(detail, "payment_ibans");

  return (
    <div className="review">
      <section className="document-pane" aria-label="Document">
        {detail.content_type === "application/pdf" && (
          // The fragment asks the browser's PDF viewer for the page at full width, without side panels.
          <iframe src={`${contentUrl(id)}#navpanes=0&view=FitH`} title="Original document" />
        )}
        {detail.content_type?.startsWith("image/") && (
          <img src={contentUrl(id)} alt="Original document" />
        )}
        {detail.content_type === null && (
          <div className="no-preview">
            <p>
              This file has no page to show. Structured e-invoices are data, not a picture of an
              invoice.
            </p>
            <a href={contentUrl(id)}>Download the original file</a>
          </div>
        )}
      </section>

      <section className="fields-pane">
        <header className="review-head">
          <Link href="/" className="back">
            Incoming invoices
          </Link>
          <h1>{detail.number ?? detail.filename ?? "Unnamed document"}</h1>
          <p className="review-meta">
            {formatLabel(detail.format)}, {statusLabel(detail.status).toLowerCase()}. Received{" "}
            {when(detail.created_at)}
            {detail.filename ? ` as ${detail.filename}` : ""}.
          </p>
          {approved && approval && (
            <p className="stamp" data-testid="stamp">
              <strong>Approved</strong>
              <span>{approval.actor}</span>
              <span>{when(approval.created_at)}</span>
            </p>
          )}
        </header>

        {waiting && <p className="notice">This document is still being read. The page updates by itself.</p>}
        {detail.error && (
          <p className="notice notice-problem">
            <strong>Reading stopped:</strong> {detail.error}
          </p>
        )}
        {detail.review_state === "not_required" && (
          <p className="notice">
            The validator accepted this e-invoice, so it needs no review. The fields are shown as
            read.
          </p>
        )}

        {detail.validation && detail.validation.violations.length > 0 && (
          <section className="group">
            <h2>Validator findings</h2>
            <ul className="violations">
              {detail.validation.violations.map((violation, index) => (
                <li key={index} className={`violation violation-${violation.severity}`}>
                  <strong>{violation.rule_id}</strong> {withoutRulePrefix(violation)}
                </li>
              ))}
            </ul>
          </section>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <fieldset disabled={!editable || busy}>
            {GROUPS.map((group) => (
              <section className="group" key={group.title}>
                <h2>{group.title}</h2>
                <div className="grid">
                  {group.fields.map((field) => {
                    const marks = marksFor(detail, field.path);
                    return (
                      <div
                        key={field.path}
                        className="field"
                        data-unsure={marks.confidence !== null || undefined}
                        data-missing={marks.missing || undefined}
                      >
                        <label htmlFor={`f-${field.path}`}>{field.label}</label>
                        <input
                          id={`f-${field.path}`}
                          {...inputProps(field.kind)}
                          className={field.kind === "amount" ? "number" : undefined}
                          value={form.scalars[field.path] ?? ""}
                          onChange={(event) => setScalar(field.path, event.target.value)}
                        />
                        <Hints marks={marks} />
                      </div>
                    );
                  })}
                </div>
              </section>
            ))}

            <section className="group">
              <h2>Payment</h2>
              <div
                className="field"
                data-unsure={ibanMarks.confidence !== null || undefined}
                data-missing={ibanMarks.missing || undefined}
              >
                <label htmlFor="f-payment_ibans">Payment IBANs, one per line</label>
                <textarea
                  id="f-payment_ibans"
                  rows={2}
                  value={form.ibans}
                  onChange={(event) => setForm({ ...form, ibans: event.target.value })}
                />
                <Hints marks={ibanMarks} />
              </div>
            </section>

            {TABLES.map((table) => {
              const rows = form.tables[table.path] ?? [];
              return (
                <section className="group" key={table.path}>
                  <h2>{table.title}</h2>
                  {rows.length === 0 && <p className="empty-rows">None read.</p>}
                  {rows.length > 0 && (
                    <div className="table-scroll">
                      <table className="rows">
                        <thead>
                          <tr>
                            {table.columns.map((column) => (
                              <th key={column.key} className={column.kind === "amount" ? "number" : undefined}>
                                {column.label}
                              </th>
                            ))}
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((row, index) => (
                            <tr key={index}>
                              {table.columns.map((column) => {
                                const path = `${table.path}.${index}.${column.key}`;
                                const marks = marksFor(detail, path);
                                return (
                                  <td
                                    key={column.key}
                                    data-unsure={marks.confidence !== null || undefined}
                                    data-missing={marks.missing || undefined}
                                    title={marks.was !== null ? `Read as ${marks.was}` : undefined}
                                  >
                                    <input
                                      aria-label={labelFor(path)}
                                      {...inputProps(column.kind)}
                                      className={column.kind === "amount" ? "number" : undefined}
                                      value={row[column.key] ?? ""}
                                      onChange={(event) =>
                                        setRows(
                                          table.path,
                                          rows.map((other, at) =>
                                            at === index
                                              ? { ...other, [column.key]: event.target.value }
                                              : other,
                                          ),
                                        )
                                      }
                                    />
                                  </td>
                                );
                              })}
                              <td>
                                <button
                                  type="button"
                                  className="button button-quiet"
                                  aria-label={`Remove ${table.rowName} ${index + 1}`}
                                  onClick={() =>
                                    setRows(
                                      table.path,
                                      rows.filter((_, at) => at !== index),
                                    )
                                  }
                                >
                                  Remove
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {editable && (
                    <button
                      type="button"
                      className="button button-quiet"
                      onClick={() =>
                        setRows(table.path, [
                          ...rows,
                          Object.fromEntries(table.columns.map((column) => [column.key, ""])),
                        ])
                      }
                    >
                      {table.add}
                    </button>
                  )}
                </section>
              );
            })}
          </fieldset>

          {editable && (
            <div className="actions">
              {actionError && (
                <p className="problem" role="alert">
                  {actionError}
                </p>
              )}
              <div className="action-row">
                <button
                  type="submit"
                  className="button"
                  disabled={busy || changeCount === 0 || reviewer.problem !== null}
                >
                  {changeCount > 0
                    ? `Save ${changeCount} ${changeCount === 1 ? "change" : "changes"}`
                    : "Save changes"}
                </button>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={busy || blocked !== null}
                  onClick={() => void approve()}
                >
                  Approve
                </button>
              </div>
              {blocked && <p className="blocked">{blocked}</p>}
            </div>
          )}
        </form>

        <section className="group">
          <h2>History</h2>
          <ol className="audit">
            {detail.audit.map((event) => (
              <li key={event.seq}>
                <span className="audit-what">{auditText(event)}</span>
                <span className="audit-who">
                  {event.actor ? `${event.actor}, ` : ""}
                  {when(event.created_at)}
                </span>
              </li>
            ))}
          </ol>
        </section>
      </section>
    </div>
  );
}
