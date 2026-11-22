# Verfahrensdokumentation – Eingangsrechnungsverarbeitung mit einvoice-agent

> **Vorlage.** Dieses Dokument beschreibt, was die Software einvoice-agent tut. Was nur der Betreiber
> wissen kann (Unternehmen, Zuständigkeiten, Systemumgebung, Abläufe außerhalb der Software), ist mit
> **[Betreiber: …]** markiert und von ihm zu ergänzen. Die fertige Verfahrensdokumentation ist Sache
> des Unternehmens; sie ist mit der steuerlichen Beratung abzustimmen. Die Software ist nicht
> zertifiziert, und dieses Dokument ist keine steuerliche Beratung.
>
> Gegliedert nach den GoBD (BMF-Schreiben vom 28. November 2019 in der jeweils geltenden Fassung):
> allgemeine Beschreibung, Anwenderdokumentation, technische Systemdokumentation,
> Betriebsdokumentation; dazu das interne Kontrollsystem, Unveränderbarkeit, Aufbewahrung und
> Datenzugriff.

| | |
|---|---|
| Unternehmen (Mandant) | [Betreiber: Firma, Anschrift, Steuernummer] |
| Verantwortlich für das Verfahren | [Betreiber: Name, Funktion] |
| Version der Software | [Betreiber: Git-Version bzw. Release, z. B. `git describe`] |
| Gültig ab | [Betreiber: Datum] |
| Stand dieses Dokuments | [Betreiber: Datum, Änderungshistorie siehe Abschnitt 9] |

## 1. Allgemeine Beschreibung

### 1.1 Zweck

einvoice-agent nimmt Eingangsrechnungen und Gutschriften entgegen, liest sie aus, prüft sie gegen die
Stammdaten, Bestellungen, Wareneingänge und Zahlungen im ERP-System, entscheidet, ob eine Person sie
prüfen muss, und übergibt freigegebene Belege zur Buchung. Die Buchung selbst erfolgt im ERP-System
bzw. in der Finanzbuchhaltung, nicht in einvoice-agent.

[Betreiber: welche Belege über einvoice-agent laufen (alle Eingangsrechnungen, nur bestimmte
Gesellschaften, Kostenstellen …), welche nicht, und wie diese anders verarbeitet werden.]

### 1.2 Überblick über den Ablauf

1. **Eingang**: Upload in der Prüfoberfläche oder über die Schnittstelle (API), ein überwachter
   Ordner oder ein E-Mail-Postfach (IMAP).
2. **Speicherung des Originals**: unverändert, mit SHA-256-Prüfsumme, verschlüsselt.
3. **Auslesen**: E-Rechnungen (XRechnung, ZUGFeRD) werden geprüft (KoSIT-Validator) und ohne KI
   eingelesen; PDFs und Scans werden ausgelesen (Sprachmodell oder regelbasiert).
4. **Abgleich und Prüfungen**: Lieferant, Bestellung, Wareneingang, Zahlung; Rechenprüfung,
   Umsatzsteuer, Dubletten, geänderte Bankverbindung, Preisabweichung, Gutschrift zur Rechnung.
5. **Entscheidung**: automatisch zur Buchung, oder zur Prüfung durch eine Person.
6. **Prüfung durch eine Person**: Korrigieren, Freigeben, Ablehnen, Wiederöffnen.
7. **Übergabe zur Buchung**: an das ERP-System, als Buchungsliste (CSV) oder per Webhook, je Beleg
   und Ziel genau einmal.
8. **Aufbewahrung, Löschung, Datenzugriff**: siehe Abschnitte 6 bis 8.

### 1.3 Beteiligte Systeme

