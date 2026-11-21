# German texts of the review UI

Every German text the review UI shows, next to its English, for a native speaker to read through
(v1.0 Stage 4, increment 5). Generated from `ui/lib/messages.ts` by `make german-texts`; CI fails when
this list is out of date or a German text uses a term the glossary rules out. To change a text, change
it in `ui/lib/messages.ts` (texts under `server.` also need `app/texts.py` only if the English changes)
and run `make german-texts`.

`{name}` is filled in when the text is shown: an amount, a date, a field's label, another text.

498 texts.

## Glossary

The accounts-payable terms the German texts use. `make german-texts-check` (run in CI) rejects the
words in the third column.

| Term | English | Not | Why |
| --- | --- | --- | --- |
| Beleg | document | `\bDokument(e\|s\|en)?\b` | the bookkeeping word for any incoming document |
| Eingangsrechnung | incoming invoice | `\bEinkaufsrechnung` | what accounts payable calls the invoices it receives |
| Lieferant | supplier, seller | `Kreditor\|Verkäufer\|Rechnungssteller` | plain for reviewers; Kreditor is the ledger's word |
| Rechnungsempfänger | buyer | `\bKäufer\b\|\bErwerber` | except Käuferreferenz, the XRechnung name of BT-10 |
| Freigabe, freigeben | approval, approve | `[Gg]enehmig` | Rechnungsfreigabe is the usual word |
| buchen, Buchung | book, booking | `[Vv]erbuch` | short and usual in AP |
| Bestellung | purchase order | `\bAuftrag\b\|\bBestellauftrag` | the buyer's side; Auftrag is the seller's |
| Wareneingang | delivery (goods receipt in the ERP) | `\bLieferung(en)?\b` | the record the three-way match compares with |
| Umsatzsteuer, USt | VAT | `Mehrwertsteuer\|\bMwSt\b` | the legal term (UStG) |
| USt-IdNr. | VAT ID | `\bUSt-ID\b\|UStIdNr\|Umsatzsteuer-ID\b` | the official abbreviation |
| Bankverbindung | payment account | `\bBankkonto` | what master data calls it |
| Dublette | duplicate | `Duplikat` | the usual word for a document entered twice |
| Gutschrift | credit note | `\bStornorechnung` | type 381; a cancellation is a different case |
| Firma | company (tenant) | `(?<!Microsoft-)\bMandant` | Mandant only where Microsoft names it so |
| Sie | you | `\b(du\|dich\|dir\|dein\w*)\b` | formal address throughout |

## Contents

- Masthead and pages (17)
- Sign-in (19)
- Queue (incoming invoices) (40)
- Document states (19)
- Review page (79)
- Rejection reasons (4)
- Findings and causes (34)
- History (21)
- Invoice fields (40)
- Mail page (22)
- Admin page (62)
- Written by the server: check (6)
- Written by the server: delivery (18)
- Written by the server: finding (21)
- Written by the server: grounds (3)
- Written by the server: match (19)
- Written by the server: order_note (3)
- Written by the server: party (2)
- Written by the server: reason (11)
- Written by the server: trigger (4)
- Written by the server: vat_id (3)
- Written by the server: api (24)
- Written by the server: members (4)
- Written by the server: review (14)
- Written by the server: role (3)
- Written by the server: credit (6)

## Masthead and pages

| Key | English | Deutsch |
| --- | --- | --- |
| `app.title` | Invoice review | Rechnungsprüfung |
| `nav.pages` | Pages | Seiten |
| `nav.invoices` | Invoices | Rechnungen |
| `nav.mail` | Mail | Post |
| `language.label` | Language | Sprache |
| `reviewer.label` | Reviewing as | Prüfung durch |
| `reviewer.placeholder` | Your name | Ihr Name |
| `reviewer.needName` | Enter your name to save corrections or approve. | Geben Sie Ihren Namen ein, um Korrekturen zu speichern oder freizugeben. |
| `reviewer.tooLong` | The name is too long (100 characters at most). | Der Name ist zu lang (höchstens 100 Zeichen). |
| `reviewer.latinOnly` | Use Latin letters only; this name cannot be sent to the API. | Bitte nur lateinische Buchstaben; dieser Name kann nicht an die API gesendet werden. |
| `reviewer.viewerOnly` | You can read here, but your role (viewer) cannot change anything. | Sie können hier lesen, aber mit Ihrer Rolle (Leser) nichts ändern. |
| `account.signedInAs` | Signed in as | Angemeldet als |
| `account.admin` | Admin | Verwaltung |
| `account.signOut` | Sign out | Abmelden |
| `account.company` | Company | Firma |
| `account.switchFailed` | Could not switch. | Wechsel nicht möglich. |
| `api.unreachable` | The API at {url} is not reachable. Start it with "make api". | Die API unter {url} ist nicht erreichbar. Starten Sie sie mit „make api“. |

## Sign-in

| Key | English | Deutsch |
| --- | --- | --- |
| `signin.title` | Sign in | Anmelden |
| `signin.lead` | to check and approve incoming invoices. | um eingehende Rechnungen zu prüfen und freizugeben. |
| `signin.withProvider` | Sign in with {provider} | Mit {provider} anmelden |
| `signin.withCompanyAccount` | Sign in with your company account | Mit dem Firmenkonto anmelden |
| `signin.orPassword` | or, for the built-in admin account | oder, für das eingebaute Administratorkonto |
| `signin.email` | Email | E-Mail |
| `signin.password` | Password | Passwort |
| `signin.submit` | Sign in with password | Mit Passwort anmelden |
| `signin.busy` | Signing in… | Anmeldung läuft… |
| `signin.wrongPassword` | Wrong email or password. | E-Mail oder Passwort ist falsch. |
| `signin.failedGeneric` | Sign-in did not work. | Die Anmeldung hat nicht funktioniert. |
| `signin.apiUnreachable` | The API cannot be reached. | Die API ist nicht erreichbar. |
| `signin.outcome.unknown_user` | Your account is not set up here yet. Ask an admin to add your email address. | Ihr Konto ist hier noch nicht eingerichtet. Bitten Sie einen Administrator, Ihre E-Mail-Adresse hinzuzufügen. |
| `signin.outcome.unverified_email` | The sign-in service did not confirm your email address, so it cannot be matched to an account here. | Der Anmeldedienst hat Ihre E-Mail-Adresse nicht bestätigt; sie kann daher keinem Konto zugeordnet werden. |
| `signin.outcome.disabled` | Your account here is disabled. Ask an admin. | Ihr Konto ist hier gesperrt. Wenden Sie sich an einen Administrator. |
| `signin.outcome.no_tenant` | Your account is not part of any company here yet. Ask an admin to add you to one. | Ihr Konto gehört hier noch zu keiner Firma. Bitten Sie einen Administrator, Sie hinzuzufügen. |
| `signin.outcome.other_account` | Your email address here is linked to another account at the sign-in service. Sign in with that one, or ask an admin. | Ihre E-Mail-Adresse ist hier mit einem anderen Konto beim Anmeldedienst verknüpft. Melden Sie sich mit diesem an oder wenden Sie sich an einen Administrator. |
| `signin.outcome.failed` | Sign-in did not work. Please try again. | Die Anmeldung hat nicht funktioniert. Bitte versuchen Sie es erneut. |
| `signin.outcome.cancelled` | Sign-in was cancelled. | Die Anmeldung wurde abgebrochen. |

