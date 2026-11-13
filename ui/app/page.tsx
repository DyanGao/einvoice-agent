"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { listDocuments, uploadDocument, type Summary } from "@/lib/api";
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

  const load = useCallback(async () => {
    try {
      // A document that is still being read is not in the review queue yet,
      // so "all" is always fetched and filtered here.
      const result = await listDocuments();
      setDocuments(result.documents);
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