| System | Aufgabe | [Betreiber: Produkt, Version, Ort] |
|---|---|---|
| einvoice-agent (API, Worker, Prüfoberfläche) | Verarbeitung, Prüfung, Übergabe | |
| PostgreSQL | Datenbank (Belege verschlüsselt) | |
| Redis | Warteschlange der Verarbeitung | |
| KoSIT-Validator | Prüfung von XRechnung/ZUGFeRD | |
| ERP-System | Stammdaten, Bestellungen, Zahlungen; Buchung | |
| Sprachmodell (Anthropic API) | Auslesen von PDFs und Scans, sofern eingeschaltet | |
| Anmeldedienst (OIDC) | Anmeldung der Benutzer | |

## 2. Anwenderdokumentation

### 2.1 Rollen und Berechtigungen

Jede Anfrage stammt von einer angemeldeten Person oder einem API-Token; jede Handlung wird mit der
verifizierten Benutzerkennung protokolliert.

| Rolle | Darf |
|---|---|
| viewer (Leser) | Belege und ihr Protokoll ansehen |
| reviewer (Prüfer) | zusätzlich hochladen, korrigieren, freigeben, ablehnen, wieder öffnen |
| admin | zusätzlich Benutzer, Tokens und die Einstellungen des Mandanten |

Betreiber-Einstellungen (ERP-Anbindung, Ordner, Postfach, Aufbewahrung, Löschung) werden nur auf der
Kommandozeile gesetzt (`einvoice tenant-set`), nicht in der Oberfläche.

[Betreiber: wer welche Rolle hat; Vergabe und Entzug von Berechtigungen; Vertretung.]

### 2.2 Prüfung durch eine Person

Ein Beleg geht zur Prüfung, wenn eine Prüfung etwas gefunden hat (z. B. Abweichung, Dublette,
geänderte Bankverbindung), wenn ein Feld nicht sicher genug gelesen wurde, wenn die Belegart weder
Rechnung noch Gutschrift ist, und immer bei Gutschriften. Die Oberfläche zeigt den Originalbeleg neben
den gelesenen Feldern.

- **Korrigieren**: jede Änderung wird mit altem und neuem Wert und der Person protokolliert. Ein
  korrigierter Beleg wird nie automatisch gebucht.
- **Freigeben**: nur, wenn die für eine Buchung nötigen Felder vorhanden sind.
- **Ablehnen**: mit Grund (und optional Anmerkung); ein abgelehnter Beleg wird nicht übergeben.
- **Wieder öffnen**: nur mit Anmerkung.

[Betreiber: Prüfanweisungen, Freigabegrenzen, Vier-Augen-Prinzip, Fristen.]

### 2.3 Gutschriften

Eine Gutschrift (Belegart 381) wird mit positiven Beträgen gespeichert und der Rechnung zugeordnet, die
sie korrigiert (über die angegebene Rechnungsnummer, sonst über Lieferant, Betrag und Positionen). Sie
wird nach Freigabe als Gutschrift übergeben (Belegart, Soll/Haben, Bezugsrechnung), nie als Rechnung.

## 3. Technische Systemdokumentation

### 3.1 Datenhaltung

- **Originale**: jeder eingegangene Beleg wird unverändert gespeichert; seine SHA-256-Prüfsumme wird
  beim Eingang festgehalten. Dieselbe Datei wird je Mandant nur einmal gespeichert (Dublette).
- **Verschlüsselung**: Originale und alle Rechnungsdaten (Felder, Positionen, Prüfergebnisse,
  Protokollangaben, Buchungssätze, Mail-Angaben) werden mit AES-256-GCM verschlüsselt, mit einem
  Datenschlüssel je Mandant. Die Datenschlüssel sind mit einem Hauptschlüssel verschlüsselt, der nicht
  in der Datenbank liegt (`MASTER_KEY`). Schlüsselwechsel: `einvoice key-rotate-master`,
  `einvoice key-rotate-tenant`.
- **Mandantentrennung**: jede Datenbankabfrage gilt nur für den Mandanten der angemeldeten Person;
  in PostgreSQL zusätzlich durch Zeilensicherheit (Row-Level Security) erzwungen.

[Betreiber: Standort und Betrieb der Datenbank; wo der Hauptschlüssel verwahrt wird und wer Zugang
hat.]