## Queue (incoming invoices)

| Key | English | Deutsch |
| --- | --- | --- |
| `queue.title` | Incoming invoices | Eingangsrechnungen |
| `queue.dropHint` | Drop XRechnung, ZUGFeRD, PDF or scanned invoices here, or choose files. | XRechnung, ZUGFeRD, PDF oder gescannte Rechnungen hier ablegen oder Dateien auswählen. |
| `queue.files` | Invoice files | Rechnungsdateien |
| `queue.choose` | Choose files | Dateien auswählen |
| `queue.uploading` | Uploading… | Wird hochgeladen… |
| `queue.uploaded` | uploaded | hochgeladen |
| `queue.uploadedBefore` | was uploaded before | wurde bereits hochgeladen |
| `queue.notUploaded` | was not uploaded: {reason} | wurde nicht hochgeladen: {reason} |
| `queue.open` | Open | Öffnen |
| `queue.which` | Which documents to show | Welche Belege angezeigt werden |
| `queue.toReview` | To review | Zu prüfen |
| `queue.all` | All documents | Alle Belege |
| `queue.emptyPending` | Nothing is waiting for review. Upload an invoice to start. | Nichts wartet auf Prüfung. Laden Sie eine Rechnung hoch, um zu beginnen. |
| `queue.emptyAll` | No documents yet. Upload an invoice to start. | Noch keine Belege. Laden Sie eine Rechnung hoch, um zu beginnen. |
| `queue.col.invoice` | Invoice | Rechnung |
| `queue.col.seller` | Seller | Lieferant |
| `queue.col.gross` | Gross total | Bruttobetrag |
| `queue.col.kind` | Kind | Art |
| `queue.col.result` | Result | Ergebnis |
| `queue.col.review` | Review | Prüfung |
| `queue.col.booked` | Booked | Gebucht |
| `queue.col.received` | Received | Eingegangen |
| `queue.unnamed` | Unnamed document | Beleg ohne Namen |
| `queue.findings.one` | {count} finding | {count} Befund |
| `queue.findings.other` | {count} findings | {count} Befunde |
| `copies.region` | Duplicates | Dubletten |
| `copies.offer.one` | Reject {count} plain duplicate… | {count} reine Dublette ablehnen… |
| `copies.offer.other` | Reject {count} plain duplicates… | {count} reine Dubletten ablehnen… |
| `copies.explain.one` | This document is only waiting because the same invoice from the same supplier, with the same total, came in earlier. The earlier one stays. Rejected copies are never booked. | Dieser Beleg wartet nur, weil dieselbe Rechnung desselben Lieferanten mit demselben Betrag bereits früher eingegangen ist. Die frühere bleibt bestehen. Abgelehnte Kopien werden nie gebucht. |
| `copies.explain.other` | These documents are only waiting because the same invoice from the same supplier, with the same total, came in earlier. The earlier one stays. Rejected copies are never booked. | Diese Belege warten nur, weil dieselbe Rechnung desselben Lieferanten mit demselben Betrag bereits früher eingegangen ist. Die frühere bleibt jeweils bestehen. Abgelehnte Kopien werden nie gebucht. |
| `copies.col.copy` | Copy | Kopie |
| `copies.col.copyOf` | Copy of | Kopie von |
| `copies.confirm.one` | Reject this copy | Diese Kopie ablehnen |
| `copies.confirm.other` | Reject these {count} | Diese {count} ablehnen |
| `copies.rejecting` | Rejecting… | Wird abgelehnt… |
| `copies.cancel` | Cancel | Abbrechen |
| `copies.done.one` | {count} copy rejected. | {count} Kopie abgelehnt. |
| `copies.done.other` | {count} copies rejected. | {count} Kopien abgelehnt. |
| `copies.doneSkipped.one` | {count} copy rejected; {skipped} changed in the meantime and stay for review. | {count} Kopie abgelehnt; {skipped} hat sich inzwischen geändert und bleibt zur Prüfung. |
| `copies.doneSkipped.other` | {count} copies rejected; {skipped} changed in the meantime and stay for review. | {count} Kopien abgelehnt; {skipped} haben sich inzwischen geändert und bleiben zur Prüfung. |

## Document states

| Key | English | Deutsch |
| --- | --- | --- |
| `format.ubl` | XRechnung (UBL) | XRechnung (UBL) |
| `format.cii` | XRechnung (CII) | XRechnung (CII) |
| `format.zugferd_pdf` | ZUGFeRD PDF | ZUGFeRD-PDF |
| `format.pdf_text` | PDF | PDF |
| `format.image` | Scan | Scan |
| `format.unknown` | Unknown | Unbekannt |
| `status.received` | Waiting to be read | Wartet auf Auslesen |
| `status.valid` | Valid e-invoice | Gültige E-Rechnung |
| `status.invalid` | E-invoice with rule violations | E-Rechnung mit Regelverstößen |
| `status.unrecognized` | E-invoice of an unknown kind | E-Rechnung unbekannter Art |
| `status.parse_failed` | E-invoice that could not be read | E-Rechnung nicht lesbar |
| `status.validation_failed` | Validator did not answer | Validator hat nicht geantwortet |
| `status.extracted` | Read from the page | Aus dem Beleg ausgelesen |
| `status.extraction_failed` | Nothing could be read | Nichts auslesbar |
| `status.unsupported` | Not an invoice format | Kein Rechnungsformat |
| `review.pending` | To review | Zu prüfen |
| `review.approved` | Approved | Freigegeben |
| `review.not_required` | No review needed | Keine Prüfung nötig |
| `review.rejected` | Rejected | Abgelehnt |

## Review page

