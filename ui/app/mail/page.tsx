"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listMail, type Mail, type MailPart } from "@/lib/api";
import { mailReasonLabel, when } from "@/lib/labels";

/** One attached file and what became of it. */
function Outcome({ part }: { part: MailPart }) {
  const name = part.filename ?? "(no name)";
  if (part.outcome === "refused") {
    return (
      <li className="mail-part mail-refused">
        {name} <span className="mail-reason">refused: {mailReasonLabel(part.reason)}</span>
      </li>
    );
  }
  const what = part.outcome === "known" ? "already received" : "received as invoice";
  return (
    <li className="mail-part">
      {part.document_id ? <Link href={`/documents/${part.document_id}`}>{name}</Link> : name}{" "}
      <span className="mail-reason">{what}</span>
    </li>
  );
}

export default function MailPage() {
  const [mails, setMails] = useState<Mail[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    listMail()
      .then((answer) => setMails(answer.mails))
      .catch((error: unknown) =>
        setProblem(error instanceof Error ? error.message : "The mail could not be loaded."),
      );
  }, []);

  return (
    <div className="queue">
      <h1>Mail</h1>
      <p className="mail-lead">
        What arrived in the mailbox, and what became of every file attached. Nothing sent in is
        dropped without a trace.
      </p>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
      {mails && mails.length === 0 && (
        <p className="empty">No mail read yet. A mailbox is set up with einvoice tenant-set.</p>
      )}
      {mails && mails.length > 0 && (
        <div className="table-scroll">
          <table className="documents">
            <thead>
              <tr>
                <th>Read</th>
                <th>From</th>
                <th>Subject</th>
                <th>Attached</th>
              </tr>
            </thead>
            <tbody>
              {mails.map((mail) => (
                <tr key={mail.id}>
                  <td>{when(mail.read_at)}</td>
                  <td>{mail.sender || "–"}</td>
                  <td>{mail.subject || "–"}</td>
                  <td>
                    {mail.parts.length === 0 ? (
                      <span className="mail-reason">nothing attached</span>
                    ) : (
                      <ul className="mail-parts">
                        {mail.parts.map((part, index) => (
                          <Outcome key={index} part={part} />
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
