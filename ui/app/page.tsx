"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useReviewer } from "@/components/Reviewer";
import {
  listDocuments,
  listDuplicateCopies,
  rejectDuplicateCopies,
  uploadDocument,
  type DuplicateCopy,
  type Summary,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { bookingLabel, formatLabel, reviewLabel, statusLabel } from "@/lib/labels";

type View = "pending" | "all";
type UploadNote = { file: string; id?: string } & (
  | { failed: false; duplicate: boolean }
  | { failed: true; reason: string }
);

const REFRESH_MS = 3000;

export default function QueuePage() {
  const [view, setView] = useState<View>("pending");
  const [documents, setDocuments] = useState<Summary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<UploadNote[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const reviewer = useReviewer();
  const i18n = useI18n();
  const { t, tn, money, when } = i18n;
  const [copies, setCopies] = useState<DuplicateCopy[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [rejectNote, setRejectNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      // A document that is still being read is not in the review queue yet,
      // so "all" is always fetched and filtered here.
      const [result, duplicates] = await Promise.all([
        listDocuments(),
        // An extra: if it cannot be listed, the queue still shows and refreshes.
        listDuplicateCopies().catch(() => ({ copies: [] })),
      ]);
      setDocuments(result.documents);
      setCopies(duplicates.copies);
      setError(null);
    } catch (problem) {
      setError(i18n.error(problem));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const waiting = documents?.some((document) => document.status === "received") ?? false;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [waiting, load]);

  async function upload(files: File[]) {
    if (files.length === 0) return;
    setUploading(true);
    const results: UploadNote[] = [];
    for (const file of files) {
      try {
        const result = await uploadDocument(file);
        results.push({ file: file.name, id: result.id, failed: false, duplicate: result.duplicate });
      } catch (problem) {
        const reason = i18n.error(problem);
        results.push({ file: file.name, failed: true, reason });
      }
    }
    setNotes(results);
    setUploading(false);
    await load();
  }

  async function rejectCopies() {
    setRejecting(true);
    try {
      const result = await rejectDuplicateCopies(
        copies.map((copy) => copy.id),
        reviewer.name.trim(),
      );
      const skipped = result.skipped.length;
      setRejectNote(
        skipped
          ? tn("copies.doneSkipped", result.rejected.length, { skipped })
          : tn("copies.done", result.rejected.length),
      );
      setConfirming(false);
    } catch (problem) {
      setRejectNote(i18n.error(problem));
    } finally {
      setRejecting(false);
      await load();
    }
  }

  const pending = documents?.filter((document) => document.review_state === "pending") ?? [];
  const shown = view === "pending" ? pending : (documents ?? []);

  return (
    <div className="queue">
      <section
        className={dragging ? "dropzone dropzone-active" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void upload([...event.dataTransfer.files]);
        }}
      >
        <div>
          <h1>{t("queue.title")}</h1>
          <p>{t("queue.dropHint")}</p>
        </div>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          aria-label={t("queue.files")}
          onChange={(event) => {
            void upload([...(event.target.files ?? [])]);
            event.target.value = "";
          }}
        />
        <button
          type="button"
          className="button button-primary"
          disabled={uploading}
          onClick={() => picker.current?.click()}
        >
          {uploading ? t("queue.uploading") : t("queue.choose")}
        </button>
      </section>

      {notes.length > 0 && (
        <ul className="notes" aria-live="polite">
          {notes.map((note, index) => (
            <li key={index} className={note.failed ? "note note-failed" : "note"}>
              <strong>{note.file}</strong>{" "}
              {note.failed
                ? t("queue.notUploaded", { reason: note.reason })
                : note.duplicate
                  ? t("queue.uploadedBefore")
                  : t("queue.uploaded")}
              {note.id && (
                <>
                  {" "}
                  <Link href={`/documents/${note.id}`}>{t("queue.open")}</Link>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <nav className="tabs" aria-label={t("queue.which")}>
        <button type="button" aria-pressed={view === "pending"} onClick={() => setView("pending")}>
          {t("queue.toReview")} <span className="count">{pending.length}</span>
        </button>
        <button type="button" aria-pressed={view === "all"} onClick={() => setView("all")}>
          {t("queue.all")} <span className="count">{documents?.length ?? 0}</span>
        </button>
      </nav>

      {rejectNote && (
        <p className="note" aria-live="polite">
          {rejectNote}
        </p>
      )}

      {view === "pending" && copies.length > 0 && (
        <section className="copies" aria-label={t("copies.region")}>
          {!confirming ? (
            <button type="button" className="button" onClick={() => setConfirming(true)}>
              {tn("copies.offer", copies.length)}
            </button>
          ) : (
            <>
              <p>{tn("copies.explain", copies.length)}</p>
              <table className="copies-table">
                <thead>
                  <tr>
                    <th scope="col">{t("copies.col.copy")}</th>
                    <th scope="col">{t("queue.col.seller")}</th>
                    <th scope="col" className="num">
                      {t("queue.col.gross")}
                    </th>
                    <th scope="col">{t("queue.col.received")}</th>
                    <th scope="col">{t("copies.col.copyOf")}</th>
                  </tr>
                </thead>
                <tbody>
                  {copies.map((copy) => (
                    <tr key={copy.id}>
                      <td>
                        <Link href={`/documents/${copy.id}`}>{copy.number ?? copy.id}</Link>
                      </td>
                      <td>{copy.seller_name ?? "–"}</td>
                      <td className="num">{money(copy.gross_total, copy.currency)}</td>
                      <td>{when(copy.created_at)}</td>
                      <td>
                        <Link href={`/documents/${copy.original.id}`}>
                          {copy.original.number ?? copy.original.id}
                        </Link>
                        , {when(copy.original.created_at)}
                        {bookingLabel(i18n, copy.original.bookings) && (
                          <span className="booked">
                            {" "}
                            · {bookingLabel(i18n, copy.original.bookings)}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="action-row">
                <button
                  type="button"
                  className="button button-danger"
                  disabled={rejecting || reviewer.problem !== null}
                  onClick={() => void rejectCopies()}
                >
                  {rejecting ? t("copies.rejecting") : tn("copies.confirm", copies.length)}
                </button>
                <button type="button" className="button" onClick={() => setConfirming(false)}>
                  {t("copies.cancel")}
                </button>
              </div>
              {reviewer.problem && <p className="blocked">{reviewer.problem}</p>}
            </>
          )}
        </section>
      )}

      {error && (
        <p className="problem" role="alert">
          {error}
        </p>
      )}

      {documents !== null && shown.length === 0 && !error && (
        <p className="empty">
          {view === "pending" ? t("queue.emptyPending") : t("queue.emptyAll")}
        </p>
      )}

      {shown.length > 0 && (
        <div className="table-scroll">
          <table className="documents">
            <thead>
              <tr>
                <th>{t("queue.col.invoice")}</th>
                <th>{t("queue.col.seller")}</th>
                <th className="number">{t("queue.col.gross")}</th>
                <th>{t("queue.col.kind")}</th>
                <th>{t("queue.col.result")}</th>
                <th>{t("queue.col.review")}</th>
                <th>{t("queue.col.booked")}</th>
                <th>{t("queue.col.received")}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((document) => (
                <tr key={document.id}>
                  <td>
                    <Link href={`/documents/${document.id}`}>
                      {document.number ?? document.filename ?? t("queue.unnamed")}
                    </Link>
                  </td>
                  <td>{document.seller_name ?? ""}</td>
                  <td className="number">{money(document.gross_total, document.currency)}</td>
                  <td>{formatLabel(i18n, document.format)}</td>
                  <td>{statusLabel(i18n, document.status)}</td>
                  <td>
                    <span className={`state state-${document.review_state}`}>
                      {reviewLabel(i18n, document.review_state)}
                    </span>
                    {document.findings_to_review > 0 && (
                      <span className="finding-count">
                        {tn("queue.findings", document.findings_to_review)}
                      </span>
                    )}
                  </td>
                  <td className="booked">{bookingLabel(i18n, document.bookings) || "–"}</td>
                  <td>{when(document.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