| Key | English | Deutsch |
| --- | --- | --- |
| `doc.back` | Incoming invoices | Eingangsrechnungen |
| `doc.backFromError` | Back to incoming invoices | Zurück zu den Eingangsrechnungen |
| `doc.loading` | Loading the document… | Beleg wird geladen… |
| `doc.region` | Document | Beleg |
| `doc.original` | Original document | Originalbeleg |
| `doc.noPreview` | This file has no page to show. Structured e-invoices are data, not a picture of an invoice. | Diese Datei hat keine Seite zum Anzeigen. Strukturierte E-Rechnungen sind Daten, kein Bild einer Rechnung. |
| `doc.download` | Download the original file | Originaldatei herunterladen |
| `doc.meta` | {format}, {status}. Received {when}. | {format} · {status} · Eingegangen am {when} |
| `doc.metaFile` | {format}, {status}. Received {when} as {filename}. | {format} · {status} · Eingegangen am {when} als {filename} |
| `doc.stamp.approved` | Approved | Freigegeben |
| `doc.stamp.rejected` | Rejected | Abgelehnt |
| `doc.waiting` | This document is still being read. The page updates by itself. | Dieser Beleg wird noch ausgelesen. Die Seite aktualisiert sich von selbst. |
| `doc.readingStopped` | Reading stopped: | Auslesen abgebrochen: |
| `doc.rejectedNotBooked` | Rejected, so it is not booked: {reason}. | Abgelehnt, daher nicht gebucht: {reason}. |
| `doc.notRequired` | Nothing here needs a person, so it can be booked without review. The fields are shown as read. | Hier muss kein Mensch prüfen; der Beleg kann ohne Prüfung gebucht werden. Die Felder werden wie ausgelesen gezeigt. |
| `doc.whyReview.region` | Why review | Grund der Prüfung |
| `doc.whyReview.title` | Why a person needs to look | Warum ein Mensch prüfen muss |
| `doc.whyReview.seeChecks` | See the checks below. | Siehe die Prüfungen unten. |
| `doc.checks` | Checks | Prüfungen |
| `doc.match.region` | Matching | Abgleich |
| `doc.match.title` | Matched in the ERP | Abgleich mit dem ERP |
| `doc.match.order` | Purchase order | Bestellung |
| `doc.match.delivery` | Delivery | Wareneingang |
| `doc.match.payments` | Payments | Zahlungen |
| `doc.match.none` | None | Keine |
| `doc.match.again` | Match and check again | Erneut abgleichen und prüfen |
| `doc.validator` | Validator findings | Befunde des Validators |
| `doc.payment` | Payment | Zahlung |
| `doc.ibans` | Payment IBANs, one per line | IBANs für die Zahlung, eine pro Zeile |
| `doc.noneRead` | None read. | Keine ausgelesen. |
| `doc.remove` | Remove | Entfernen |
| `doc.removeRow` | Remove {row} {number} | {row} {number} entfernen |
| `doc.save.one` | Save {count} change | {count} Änderung speichern |
| `doc.save.other` | Save {count} changes | {count} Änderungen speichern |
| `doc.saveNone` | Save changes | Änderungen speichern |
| `doc.approve` | Approve | Freigeben |
| `doc.blocked.save` | Save your changes before approving. | Speichern Sie Ihre Änderungen vor der Freigabe. |
| `doc.blocked.fill` | Fill in the invoice fields before approving. | Füllen Sie die Rechnungsfelder vor der Freigabe aus. |
| `doc.blocked.needed` | Still needed: {fields}. | Noch nötig: {fields}. |
| `doc.reject.open` | Reject instead… | Stattdessen ablehnen… |
| `doc.reject.hint` | A rejected document is not booked unless someone reopens it. | Ein abgelehnter Beleg wird nicht gebucht, außer jemand öffnet ihn wieder. |
| `doc.reject.reason` | Reason | Grund |
| `doc.reject.choose` | Choose… | Auswählen… |
| `doc.reject.note` | Note | Notiz |
| `doc.reject.noteOptional` | Note (optional) | Notiz (optional) |
| `doc.reject.submit` | Reject document | Beleg ablehnen |
| `doc.reject.needReason` | Choose why it is rejected. | Wählen Sie, warum er abgelehnt wird. |
| `doc.reject.needNote` | Say which reason. | Geben Sie den Grund an. |
| `doc.reopen.open` | Reopen… | Wieder öffnen… |
| `doc.reopen.hint` | If the rejection was a mistake, the document goes back to review. Nothing is booked until it is approved. | War die Ablehnung ein Fehler, geht der Beleg zurück in die Prüfung. Gebucht wird erst nach der Freigabe. |
| `doc.reopen.why` | Why it is reopened | Warum er wieder geöffnet wird |
| `doc.reopen.submit` | Reopen for review | Zur Prüfung wieder öffnen |
| `doc.history` | History | Verlauf |
| `doc.credit.for` | Credits invoice | Gutschrift zu Rechnung |
| `doc.credit.notFound` | None found | Keine gefunden |
| `doc.credit.credited` | Credited before | Bereits gutgeschrieben |
| `doc.credit.left` | Left to credit | Noch gutzuschreiben |
| `doc.credits` | Credit notes | Gutschriften |
| `hint.sure` | {percent} sure | {percent} sicher |
| `hint.derived` | Filled in by a rule, not read from the document | Durch eine Regel ergänzt, nicht aus dem Beleg ausgelesen |
| `hint.needed` | Needed for approval | Für die Freigabe nötig |
| `hint.nothingRead` | Nothing was read here | Hier wurde nichts ausgelesen |
| `hint.readAs` | Read as {value} | Ausgelesen als {value} |
| `delivery.delivered` | Booked in {target} as no. {ref}. | In {target} gebucht als Nr. {ref}. |
| `delivery.deliveredOn` | Booked in {target} as no. {ref} on {when}. | In {target} gebucht als Nr. {ref} am {when}. |
| `delivery.failed` | Not booked in {target}: {reason}. | Nicht in {target} gebucht: {reason}. |
| `delivery.noReason` | the reason was not recorded | der Grund wurde nicht erfasst |
| `delivery.retrying` | Booking in {target} is tried again (attempt {attempts} failed: {reason}). | Die Buchung in {target} wird erneut versucht (Versuch {attempts} fehlgeschlagen: {reason}). |
| `delivery.waiting` | Waiting to be booked in {target}. | Wartet auf Buchung in {target}. |
| `booking.number` | {where} no. {ref} | {where} Nr. {ref} |
| `booking.booked` | {where}: booked | {where}: gebucht |
| `booking.failed` | {where}: booking failed | {where}: Buchung fehlgeschlagen |
| `booking.waiting` | {where}: waiting | {where}: wartet |
| `connector.erp` | the ERP | das ERP |
| `connector.csv_export` | the booking list | die Buchungsliste |
| `connector.webhook` | the webhook receiver | den Webhook-Empfänger |
| `connectorShort.erp` | ERP | ERP |
| `connectorShort.csv_export` | Booking list | Buchungsliste |
| `connectorShort.webhook` | Webhook | Webhook |

## Rejection reasons

| Key | English | Deutsch |
| --- | --- | --- |
| `reject.duplicate` | Duplicate of an invoice already on file | Dublette einer bereits erfassten Rechnung |
| `reject.not_addressed_to_us` | Not addressed to us | Nicht an uns gerichtet |
| `reject.replaced_or_cancelled` | Replaced or cancelled by the supplier | Vom Lieferanten ersetzt oder storniert |
| `reject.other` | Other reason (say which) | Anderer Grund (bitte angeben) |

## Findings and causes

