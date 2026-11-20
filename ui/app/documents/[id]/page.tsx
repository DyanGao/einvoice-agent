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
  recheckDocument,
  rejectDocument,
  reopenDocument,
  type AuditEvent,
  type Delivery,
  type Detail,
  type RejectReason,
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
import { useI18n, type Translator } from "@/lib/i18n";
import {
  basisLabel,
  causeLabel,
  connectorLabel,
  findingLabel,
  formatLabel,
  REJECT_REASONS,
  rejectReasonLabel,
  statusLabel,
} from "@/lib/labels";

const REFRESH_MS = 3000;

/** A value as text; null when there is none. */
function shown(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return Array.isArray(value) || typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** A value for a sentence: "nothing" in the reader's language when empty. */
function said(i18n: Translator, value: unknown): string {
  return shown(value) ?? i18n.t("value.nothing");
}

/** What the reviewer should know about one field, taken from the server's answer. */
function marksFor(detail: Detail, path: string) {
  const confidence = (detail.fields?.field_confidence as Record<string, number> | undefined)?.[
    path
  ];
  const original = valueAt(detail.original, path);
  const current = valueAt(detail.fields, path);
  const derived = (detail.fields?.derived_fields as string[] | undefined) ?? [];
  // What was read before the reviewer changed it; null when unchanged.
  // `text` null: nothing was read there.
  const was: { text: string | null } | null =
    detail.original !== null && JSON.stringify(original) !== JSON.stringify(current)
      ? { text: shown(original) }
      : null;
  return {
    // What the checks say about this field, if anything.
    findings: detail.findings.filter((finding) => finding.fields.includes(path)),
    // Still the value a rule filled in, not one the reviewer has since typed.
    derived: derived.includes(path) && JSON.stringify(original) === JSON.stringify(current),
    missing: detail.missing.includes(path),
    confidence: confidence !== undefined && confidence < LOW_CONFIDENCE ? confidence : null,
    was,
  };
}

function wasText(i18n: Translator, was: { text: string | null }): string {
  return was.text === null
    ? i18n.t("hint.nothingRead")
    : i18n.t("hint.readAs", { value: was.text });
}

function Hints({ marks }: { marks: ReturnType<typeof marksFor> }) {
  const i18n = useI18n();
  const { t } = i18n;
  return (
    <>
      {marks.confidence !== null && (
        <span className="hint hint-unsure">
          {t("hint.sure", { percent: i18n.percent(marks.confidence) })}
        </span>
      )}
      {marks.derived && <span className="hint hint-unsure">{t("hint.derived")}</span>}
      {marks.missing && <span className="hint hint-missing">{t("hint.needed")}</span>}
      {marks.findings.map((finding, index) => (
        <span key={index} className="hint hint-missing">
          {findingLabel(i18n, finding.type)}
        </span>
      ))}
      {marks.was !== null && <span className="hint">{wasText(i18n, marks.was)}</span>}
    </>
  );
}

/** A table cell has no room for hints; they go into its tooltip. */
function cellTitle(i18n: Translator, marks: ReturnType<typeof marksFor>): string | undefined {
  const notes = marks.findings.map((finding) => finding.message);
  if (marks.was !== null) notes.push(wasText(i18n, marks.was));
  return notes.length > 0 ? notes.join("\n") : undefined;
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

function deliveryText(i18n: Translator, delivery: Delivery): string {
  const { t } = i18n;
  const target = connectorLabel(i18n, delivery.connector);
  if (delivery.status === "delivered") {
    const booked = { target, ref: delivery.external_ref ?? "" };
    return delivery.delivered_at
      ? t("delivery.deliveredOn", { ...booked, when: i18n.when(delivery.delivered_at) })
      : t("delivery.delivered", booked);
  }
  if (delivery.status === "failed") {
    return t("delivery.failed", { target, reason: delivery.last_error ?? t("delivery.noReason") });
  }
  return delivery.last_error
    ? t("delivery.retrying", { target, attempts: delivery.attempts, reason: delivery.last_error })
    : t("delivery.waiting", { target });
}

/** The reason a document was rejected, with the note, as a short phrase. */
function rejectionReason(i18n: Translator, event: AuditEvent): string {
  const detail = event.detail ?? {};
  const reason = rejectReasonLabel(i18n, String(detail.reason));
  return detail.note ? `${reason} (${String(detail.note)})` : reason;
}

function auditText(i18n: Translator, event: AuditEvent): string {
  const { t } = i18n;
  const detail = event.detail ?? {};
  if (event.event === "field_corrected") {
    return t("audit.field_corrected", {
      field: labelFor(i18n, String(detail.field)),
      old: said(i18n, detail.old),
      new: said(i18n, detail.new),
    });
  }
  if (event.event === "approved") return t("audit.approved");
  if (event.event === "rejected") {
    const reason = rejectReasonLabel(i18n, String(detail.reason));
    return detail.note
      ? t("audit.rejectedNote", { reason, note: String(detail.note) })
      : t("audit.rejected", { reason });
  }
  if (event.event === "reopened") return t("audit.reopened", { note: String(detail.note) });
  const name = event.event.replaceAll("_", " ");
  const text = i18n.tx(`audit.${event.event}`, name.charAt(0).toUpperCase() + name.slice(1));
  // A re-match says what set it off: a later invoice or a new bank transfer.
  return typeof detail.trigger === "string"
    ? t("audit.again", { event: text, trigger: detail.trigger })
    : text;
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const reviewer = useReviewer();
  const i18n = useI18n();
  const { t, tn, when } = i18n;
  const [detail, setDetail] = useState<Detail | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rejectReason, setRejectReason] = useState<RejectReason | "">("");
  const [rejectNote, setRejectNote] = useState("");
  const [reopenNote, setReopenNote] = useState("");

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
        <Link href="/">{t("doc.backFromError")}</Link>
      </div>
    );
  }
  if (!detail || !form) return <p className="empty">{t("doc.loading")}</p>;

  const approved = detail.review_state === "approved";
  const editable = detail.review_state === "pending" && !waiting;
  const approval = detail.audit.findLast((event) => event.event === "approved");
  const rejected = detail.review_state === "rejected";
  const rejection = detail.audit.findLast((event) => event.event === "rejected");

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
  const reject = () =>
    rejectReason !== "" &&
    run(() => rejectDocument(id, reviewer.name.trim(), rejectReason, rejectNote));

  const reopen = () =>
    run(() => reopenDocument(id, reviewer.name.trim(), reopenNote.trim())).then(() =>
      setReopenNote(""),
    );

  // The name is asked for once, above; this says only what the rejection lacks.
  let rejectNeeds: string | null = null;
  if (rejectReason === "") rejectNeeds = t("doc.reject.needReason");
  else if (rejectReason === "other" && rejectNote.trim() === "") {
    rejectNeeds = t("doc.reject.needNote");
  }

  const setScalar = (path: string, value: string) =>
    setForm({ ...form, scalars: { ...form.scalars, [path]: value } });
  const setRows = (path: string, rows: Record<string, string>[]) =>
    setForm({ ...form, tables: { ...form.tables, [path]: rows } });

  let blocked: string | null = null;
  if (reviewer.problem) blocked = reviewer.problem;
  else if (changeCount > 0) blocked = t("doc.blocked.save");
  else if (detail.fields === null) blocked = t("doc.blocked.fill");
  else if (detail.missing.length > 0) {
    const fields = detail.missing.map((path) => labelFor(i18n, path)).join(", ");
    blocked = t("doc.blocked.needed", { fields });
  }

  const ibanMarks = marksFor(detail, "payment_ibans");

  return (
    <div className="review">
      <section className="document-pane" aria-label={t("doc.region")}>
        {detail.content_type === "application/pdf" && (
          // The fragment asks the browser's PDF viewer for the page at full width, without side panels.
          <iframe src={`${contentUrl(id)}#navpanes=0&view=FitH`} title={t("doc.original")} />
        )}
        {detail.content_type?.startsWith("image/") && (
          <img src={contentUrl(id)} alt={t("doc.original")} />
        )}
        {detail.content_type === null && (
          <div className="no-preview">
            <p>{t("doc.noPreview")}</p>
            <a href={contentUrl(id)}>{t("doc.download")}</a>
          </div>
        )}
      </section>

      <section className="fields-pane">
        <header className="review-head">
          <Link href="/" className="back">
            {t("doc.back")}
          </Link>
          <h1>{detail.number ?? detail.filename ?? t("queue.unnamed")}</h1>
          <p className="review-meta">
            {t(detail.filename ? "doc.metaFile" : "doc.meta", {
              format: formatLabel(i18n, detail.format),
              status: i18n.inSentence(statusLabel(i18n, detail.status)),
              when: when(detail.created_at),
              filename: detail.filename ?? "",
            })}
          </p>
          {approved && approval && (
            <p className="stamp" data-testid="stamp">
              <strong>{t("doc.stamp.approved")}</strong>
              <span>{approval.actor}</span>
              <span>{when(approval.created_at)}</span>
            </p>
          )}
          {rejected && rejection && (
            <p className="stamp stamp-rejected" data-testid="stamp">
              <strong>{t("doc.stamp.rejected")}</strong>
              <span>{rejection.actor}</span>
              <span>{when(rejection.created_at)}</span>
            </p>
          )}
        </header>

        {waiting && <p className="notice">{t("doc.waiting")}</p>}
        {detail.error && (
          <p className="notice notice-problem">
            <strong>{t("doc.readingStopped")}</strong> {detail.error}
          </p>
        )}
        {rejected && rejection && (
          <p className="notice">
            {t("doc.rejectedNotBooked", {
              reason: i18n.inSentence(rejectionReason(i18n, rejection)),
            })}
          </p>
        )}
        {rejected && (
          <details className="reject">
            <summary>{t("doc.reopen.open")}</summary>
            <p className="reject-hint">{t("doc.reopen.hint")}</p>
            <label>
              {t("doc.reopen.why")}
              <input
                type="text"
                value={reopenNote}
                maxLength={500}
                onChange={(event) => setReopenNote(event.target.value)}
              />
            </label>
            {actionError && (
              <p className="problem" role="alert">
                {actionError}
              </p>
            )}
            <button
              type="button"
              className="button"
              disabled={busy || reopenNote.trim() === "" || reviewer.problem !== null}
              onClick={() => void reopen()}
            >
              {t("doc.reopen.submit")}
            </button>
            {reviewer.problem && <p className="blocked">{reviewer.problem}</p>}
          </details>
        )}
        {detail.review_state === "not_required" && (
          <p className="notice">{t("doc.notRequired")}</p>
        )}

        {detail.deliveries.map((delivery) => (
          <p
            key={delivery.connector}
            className={delivery.status === "failed" ? "notice notice-problem" : "notice"}
          >
            {deliveryText(i18n, delivery)}
          </p>
        ))}

        {detail.review_state === "pending" && detail.decision?.route === "review" && (
          <section className="group" aria-label={t("doc.whyReview.region")}>
            <h2>{t("doc.whyReview.title")}</h2>
            <ul className="violations">
              {detail.decision.reasons.map((reason, index) => (
                <li key={index} className="violation">
                  <strong>{causeLabel(i18n, reason.cause)}</strong>{" "}
                  {reason.cause === "findings" ? t("doc.whyReview.seeChecks") : reason.message}
                </li>
              ))}
            </ul>
          </section>
        )}

        {detail.findings.length > 0 && (
          <section className="group">
            <h2>{t("doc.checks")}</h2>
            <ul className="violations">
              {detail.findings.map((finding, index) => (
                <li
                  key={index}
                  className={
                    finding.severity === "review" ? "violation violation-error" : "violation"
                  }
                >
                  <strong>{findingLabel(i18n, finding.type)}</strong> {finding.message}
                </li>
              ))}
            </ul>
          </section>
        )}

        {detail.match && (
          <section className="group" aria-label={t("doc.match.region")}>
            <h2>{t("doc.match.title")}</h2>
            <dl className="match">
              <dt>{t("doc.match.order")}</dt>
              <dd>
                {detail.match.po_number ?? t("doc.match.none")}{" "}
                <span className="basis">{basisLabel(i18n, detail.match.po_basis)}</span>
              </dd>
              <dt>{t("doc.match.delivery")}</dt>
              <dd>{detail.match.delivery_id ?? t("doc.match.none")}</dd>
              <dt>{t("doc.match.payments")}</dt>
              <dd>
                {detail.match.payments.length === 0
                  ? t("doc.match.none")
                  : detail.match.payments
                      .map(
                        (payment) =>
                          `${payment.bank_transaction_id} (${i18n.money(payment.amount, null)})`,
                      )
                      .join(", ")}{" "}
                <span className="basis">{basisLabel(i18n, detail.match.payment_basis)}</span>
              </dd>
            </dl>
            <ul className="match-why">
              {detail.match.explanation.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
            {!approved && !rejected && (
              <button
                type="button"
                className="button button-quiet"
                disabled={busy}
                onClick={() => void run(() => recheckDocument(id))}
              >
                {t("doc.match.again")}
              </button>
            )}
          </section>
        )}

        {detail.validation && detail.validation.violations.length > 0 && (
          <section className="group">
            <h2>{t("doc.validator")}</h2>
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
                <h2>{t(group.title)}</h2>
                <div className="grid">
                  {group.fields.map((field) => {
                    const marks = marksFor(detail, field.path);
                    return (
                      <div
                        key={field.path}
                        className="field"
                        data-unsure={marks.confidence !== null || undefined}
                        data-missing={marks.missing || marks.findings.length > 0 || undefined}
                      >
                        <label htmlFor={`f-${field.path}`}>{t(field.label)}</label>
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
              <h2>{t("doc.payment")}</h2>
              <div
                className="field"
                data-unsure={ibanMarks.confidence !== null || undefined}
                data-missing={ibanMarks.missing || ibanMarks.findings.length > 0 || undefined}
              >
                <label htmlFor="f-payment_ibans">{t("doc.ibans")}</label>
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
                  <h2>{t(table.title)}</h2>
                  {rows.length === 0 && <p className="empty-rows">{t("doc.noneRead")}</p>}
                  {rows.length > 0 && (
                    <div className="table-scroll">
                      <table className="rows">
                        <thead>
                          <tr>
                            {table.columns.map((column) => (
                              <th key={column.key} className={column.kind === "amount" ? "number" : undefined}>
                                {t(column.label)}
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
                                    data-missing={
                                      marks.missing || marks.findings.length > 0 || undefined
                                    }
                                    title={cellTitle(i18n, marks)}
                                  >
                                    <input
                                      aria-label={labelFor(i18n, path)}
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
                                  aria-label={t("doc.removeRow", {
                                    row: t(table.rowName),
                                    number: index + 1,
                                  })}
                                  onClick={() =>
                                    setRows(
                                      table.path,
                                      rows.filter((_, at) => at !== index),
                                    )
                                  }
                                >
                                  {t("doc.remove")}
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
                      {t(table.add)}
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
                  {changeCount > 0 ? tn("doc.save", changeCount) : t("doc.saveNone")}
                </button>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={busy || blocked !== null}
                  onClick={() => void approve()}
                >
                  {t("doc.approve")}
                </button>
              </div>
              {blocked && <p className="blocked">{blocked}</p>}
              <details className="reject">
                <summary>{t("doc.reject.open")}</summary>
                <p className="reject-hint">{t("doc.reject.hint")}</p>
                <label>
                  {t("doc.reject.reason")}
                  <select
                    value={rejectReason}
                    onChange={(event) => setRejectReason(event.target.value as RejectReason | "")}
                  >
                    <option value="">{t("doc.reject.choose")}</option>
                    {REJECT_REASONS.map((reason) => (
                      <option key={reason} value={reason}>
                        {rejectReasonLabel(i18n, reason)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {rejectReason === "other" ? t("doc.reject.note") : t("doc.reject.noteOptional")}
                  <input
                    type="text"
                    value={rejectNote}
                    maxLength={500}
                    onChange={(event) => setRejectNote(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button button-danger"
                  disabled={busy || rejectNeeds !== null || reviewer.problem !== null}
                  onClick={() => void reject()}
                >
                  {t("doc.reject.submit")}
                </button>
                {rejectNeeds && <p className="blocked">{rejectNeeds}</p>}
              </details>
            </div>
          )}
        </form>

        <section className="group">
          <h2>{t("doc.history")}</h2>
          <ol className="audit">
            {detail.audit.map((event) => (
              <li key={event.seq}>
                <span className="audit-what">{auditText(i18n, event)}</span>
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