### 3.2 Schnittstellen

| Schnittstelle | Richtung | Inhalt |
|---|---|---|
| Upload/API, Ordner, Postfach | ein | Belegdateien (XML, PDF, Bild) |
| ERP-Lese-API | ein | Lieferanten, Bestellungen, Wareneingänge, Banktransaktionen |
| ERP-Buchungs-API oder -Masken | aus | Buchungssatz je Beleg |
| CSV-Buchungsliste (DATEV-ähnlich) | aus | eine Datei je Monat |
| Webhook (HMAC-signiert) | aus | Buchungssatz je Beleg |
| Sprachmodell | aus/ein | Bild des Belegs; zurück die gelesenen Felder |

Jede Übergabe erfolgt je Beleg und Ziel genau einmal (Idempotenzschlüssel); fehlgeschlagene
Übergaben werden wiederholt und, wenn sie endgültig scheitern, in der Oberfläche gezeigt.

[Betreiber: welche Schnittstellen in Betrieb sind, mit welchem Ziel.]

### 3.3 Automatische Entscheidung

Ein Beleg geht zur Prüfung durch eine Person, sobald einer dieser Gründe vorliegt; nur ohne jeden
Grund wird er ohne Person zur Buchung gegeben:

- er konnte nicht gelesen werden, oder für die Buchung nötige Felder fehlen;
- ein Wert wurde angenommen statt gelesen (z. B. die Währung), oder etwas Gedrucktes wurde nicht
  gelesen;
- ein Feld wurde nicht sicher genug gelesen, oder das Auslesen ist für die automatische Buchung nicht
  freigegeben (nicht kalibriert);
- eine Prüfung hat etwas gefunden;
- er konnte nicht mit dem ERP-System abgeglichen werden;
- die Bestellung wurde nur über den Inhalt gefunden (sofern nicht anders eingestellt), oder es gibt
  keinen Wareneingang;
- er wurde korrigiert.

Die Schwellen sind auf Testdaten bestimmt und versioniert; jede Entscheidung wird mit ihren Gründen und
der Version der Schwellen gespeichert.

[Betreiber: ob die automatische Buchung genutzt wird; Stichprobenkontrollen.]

## 4. Betriebsdokumentation

- **Installation und Aktualisierung**: Container (Docker Compose) oder aus dem Quelltext;
  Datenbankänderungen nur über versionierte Migrationen.
- **Überwachung der Verarbeitung**: hängengebliebene Belege werden erneut eingereiht; fehlgeschlagene
  Übergaben und nicht lesbare Belege erscheinen in der Oberfläche.
- **Prüfung der Unveränderbarkeit**: `einvoice verify` (Abschnitt 5) regelmäßig ausführen und das
  Ergebnis samt Digest außerhalb der Datenbank ablegen.

[Betreiber: wer den Betrieb verantwortet; Datensicherung (Umfang, Häufigkeit, Aufbewahrung,
Wiederherstellungstest) – die Sicherung enthält die verschlüsselten Daten, der Hauptschlüssel ist
getrennt zu sichern; Notfallplan; Änderungsverfahren für Software und Konfiguration.]

## 5. Unveränderbarkeit und Protokoll

- Jedes Ereignis eines Belegs (Eingang, Auslesen, Prüfung, Korrektur, Freigabe, Ablehnung, Übergabe,
  Löschsperre …) wird mit Zeitpunkt, Handelndem und Angaben protokolliert. Das Protokoll wird nur
  ergänzt, nie geändert.
- Das Protokoll jedes Belegs ist eine Hash-Kette: jeder Eintrag enthält die SHA-256-Prüfsumme des
  vorigen, der erste die des Originals; der Beleg hält den neuesten Wert.
- `einvoice verify` prüft jedes Original gegen seine Prüfsumme und jede Kette, und meldet jede
  Abweichung: geändertes oder unlesbares Original, geänderter, entfernter oder nachträglich
  eingefügter Eintrag, gekürztes Protokoll.