| Key | English | Deutsch |
| --- | --- | --- |
| `finding.totals_mismatch` | Totals do not add up | Summen gehen nicht auf |
| `finding.vat_rate_implausible` | Unusual VAT rate | Ungewöhnlicher Steuersatz |
| `finding.vat_id_invalid` | Invalid VAT ID | Ungültige USt-IdNr. |
| `finding.credit_note` | Credit note | Gutschrift |
| `finding.unusual_document_type` | Not an ordinary invoice | Keine gewöhnliche Rechnung |
| `finding.duplicate` | Duplicate | Dublette |
| `finding.near_duplicate` | Possible duplicate | Mögliche Dublette |
| `finding.delivery_already_billed` | Delivery already billed | Wareneingang bereits abgerechnet |
| `finding.unknown_supplier` | Unknown supplier | Unbekannter Lieferant |
| `finding.iban_changed` | Bank account not on file | Bankverbindung nicht hinterlegt |
| `finding.missing_po` | No purchase order | Keine Bestellung |
| `finding.po_not_found` | Purchase order not found | Bestellung nicht gefunden |
| `finding.price_deviation` | Price above order | Preis über Bestellung |
| `finding.quantity_deviation` | Quantity above order | Menge über Bestellung |
| `finding.master_data_not_checked` | Not checked against the ERP | Nicht mit dem ERP abgeglichen |
| `finding.po_not_quoted` | Order found, not quoted correctly | Bestellung gefunden, nicht korrekt angegeben |
| `finding.credit_not_linked` | Credited invoice not found | Gutgeschriebene Rechnung nicht gefunden |
| `finding.credit_exceeds_invoice` | Credit above invoice | Gutschrift über Rechnungsbetrag |
| `cause.not_read` | Not validated or read | Nicht validiert oder ausgelesen |
| `cause.missing_fields` | Fields missing | Felder fehlen |
| `cause.assumed_value` | Value assumed | Wert angenommen |
| `cause.printed_not_read` | On the document, not read | Auf dem Beleg, nicht ausgelesen |
| `cause.low_confidence` | Read with low confidence | Mit geringer Sicherheit ausgelesen |
| `cause.findings` | Checks need a person | Prüfung durch eine Person nötig |
| `cause.not_compared` | Not compared with the ERP | Nicht mit dem ERP abgeglichen |
| `cause.order_by_content` | Order not quoted | Bestellung nicht angegeben |
| `cause.not_delivered` | Nothing delivered | Kein Wareneingang |
| `cause.corrected` | Changed by a reviewer | Von einem Prüfer geändert |
| `cause.reader_not_calibrated` | Read by a model not trusted to book alone | Von einem Modell ausgelesen, das nicht allein buchen darf |
| `basis.exact_reference` | quoted exactly | genau angegeben |
| `basis.similar_reference` | quoted with a slip | mit Tippfehler angegeben |
| `basis.content` | found by content | über den Inhalt gefunden |
| `basis.ambiguous` | several fit, none chosen | mehrere passen, keine gewählt |
| `basis.none` | not found | nicht gefunden |

## History

| Key | English | Deutsch |
| --- | --- | --- |
| `audit.field_corrected` | {field} changed from {old} to {new} | {field} geändert von {old} auf {new} |
| `audit.approved` | Approved | Freigegeben |
| `audit.rejected` | Rejected: {reason} | Abgelehnt: {reason} |
| `audit.rejectedNote` | Rejected: {reason} ({note}) | Abgelehnt: {reason} ({note}) |
| `audit.reopened` | Reopened for review: {note} | Zur Prüfung wieder geöffnet: {note} |
| `audit.received` | Received | Eingegangen |
| `audit.received_again` | Received again | Erneut eingegangen |
| `audit.format_detected` | Format detected | Format erkannt |
| `audit.payments_matched` | Payments matched | Zahlungen abgeglichen |
| `audit.queued_again` | Queued again | Erneut eingereiht |
| `audit.status_set` | Status set | Status gesetzt |
| `audit.validated` | Validated | Validiert |
| `audit.parsed` | Parsed | Eingelesen |
| `audit.extracted` | Extracted | Ausgelesen |
| `audit.checked` | Checked | Geprüft |
| `audit.matched` | Matched | Abgeglichen |
| `audit.decided` | Decided | Entschieden |
| `audit.delivered` | Delivered | Übergeben |
| `audit.delivery_failed` | Delivery failed | Übergabe fehlgeschlagen |
| `audit.again` | {event} again: {trigger} | {event} erneut: {trigger} |
| `value.nothing` | nothing | nichts |

## Invoice fields

| Key | English | Deutsch |
| --- | --- | --- |
| `group.invoice` | Invoice | Rechnung |
| `group.seller` | Seller | Lieferant |
| `group.buyer` | Buyer | Rechnungsempfänger |
| `group.totals` | Totals | Summen |
| `field.number` | Invoice number | Rechnungsnummer |
| `field.issue_date` | Invoice date | Rechnungsdatum |
| `field.due_date` | Due date | Fälligkeitsdatum |
| `field.type_code` | Document type code | Rechnungsart (Code) |
| `field.currency` | Currency | Währung |
| `field.buyer_reference` | Buyer reference (Leitweg-ID) | Käuferreferenz (Leitweg-ID) |
| `field.po_ref` | Purchase order | Bestellnummer |
| `field.preceding_ref` | Preceding invoice | Vorausgegangene Rechnung |
| `field.seller.name` | Seller name | Name des Lieferanten |
| `field.seller.vat_id` | Seller VAT ID | USt-IdNr. des Lieferanten |
| `field.buyer.name` | Buyer name | Name des Rechnungsempfängers |
| `field.buyer.vat_id` | Buyer VAT ID | USt-IdNr. des Rechnungsempfängers |
| `field.line_net_total` | Sum of line amounts | Summe der Positionen |
| `field.net_total` | Net total | Nettobetrag |
| `field.vat_total` | VAT total | Umsatzsteuer gesamt |
| `field.gross_total` | Gross total | Bruttobetrag |
| `field.amount_due` | Amount due | Zahlbetrag |
| `field.payment_ibans` | Payment IBANs | IBANs für die Zahlung |
| `field.cell` | {row} {number}, {column} | {row} {number}, {column} |
| `field.row` | {row} {number} | {row} {number} |
| `table.lines` | Line items | Positionen |
| `table.lines.row` | Line | Position |
| `table.lines.add` | Add line | Position hinzufügen |
| `table.vat_breakdown` | VAT breakdown | Umsatzsteueraufschlüsselung |
| `table.vat_breakdown.row` | VAT row | Steuerzeile |
| `table.vat_breakdown.add` | Add VAT row | Steuerzeile hinzufügen |
| `column.line_id` | No. | Pos. |
| `column.description` | Description | Bezeichnung |
| `column.quantity` | Quantity | Menge |
| `column.unit_price` | Unit price | Einzelpreis |
| `column.net` | Net | Netto |
| `column.vat_rate` | VAT % | USt-% |
| `column.category` | Category | Kategorie |
| `column.rate` | Rate % | Satz % |
| `column.taxable_amount` | Taxable amount | Bemessungsgrundlage |
| `column.tax_amount` | Tax | Steuer |

## Mail page

| Key | English | Deutsch |
| --- | --- | --- |
| `mail.title` | Mail | Post |
| `mail.lead` | What arrived in the mailbox, and what became of every file attached. Nothing sent in is dropped without a trace. | Was im Postfach eingegangen ist und was aus jeder angehängten Datei wurde. Nichts Eingesandtes verschwindet spurlos. |
| `mail.empty` | No mail read yet. A mailbox is set up with einvoice tenant-set. | Noch keine Post gelesen. Ein Postfach wird mit einvoice tenant-set eingerichtet. |
| `mail.loadFailed` | The mail could not be loaded. | Die Post konnte nicht geladen werden. |
| `mail.col.read` | Read | Gelesen |
| `mail.col.from` | From | Von |
| `mail.col.subject` | Subject | Betreff |
| `mail.col.attached` | Attached | Anhänge |
| `mail.nothingAttached` | nothing attached | nichts angehängt |
| `mail.noName` | (no name) | (ohne Namen) |
| `mail.received` | received as invoice | als Rechnung eingegangen |
| `mail.known` | already received | bereits eingegangen |
| `mail.refused` | refused: {reason} | abgewiesen: {reason} |
| `mailReason.too_large` | too large | zu groß |
| `mailReason.empty` | empty | leer |
| `mailReason.unsupported_type` | not an invoice format (XML, PDF, PNG, JPEG or a ZIP of them) | kein Rechnungsformat (XML, PDF, PNG, JPEG oder ein ZIP davon) |
| `mailReason.encrypted_pdf` | PDF protected by a password | PDF mit Passwortschutz |
| `mailReason.encrypted_zip` | ZIP protected by a password | ZIP mit Passwortschutz |
| `mailReason.nested_zip` | ZIP inside a ZIP (only one level is opened) | ZIP in einem ZIP (nur eine Ebene wird geöffnet) |
| `mailReason.zip_bomb` | unpacks to far more than any invoice | entpackt weit mehr als jede Rechnung |
| `mailReason.unreadable` | could not be read | nicht lesbar |
| `mailReason.zip_too_many_files` | ZIP with too many files {details} | ZIP mit zu vielen Dateien {details} |

