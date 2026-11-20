"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listMail, type Mail, type MailPart } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { mailReasonLabel } from "@/lib/labels";

/** One attached file and what became of it. */
function Outcome({ part }: { part: MailPart }) {
  const i18n = useI18n();
  const { t } = i18n;
  const name = part.filename ?? t("mail.noName");
  if (part.outcome === "refused") {
    return (
      <li className="mail-part mail-refused">
        {name}{" "}
        <span className="mail-reason">
          {t("mail.refused", { reason: mailReasonLabel(i18n, part.reason) })}
        </span>
      </li>
    );
  }
  const what = part.outcome === "known" ? t("mail.known") : t("mail.received");
  return (
    <li className="mail-part">
      {part.document_id ? <Link href={`/documents/${part.document_id}`}>{name}</Link> : name}{" "}
      <span className="mail-reason">{what}</span>
    </li>
  );
}

export default function MailPage() {
  const { t, when } = useI18n();
  const [mails, setMails] = useState<Mail[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    listMail()
      .then((answer) => setMails(answer.mails))
      .catch((error: unknown) => {
        if (error instanceof Error) setProblem(error.message);
        else setFailed(true);
      });
  }, []);

  return (
    <div className="queue">
      <h1>{t("mail.title")}</h1>
      <p className="mail-lead">{t("mail.lead")}</p>
      {(problem || failed) && (
        <p className="problem" role="alert">
          {problem ?? t("mail.loadFailed")}
        </p>
      )}
      {mails && mails.length === 0 && <p className="empty">{t("mail.empty")}</p>}
      {mails && mails.length > 0 && (
        <div className="table-scroll">
          <table className="documents">
            <thead>
              <tr>
                <th>{t("mail.col.read")}</th>
                <th>{t("mail.col.from")}</th>
                <th>{t("mail.col.subject")}</th>
                <th>{t("mail.col.attached")}</th>
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
                      <span className="mail-reason">{t("mail.nothingAttached")}</span>
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