- Grenze: wer die Datenbank selbst schreiben kann, kann auch eine neue, in sich stimmige Kette
  erzeugen. Dagegen hilft der außerhalb abgelegte Digest bzw. der Datenexport (Abschnitt 8).

## 6. Internes Kontrollsystem (Kontrollen in der Software)

| Kontrolle | Wirkung |
|---|---|
| Prüfung der E-Rechnung (KoSIT) | ungültige E-Rechnungen gehen zur Prüfung |
| Rechenprüfung, Umsatzsteuer, USt-IdNr. | Abweichungen gehen zur Prüfung |
| Dubletten (gleiche Datei, Nummer, Inhalt) | gehen zur Prüfung, werden nicht doppelt gebucht |
| Geänderte Bankverbindung | geht immer zur Prüfung |
| Abgleich Bestellung/Wareneingang/Zahlung | Preis- und Mengenabweichungen gehen zur Prüfung |
| Gutschrift größer als die Rechnung | geht zur Prüfung |
| Berechtigungen nach Rolle | nur Prüfer und Admins ändern Belege |
| Korrigierte Belege | nie automatisch gebucht |
| Übergabe genau einmal | keine doppelten Buchungssätze |

[Betreiber: Kontrollen außerhalb der Software (Freigabe von Zahlungen, Abstimmung mit der
Buchhaltung, Stichproben).]

## 7. Aufbewahrung und Löschung

- **Frist**: je Beleg 8 Jahre (§ 147 AO in der seit 2025 geltenden Fassung für Buchungsbelege) nach
  Ende des spätesten der Jahre, in dem der Beleg datiert, eingegangen bzw. freigegeben oder übergeben
  ist. Einstellbar je Mandant (`retention_years`), nie unter 8 Jahre.
  [Betreiber/steuerliche Beratung: Frist bestätigen; längere Fristen für andere Unterlagen.]
- **Löschsperre**: je Beleg (`einvoice hold`, mit Grund im Protokoll) oder für den ganzen Mandanten
  (`legal_hold`), z. B. während einer Außenprüfung oder eines Rechtsstreits.
- **Löschung**: nie von selbst. `einvoice retention` zeigt, was löschbar ist; gelöscht wird nur mit
  `--apply` und nur, wenn für den Mandanten die Löschung eingeschaltet ist (`deletion_enabled`). Jede
  Löschung wird im nur ergänzbaren Löschprotokoll festgehalten (Beleg, Prüfsumme des Originals, Kopf
  und Länge seines Protokolls, Fristende, Zeitpunkt, wer).

[Betreiber: wer Löschungen veranlasst und wann; wie mit Sicherungskopien abgelaufener Belege
verfahren wird.]

## 8. Datenzugriff der Finanzverwaltung

- **Unmittelbarer Zugriff (Z1)** und **mittelbarer Zugriff (Z2)**: ein Lesekonto mit der Rolle viewer
  für den Prüfer.
  [Betreiber: Bereitstellung, Einweisung, Zeitraum.]
- **Datenträgerüberlassung (Z3)**: `einvoice export ORDNER --from … --to … --tenant …` schreibt die
  Belege eines Zeitraums: Originale, Tabellen der Belege, Positionen, Protokolle und Löschungen (CSV),
  eine Beschreibung nach dem GDPdU-Beschreibungsstandard (`index.xml`), ein Manifest mit dem Ergebnis
  der Prüfung der Unveränderbarkeit und Prüfsummen aller Dateien (`checksums.sha256`). Der Export ist
  unverschlüsselt: auf einem verschlüsselten Datenträger übergeben und danach löschen.
  [Betreiber: Übergabe, Datenträger; die Beschreibung ist nicht mit Prüfsoftware (IDEA) getestet.]

## 9. Änderungshistorie

| Datum | Version | Änderung | Wer |
|---|---|---|---|
| [Betreiber] | | Ersterstellung aus der Vorlage | |