## Admin page

| Key | English | Deutsch |
| --- | --- | --- |
| `admin.title` | Admin · {tenant} | Verwaltung · {tenant} |
| `admin.heading` | Admin | Verwaltung |
| `admin.off` | Sign-in is off (AUTH=off): members and settings are managed with the einvoice command line. | Die Anmeldung ist aus (AUTH=off): Mitglieder und Einstellungen werden über die einvoice-Kommandozeile verwaltet. |
| `admin.needsAdmin` | This page needs the admin role in {tenant}. | Diese Seite braucht die Administratorrolle in {tenant}. |
| `admin.backToInvoices` | Back to the invoices | Zurück zu den Rechnungen |
| `admin.failed` | Something went wrong. | Etwas ist schiefgegangen. |
| `admin.members` | Members | Mitglieder |
| `admin.col.name` | Name | Name |
| `admin.col.email` | Email | E-Mail |
| `admin.col.signsIn` | Signs in | Anmeldung |
| `admin.col.role` | Role | Rolle |
| `admin.col.remove` | Remove | Entfernen |
| `admin.withPassword` | with password | mit Passwort |
| `admin.throughProvider` | through the provider | über den Anmeldedienst |
| `admin.roleOf` | Role of {name} | Rolle von {name} |
| `admin.ownRole` | Another admin can change your role. | Ihre Rolle kann ein anderer Administrator ändern. |
| `admin.remove` | Remove | Entfernen |
| `admin.removeConfirm` | Remove {name} from {tenant}? Their sessions and tokens here stop working at once. | {name} aus {tenant} entfernen? Sitzungen und Tokens dort enden sofort. |
| `admin.removed` | {name} was removed. | {name} wurde entfernt. |
| `admin.roleChanged` | {name} is now {role}. | {name} ist jetzt {role}. |
| `admin.added` | {email} was added. | {email} wurde hinzugefügt. |
| `admin.addForm` | Add a member | Mitglied hinzufügen |
| `admin.add` | Add member | Mitglied hinzufügen |
| `admin.addHint` | They sign in through the identity provider with this email address. | Die Anmeldung erfolgt über den Anmeldedienst mit dieser E-Mail-Adresse. |
| `admin.name` | Name | Name |
| `admin.role` | Role | Rolle |
| `admin.hint.viewer` | reads | liest |
| `admin.hint.reviewer` | corrects, approves, rejects, uploads | korrigiert, gibt frei, lehnt ab, lädt hoch |
| `admin.hint.admin` | everything, and this page | alles, auch diese Seite |
| `admin.settings` | Settings | Einstellungen |
| `admin.save` | Save settings | Einstellungen speichern |
| `admin.saved` | Settings saved. | Einstellungen gespeichert. |
| `admin.operator` | Set by the server's operator | Vom Serverbetreiber festgelegt |
| `admin.operatorHint` | These reach the server's network and files, so they are changed with einvoice tenant-set, not here. | Diese betreffen Netz und Dateien des Servers und werden daher mit einvoice tenant-set geändert, nicht hier. |
| `admin.yes` | yes | ja |
| `admin.no` | no | nein |
| `setting.max_pages` | Pages read automatically, at most | Höchstens automatisch gelesene Seiten |
| `setting.auto_on_order_found_by_content` | Book on an order found by its contents alone | Auf eine nur über den Inhalt gefundene Bestellung buchen |
| `setting.erp_url` | ERP | ERP |
| `setting.erp_timeout_seconds` | ERP timeout (seconds) | ERP-Zeitlimit (Sekunden) |
| `setting.erp_channel` | Booking in the ERP through | Buchung im ERP über |
| `setting.erp_ui_url` | ERP screens | ERP-Oberfläche |
| `setting.browser_allowed_hosts` | Browser may reach | Browser darf erreichen |
| `setting.export_dir` | Booking lists (CSV) folder | Ordner der Buchungslisten (CSV) |
| `setting.webhook_url` | Webhook | Webhook |
| `setting.webhook_secret_env` | Webhook secret, held in | Webhook-Geheimnis, hinterlegt in |
| `setting.imap_host` | Mailbox server | Postfach-Server |
| `setting.imap_port` | Mailbox port | Postfach-Port |
| `setting.imap_tls` | Mailbox over TLS | Postfach über TLS |
| `setting.imap_user` | Mailbox user | Postfach-Benutzer |
| `setting.imap_password_env` | Mailbox password, held in | Postfach-Passwort, hinterlegt in |
| `setting.imap_folder` | Mailbox folder | Postfach-Ordner |
| `setting.imap_processed_folder` | Handled mail moved to | Erledigte Post verschoben nach |
| `setting.imap_auth` | Mailbox sign-in | Postfach-Anmeldung |
| `setting.imap_oauth_tenant` | Microsoft tenant | Microsoft-Mandant |
| `setting.imap_oauth_client_id` | OAuth client ID | OAuth-Client-ID |
| `setting.imap_oauth_secret_env` | OAuth client secret, held in | OAuth-Client-Geheimnis, hinterlegt in |
| `setting.imap_oauth_refresh_env` | OAuth refresh token, held in | OAuth-Refresh-Token, hinterlegt in |
| `setting.imap_oauth_token_url` | OAuth token endpoint | OAuth-Token-Endpunkt |
| `role.viewer` | Viewer | Leser |
| `role.reviewer` | Reviewer | Prüfer |
| `role.admin` | Admin | Administrator |

## Written by the server: check

| Key | English | Deutsch |
| --- | --- | --- |
| `server.check.gross_total` | net total + VAT total = gross total | Nettobetrag + Umsatzsteuer = Bruttobetrag |
| `server.check.line_net` | line {line}: quantity x unit price = net amount | Position {line}: Menge x Einzelpreis = Nettobetrag |
| `server.check.lines_total` | sum of the line amounts = line total | Summe der Positionsbeträge = Positionssumme |
| `server.check.net_total` | sum of the taxable amounts = net total | Summe der Bemessungsgrundlagen = Nettobetrag |
| `server.check.vat_group` | VAT group {group}: rate x taxable amount = tax | Steuergruppe {group}: Satz x Bemessungsgrundlage = Steuer |
| `server.check.vat_total` | sum of the VAT groups = VAT total | Summe der Steuergruppen = Umsatzsteuer gesamt |

## Written by the server: delivery

