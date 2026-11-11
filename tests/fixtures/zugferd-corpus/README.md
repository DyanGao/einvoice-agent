# ZUGFeRD / Factur-X samples — vendored selection

Unmodified files from the [ZUGFeRD corpus](https://github.com/ZUGFeRD/corpus) (Apache License 2.0),
commit `84f8d8f68f6c3698aa3019d701a8b82db9ba0aac`. They are the sample invoices of the FeRD
ZUGFeRD information package ("Lieferant GmbH" / "Kunden AG Mitte"): invented data, real file
structure produced by third-party software. Do not edit them.

They exist so detection, attachment extraction and parsing are tested on PDFs this project
did not generate.

| File | Corpus path | Why it is here |
|---|---|---|
| `EN16931_Einfach.pdf` | `XML-Rechnung/FX/` | baseline EN 16931 profile; full hand-written gold |
| `EN16931_Rabatte.pdf` | `XML-Rechnung/FX/` | allowances, charges and a prepaid amount: totals cannot be recomputed from lines |
| `EN16931_Gutschrift.pdf` | `XML-Rechnung/FX/` | type 389, self-billing — what German usage calls "Gutschrift"; not a credit note (381) |
| `XRECHNUNG_Einfach.pdf` | `XML-Rechnung/FX/` | attachment named `xrechnung.xml`; XRechnung 2.1, older than the validator configuration |
| `zugferd_2p0_EXTENDED_Warenrechnung.pdf` | `ZUGFeRDv2/correct/intarsys/EXTENDED/` | EXTENDED profile, attachment named `zugferd-invoice.xml` |
| `zugferd_2p0_MINIMUM.pdf` | `ZUGFeRDv2/correct/intarsys/MINIMUM/` | MINIMUM profile: type 751, no lines — booking aid, not an invoice |
| `ZUGFeRD_1p0_BASIC_Einfach.pdf` | `ZUGFeRDv1/correct/Intarsys/` | ZUGFeRD 1.0, a different, pre-EN-16931 XML schema |
