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
import { formatLabel, money, reviewLabel, statusLabel, when } from "@/lib/labels";

type View = "pending" | "all";
type UploadNote = { file: string; text: string; id?: string; failed: boolean };

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
      setError(problem instanceof Error ? problem.message : String(problem));
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
        results.push({
          file: file.name,
          id: result.id,
          failed: false,
          text: result.duplicate ? "was uploaded before" : "uploaded",
        });
      } catch (problem) {
        const text = problem instanceof Error ? problem.message : String(problem);
        results.push({ file: file.name, failed: true, text });
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
        `${result.rejected.length} ${result.rejected.length === 1 ? "copy" : "copies"} rejected` +
          (skipped ? `; ${skipped} changed in the meantime and stay for review` : "") +
          ".",
      );
      setConfirming(false);
    } catch (problem) {
      setRejectNote(problem instanceof Error ? problem.message : String(problem));
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
          <h1>Incoming invoices</h1>
          <p>Drop XRechnung, ZUGFeRD, PDF or scanned invoices here, or choose files.</p>
        </div>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          aria-label="Invoice files"
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
          {uploading ? "Uploading…" : "Choose files"}
        </button>
      </section>

      {notes.length > 0 && (
        <ul className="notes" aria-live="polite">
          {notes.map((note, index) => (
            <li key={index} className={note.failed ? "note note-failed" : "note"}>
              <strong>{note.file}</strong>{" "}
              {note.failed ? `was not uploaded: ${note.text}` : note.text}
              {note.id && (
                <>
                  {" "}
                  <Link href={`/documents/${note.id}`}>Open</Link>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <nav className="tabs" aria-label="Which documents to show">
        <button type="button" aria-pressed={view === "pending"} onClick={() => setView("pending")}>
          To review <span className="count">{pending.length}</span>
        </button>
        <button type="button" aria-pressed={view === "all"} onClick={() => setView("all")}>
          All documents <span className="count">{documents?.length ?? 0}</span>
        </button>
      </nav>

      {rejectNote && (
        <p className="note" aria-live="polite">
          {rejectNote}
        </p>
      )}

      {view === "pending" && copies.length > 0 && (
        <section className="copies" aria-label="Duplicates">
          {!confirming ? (
            <button type="button" className="button" onClick={() => setConfirming(true)}>
              Reject {copies.length} plain {copies.length === 1 ? "duplicate" : "duplicates"}…
            </button>
          ) : (
            <>
              <p>
                {copies.length === 1 ? "This document is" : "These documents are"} only waiting
                because the same invoice from the same supplier, with the same total, came in
                earlier. The earlier one stays. Rejected copies are never booked.
              </p>
              <table className="copies-table">
                <thead>
                  <tr>
                    <th scope="col">Copy</th>
                    <th scope="col">Seller</th>
                    <th scope="col" className="num">
                      Gross total
                    </th>
                    <th scope="col">Received</th>
                    <th scope="col">Copy of</th>
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
                  {rejecting
                    ? "Rejecting…"
                    : copies.length === 1
                      ? "Reject this copy"
                      : `Reject these ${copies.length}`}
                </button>
                <button type="button" className="button" onClick={() => setConfirming(false)}>
                  Cancel
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
          {view === "pending"
            ? "Nothing is waiting for review. Upload an invoice to start."
            : "No documents yet. Upload an invoice to start."}
        </p>
      )}

      {shown.length > 0 && (
        <div className="table-scroll">
          <table className="documents">
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Seller</th>
                <th className="number">Gross total</th>
                <th>Kind</th>
                <th>Result</th>
                <th>Review</th>
                <th>Received</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((document) => (
                <tr key={document.id}>
                  <td>
                    <Link href={`/documents/${document.id}`}>
                      {document.number ?? document.filename ?? "Unnamed document"}
                    </Link>
                  </td>
                  <td>{document.seller_name ?? ""}</td>
                  <td className="number">{money(document.gross_total, document.currency)}</td>
                  <td>{formatLabel(document.format)}</td>
                  <td>{statusLabel(document.status)}</td>
                  <td>
                    <span className={`state state-${document.review_state}`}>
                      {reviewLabel(document.review_state)}
                    </span>
                    {document.findings_to_review > 0 && (
                      <span className="finding-count">
                        {document.findings_to_review}{" "}
                        {document.findings_to_review === 1 ? "finding" : "findings"}
                      </span>
                    )}
                  </td>
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