| Key | English | Deutsch |
| --- | --- | --- |
| `server.delivery.csv_conflict` | {file} line {line} already holds this invoice with other values | {file} Zeile {line} enthält diese Rechnung bereits mit anderen Werten |
| `server.delivery.csv_encoding` | invoice {number}: {characters} cannot be written in Windows-1252, the encoding of the booking list | Rechnung {number}: {characters} kann nicht in Windows-1252, der Kodierung der Buchungsliste, geschrieben werden |
| `server.delivery.csv_not_written` | booking list {file} could not be written: {detail} | Buchungsliste {file} konnte nicht geschrieben werden: {detail} |
| `server.delivery.erp_failed` | ERP failed with {status}: {detail} | ERP-Fehler {status}: {detail} |
| `server.delivery.erp_refused` | ERP refused the booking ({status}): {detail} | das ERP hat die Buchung abgelehnt ({status}): {detail} |
| `server.delivery.erp_unreachable` | ERP not reachable: {detail} | ERP nicht erreichbar: {detail} |
| `server.delivery.form_blocked` | the browser was sent to {url}, which is not on the allowlist; blocked, nothing was booked there | der Browser wurde zu {url} geleitet, das nicht auf der Positivliste steht; blockiert, dort wurde nichts gebucht |
| `server.delivery.form_changed` | the ERP's booking form has changed: {detail}; nothing was booked | das Buchungsformular des ERP hat sich geändert: {detail}; nichts wurde gebucht |
| `server.delivery.form_differs` | booking {booking} in the ERP: {refuse}: {fields_differing} | Buchung {booking} im ERP: {refuse}: {fields_differing} |
| `server.delivery.form_several` | the ERP holds {count} bookings for reference {reference}: {ids} | das ERP enthält {count} Buchungen zur Referenz {reference}: {ids} |
| `server.delivery.form_status` | ERP answered {status} for {url} | das ERP antwortete {status} für {url} |
| `server.delivery.form_unusable` | ERP screens not usable: {detail} | ERP-Oberfläche nicht nutzbar: {detail} |
| `server.delivery.not_an_invoice` | document type {type_code} is not an ordinary invoice; credit notes and other types are booked by hand | Rechnungsart {type_code} ist keine gewöhnliche Rechnung; Gutschriften und andere Arten werden von Hand gebucht |
| `server.delivery.supplier_unknown` | the supplier is not known in the ERP | der Lieferant ist im ERP nicht bekannt |
| `server.delivery.webhook_failed` | webhook receiver failed with {status} | Webhook-Empfänger-Fehler {status} |
| `server.delivery.webhook_redirect` | webhook receiver answered {status} (redirect to {location}); redirects are not followed | Webhook-Empfänger antwortete {status} (Weiterleitung nach {location}); Weiterleitungen werden nicht gefolgt |
| `server.delivery.webhook_refused` | webhook receiver refused the booking with {status} | der Webhook-Empfänger hat die Buchung mit {status} abgelehnt |
| `server.delivery.webhook_unreachable` | webhook receiver not reachable: {detail} | Webhook-Empfänger nicht erreichbar: {detail} |

## Written by the server: finding

| Key | English | Deutsch |
| --- | --- | --- |
| `server.finding.credit_note` | credit note: reduces an earlier invoice and is not booked automatically | Gutschrift: mindert eine frühere Rechnung und wird nicht automatisch gebucht |
| `server.finding.delivery_already_billed` | delivery {delivery} was already billed with invoice {number}, on file | Wareneingang {delivery} wurde bereits mit der erfassten Rechnung {number} abgerechnet |
| `server.finding.duplicate` | invoice {number} from this supplier is already on file | Rechnung {number} dieses Lieferanten ist bereits erfasst |
| `server.finding.iban_changed` | payment account {ibans} is not on file for {supplier}; confirm it with the supplier through a known contact before paying | Bankverbindung {ibans} ist für {supplier} nicht hinterlegt; vor der Zahlung über einen bekannten Kontakt beim Lieferanten bestätigen |
| `server.finding.master_data_none` | supplier, payment account and purchase order were not checked: no ERP is connected | Lieferant, Bankverbindung und Bestellung wurden nicht geprüft: kein ERP angebunden |
| `server.finding.master_data_unavailable` | supplier, payment account and purchase order were not checked: the ERP did not answer ({problem}) | Lieferant, Bankverbindung und Bestellung wurden nicht geprüft: das ERP hat nicht geantwortet ({problem}) |
| `server.finding.missing_po` | the invoice names no purchase order | die Rechnung nennt keine Bestellung |
| `server.finding.near_duplicate_date` | looks like invoice {number} from this supplier, already on file: same amount and date | ähnelt der bereits erfassten Rechnung {number} dieses Lieferanten: gleicher Betrag und gleiches Datum |
| `server.finding.near_duplicate_number` | looks like invoice {number} from this supplier, already on file: same amount and a similar number | ähnelt der bereits erfassten Rechnung {number} dieses Lieferanten: gleicher Betrag und ähnliche Nummer |
| `server.finding.no_po_because` | the invoice names no purchase order; {problem} | die Rechnung nennt keine Bestellung; {problem} |
| `server.finding.po_not_found` | purchase order {po_ref} named on the invoice does not exist | die auf der Rechnung genannte Bestellung {po_ref} existiert nicht |
| `server.finding.po_not_found_because` | purchase order {po_ref} named on the invoice does not exist; {problem} | die auf der Rechnung genannte Bestellung {po_ref} existiert nicht; {problem} |
| `server.finding.price_deviation` | line {line}: price {invoiced_amount} is {above_percent} above the {ordered_amount} on order {order} | Position {line}: Preis {invoiced_amount} liegt {above_percent} über den {ordered_amount} der Bestellung {order} |
| `server.finding.quantity_deviation` | line {line}: quantity {invoiced_amount} is {above_percent} above the {ordered_amount} on order {order} | Position {line}: Menge {invoiced_amount} liegt {above_percent} über den {ordered_amount} der Bestellung {order} |
| `server.finding.totals_mismatch` | {check}: expected {expected_amount}, invoice says {actual_amount} | {check}: erwartet {expected_amount}, auf der Rechnung {actual_amount} |
| `server.finding.unknown_supplier` | no supplier with this VAT ID or name is on file | kein Lieferant mit dieser USt-IdNr. oder diesem Namen hinterlegt |
| `server.finding.unusual_document_type` | document type {type_code} is not an ordinary invoice | Rechnungsart {type_code} ist keine gewöhnliche Rechnung |
| `server.finding.vat_id_invalid` | {party} VAT ID {vat_id}: {problem} | USt-IdNr. {vat_id} ({party}): {problem} |
| `server.finding.vat_rate_category` | VAT category {category} cannot carry a rate of {rate} % | Steuerkategorie {category} kann keinen Satz von {rate} % tragen |
| `server.finding.vat_rate_not_german` | {rate} % is not a German VAT rate | {rate} % ist kein deutscher Umsatzsteuersatz |
| `server.finding.credit_exceeds_invoice` | credit of {credit_amount} is more than the {open_amount} of invoice {number} not yet credited ({credited_amount} credited before) | Gutschrift über {credit_amount} ist mehr als die {open_amount} der Rechnung {number}, die noch nicht gutgeschrieben sind (bereits gutgeschrieben: {credited_amount}) |

## Written by the server: grounds

| Key | English | Deutsch |
| --- | --- | --- |
| `server.grounds.content` | to the supplier's account, without an invoice number | auf das Konto des Lieferanten, ohne Rechnungsnummer |
| `server.grounds.exact_reference` | and quotes the invoice number | und nennt die Rechnungsnummer |
| `server.grounds.similar_reference` | and quotes the invoice number with a slip | und nennt die Rechnungsnummer mit einem Tippfehler |

## Written by the server: match

| Key | English | Deutsch |
| --- | --- | --- |
| `server.match.content_no_reference` | the invoice names no order; delivery {delivery} of order {order} contains exactly the billed items | die Rechnung nennt keine Bestellung; Wareneingang {delivery} zur Bestellung {order} enthält genau die abgerechneten Positionen |
| `server.match.content_unknown_reference` | the invoice names {po_ref}, which is no order of this supplier; delivery {delivery} of order {order} contains exactly the billed items | die Rechnung nennt {po_ref}, keine Bestellung dieses Lieferanten; Wareneingang {delivery} zur Bestellung {order} enthält genau die abgerechneten Positionen |
| `server.match.deliveries_ambiguous` | several deliveries fit equally well: {candidates} | mehrere Wareneingänge passen gleich gut: {candidates} |
| `server.match.delivery_exact` | delivery {delivery} contains exactly the billed items | Wareneingang {delivery} enthält genau die abgerechneten Positionen |
| `server.match.no_order` | no order of this supplier fits the invoice | keine Bestellung dieses Lieferanten passt zur Rechnung |
| `server.match.no_single_delivery` | no single delivery of that order contains exactly the billed items | kein einzelner Wareneingang zu dieser Bestellung enthält genau die abgerechneten Positionen |
| `server.match.no_supplier` | the supplier is not on file, so nothing can be matched | der Lieferant ist nicht hinterlegt, daher kann nichts abgeglichen werden |
| `server.match.no_total` | the invoice has no total to look for | die Rechnung hat keinen Betrag, nach dem gesucht werden kann |
| `server.match.no_transfer` | no transfer to this supplier pays this invoice | keine Überweisung an diesen Lieferanten bezahlt diese Rechnung |
| `server.match.order_named` | order {order} is named on the invoice | Bestellung {order} ist auf der Rechnung genannt |
| `server.match.order_similar_unconfirmed` | the invoice names {po_ref}; order {order} is similar, but no delivery confirms it | die Rechnung nennt {po_ref}; Bestellung {order} ist ähnlich, aber kein Wareneingang bestätigt sie |
| `server.match.order_slip` | the invoice names {po_ref}; order {order} differs by a slip and one of its deliveries contains exactly the billed items | die Rechnung nennt {po_ref}; Bestellung {order} weicht durch einen Tippfehler ab, und einer ihrer Wareneingänge enthält genau die abgerechneten Positionen |
| `server.match.payment_grounds` | {note} {grounds} | {note} {grounds} |
| `server.match.payments_again` | payments matched again; order {order} and delivery {delivery} stay as approved | Zahlungen erneut abgeglichen; Bestellung {order} und Wareneingang {delivery} bleiben wie freigegeben |
| `server.match.transfer_exact` | transfer {transfer} is exactly the invoice total | Überweisung {transfer} entspricht genau dem Rechnungsbetrag |
| `server.match.transfer_shared` | transfer {transfer} of {transfer_amount} equals this invoice's total plus other invoices of the supplier | Überweisung {transfer} über {transfer_amount} entspricht diesem Rechnungsbetrag plus weiteren Rechnungen des Lieferanten |
| `server.match.transfer_twin` | a transfer of the invoice total exists without an invoice number, but invoice {number} of this supplier has the same total | eine Überweisung über den Rechnungsbetrag ohne Rechnungsnummer existiert, aber Rechnung {number} dieses Lieferanten hat denselben Betrag |
| `server.match.transfers_ambiguous` | several transfers fit equally well: {transfers} | mehrere Überweisungen passen gleich gut: {transfers} |
| `server.match.transfers_two` | transfers {first} and {second} add up to the invoice total | Überweisungen {first} und {second} ergeben zusammen den Rechnungsbetrag |

## Written by the server: order_note

| Key | English | Deutsch |
| --- | --- | --- |
| `server.order_note.content` | the invoice names {po_ref}; matched to order {order}, found by the delivered items | die Rechnung nennt {po_ref}; zugeordnet zur Bestellung {order}, gefunden über die gelieferten Positionen |
| `server.order_note.content_unnamed` | the invoice names no purchase order; matched to order {order}, found by the delivered items | die Rechnung nennt keine Bestellung; zugeordnet zur Bestellung {order}, gefunden über die gelieferten Positionen |
| `server.order_note.slip` | the invoice names {po_ref}; matched to order {order}, which differs from it by a slip; a delivery confirms it | die Rechnung nennt {po_ref}; zugeordnet zur Bestellung {order}, die sich durch einen Tippfehler unterscheidet; ein Wareneingang bestätigt sie |

## Written by the server: party

| Key | English | Deutsch |
| --- | --- | --- |
| `server.party.buyer` | buyer | Rechnungsempfänger |
| `server.party.seller` | seller | Lieferant |

## Written by the server: reason

| Key | English | Deutsch |
| --- | --- | --- |
| `server.reason.assumed_value` | not on the document, assumed: {fields} | nicht auf dem Beleg, angenommen: {fields} |
| `server.reason.corrected` | a reviewer changed it; it waits for approval | von einem Prüfer geändert; wartet auf Freigabe |
| `server.reason.findings` | the checks found something a person has to look at: {details} | die Prüfungen haben etwas gefunden, das ein Mensch ansehen muss: {details} |
| `server.reason.low_confidence` | read with less than {threshold_percent} confidence: {fields} | mit weniger als {threshold_percent} Sicherheit ausgelesen: {fields} |
| `server.reason.missing_fields` | mandatory fields are missing or invalid: {fields} | Pflichtfelder fehlen oder sind ungültig: {fields} |
| `server.reason.not_compared` | not compared with the ERP: supplier, bank account and order are unconfirmed | nicht mit dem ERP abgeglichen: Lieferant, Bankverbindung und Bestellung sind unbestätigt |
| `server.reason.not_delivered` | no delivery on order {order} matches what this invoice bills | kein Wareneingang zur Bestellung {order} passt zu dem, was diese Rechnung abrechnet |
| `server.reason.not_read` | the document was not validated or read ({status}) | der Beleg wurde nicht validiert oder ausgelesen ({status}) |
| `server.reason.order_by_content` | the invoice quotes no usable order number; order {order} was found by what it bills, confirm it | die Rechnung nennt keine verwendbare Bestellnummer; Bestellung {order} wurde über die abgerechneten Positionen gefunden, bitte bestätigen |
| `server.reason.printed_not_read` | on the document but not read: {fields} | auf dem Beleg, aber nicht ausgelesen: {fields} |
| `server.reason.reader_not_calibrated` | read by {model}, a model the decision is not calibrated for: a person checks everything it reads | ausgelesen von {model}, einem Modell, für das die Entscheidung nicht kalibriert ist: ein Mensch prüft alles, was es ausliest |

## Written by the server: trigger

| Key | English | Deutsch |
| --- | --- | --- |
| `server.trigger.new_invoice` | new invoice {number} of the supplier | neue Rechnung {number} des Lieferanten |
| `server.trigger.new_transfer` | new bank transaction {transfers} | neue Banktransaktion {transfers} |
| `server.trigger.new_transfers` | new bank transactions {transfers} | neue Banktransaktionen {transfers} |
| `server.trigger.new_transfers_more` | new bank transactions {transfers} and {more} more | neue Banktransaktionen {transfers} und {more} weitere |

## Written by the server: vat_id

| Key | English | Deutsch |
| --- | --- | --- |
| `server.vat_id.eu_shape` | this is not the shape of an EU VAT ID | das ist nicht die Form einer EU-USt-IdNr. |
| `server.vat_id.german_check_digit` | the check digit of this German VAT ID is wrong | die Prüfziffer dieser deutschen USt-IdNr. stimmt nicht |
| `server.vat_id.german_shape` | a German VAT ID is DE followed by nine digits | eine deutsche USt-IdNr. besteht aus DE und neun Ziffern |

## Written by the server: api

| Key | English | Deutsch |
| --- | --- | --- |
| `server.api.csrf_header` | a change made with the session cookie needs {header} | eine Änderung mit dem Sitzungscookie braucht den Header {header} |
| `server.api.document_not_found` | document not found | Beleg nicht gefunden |
| `server.api.empty_file` | empty file | leere Datei |
| `server.api.file_too_large` | file larger than {bytes} bytes | Datei größer als {bytes} Bytes |
| `server.api.invalid_settings` | settings that cannot work: {detail} | Einstellungen, die nicht funktionieren können: {detail} |
| `server.api.member_already` | {email} is a member already | {email} ist bereits Mitglied |
| `server.api.member_refused` | {detail} | {detail} |
| `server.api.members_cli` | sign-in is off (AUTH=off): manage members with the einvoice CLI | die Anmeldung ist aus (AUTH=off): Mitglieder werden mit der einvoice-Kommandozeile verwaltet |
| `server.api.needs_role` | needs the {role} role | braucht die Rolle {role} |
| `server.api.no_checks` | no checks are configured for this installation | für diese Installation sind keine Prüfungen eingerichtet |
| `server.api.no_provider` | no identity provider is configured (OIDC_ISSUER) | kein Anmeldedienst eingerichtet (OIDC_ISSUER) |
| `server.api.no_such_tenant` | no such tenant among yours | keine solche Firma unter Ihren |
| `server.api.not_queued` | document stored but not queued ({detail}); run `einvoice requeue` | Beleg gespeichert, aber nicht eingereiht ({detail}); `einvoice requeue` ausführen |
| `server.api.not_signed_in` | not signed in | nicht angemeldet |
| `server.api.nothing_to_check` | nothing was read from this document, so nothing to check | aus diesem Beleg wurde nichts ausgelesen, daher nichts zu prüfen |
| `server.api.open_mode_localhost` | sign-in is off (AUTH=off): only requests to localhost are answered | die Anmeldung ist aus (AUTH=off): nur Anfragen an localhost werden beantwortet |
| `server.api.operator_settings` | {settings}: set by the installation's operator (einvoice tenant-set) | {settings}: vom Serverbetreiber festgelegt (einvoice tenant-set) |
| `server.api.provider_unreachable` | the identity provider cannot be reached | der Anmeldedienst ist nicht erreichbar |
| `server.api.reviewer_name_required` | X-Reviewer header with the reviewer's name is required | der X-Reviewer-Header mit dem Namen des Prüfers fehlt |
| `server.api.sign_in` | sign in, or send an API token | bitte anmelden oder ein API-Token senden |
| `server.api.sign_in_off` | sign-in is off (AUTH=off) | die Anmeldung ist aus (AUTH=off) |
| `server.api.token_cannot_switch` | an API token acts in the tenant it was made for | ein API-Token handelt für die Firma, für die es erstellt wurde |
| `server.api.too_many_sign_ins` | too many failed sign-ins; try again in {minutes} minute(s) | zu viele fehlgeschlagene Anmeldungen; bitte in {minutes} Minute(n) erneut versuchen |
| `server.api.wrong_password` | wrong email or password | E-Mail oder Passwort ist falsch |

## Written by the server: members

| Key | English | Deutsch |
| --- | --- | --- |
| `server.members.last_admin` | the tenant would be left without an admin | die Firma hätte dann keinen Administrator mehr |
| `server.members.not_member` | not a member of this tenant | kein Mitglied dieser Firma |
| `server.members.own_admin` | you cannot take away your own admin role | Sie können sich die Administratorrolle nicht selbst entziehen |
| `server.members.self_remove` | you cannot remove yourself | Sie können sich nicht selbst entfernen |

## Written by the server: review

| Key | English | Deutsch |
| --- | --- | --- |
| `server.review.already_approved` | document is already approved | der Beleg ist bereits freigegeben |
| `server.review.fields_missing` | cannot approve, fields missing or invalid: {fields} | Freigabe nicht möglich, Felder fehlen oder sind ungültig: {fields} |
| `server.review.invalid_value` | invalid value: {problems} | ungültiger Wert: {problems} |
| `server.review.no_draft` | document has no draft to approve | der Beleg hat keinen Entwurf zum Freigeben |
| `server.review.not_editable` | field cannot be edited: {path} | Feld kann nicht bearbeitet werden: {path} |
| `server.review.not_processed` | document has not been processed yet | der Beleg ist noch nicht verarbeitet |
| `server.review.not_rejected` | document is not rejected | der Beleg ist nicht abgelehnt |
| `server.review.note_needed` | a rejection for another reason needs a note saying which | eine Ablehnung aus anderem Grund braucht eine Notiz, welcher |
| `server.review.rejected` | document was rejected | der Beleg wurde abgelehnt |
| `server.review.reopen_note` | reopening needs a note saying why | zum Wiederöffnen ist eine Notiz mit dem Grund nötig |
| `server.review.copy_of` | copy of invoice {number}, document {document} | Kopie der Rechnung {number}, Beleg {document} |
| `server.review.copy_of_unnumbered` | copy of invoice (no number), document {document} | Kopie einer Rechnung ohne Nummer, Beleg {document} |
| `server.review.unknown_field` | unknown field: {path} | unbekanntes Feld: {path} |
| `server.review.unknown_reason` | unknown reason {reason}; one of: {allowed} | unbekannter Grund {reason}; einer von: {allowed} |

## Written by the server: role

| Key | English | Deutsch |
| --- | --- | --- |
| `server.role.admin` | admin | Administrator |
| `server.role.reviewer` | reviewer | Prüfer |
| `server.role.viewer` | viewer | Leser |

## Written by the server: credit

| Key | English | Deutsch |
| --- | --- | --- |
| `server.credit.ambiguous` | several invoices of this supplier fit this credit note: {numbers} | mehrere Rechnungen dieses Lieferanten passen zu dieser Gutschrift: {numbers} |
| `server.credit.by_lines` | credit note for invoice {number}: it names none, found by supplier and the lines it credits | Gutschrift zur Rechnung {number}: sie nennt keine, gefunden über Lieferant und die gutgeschriebenen Positionen |
| `server.credit.by_total` | credit note for invoice {number}: it names none, found by supplier and the same total | Gutschrift zur Rechnung {number}: sie nennt keine, gefunden über Lieferant und denselben Betrag |
| `server.credit.not_found` | no invoice of this supplier on file fits this credit note | keine erfasste Rechnung dieses Lieferanten passt zu dieser Gutschrift |
| `server.credit.reference` | credit note for invoice {number}, which it names | Gutschrift zur Rechnung {number}, die sie nennt |
| `server.credit.reference_not_found` | the credit note names invoice {number}, which is not on file for this supplier | die Gutschrift nennt Rechnung {number}, die für diesen Lieferanten nicht erfasst ist |
