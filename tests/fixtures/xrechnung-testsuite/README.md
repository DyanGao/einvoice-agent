# XRechnung test suite — vendored selection

Unmodified files from the official [XRechnung Test Suite](https://github.com/itplr-kosit/xrechnung-testsuite),
release `v2026-08-31` (XRechnung 3.0.2), published by KoSIT under the Apache License 2.0.

They exist so the parsers are tested against invoices this project did not write.
All content is synthetic sample data from the suite. Do not edit these files; to
test a variation, build it in the test from one of them.

| Pair | Why it is here |
|---|---|
| 01.01a | baseline; full hand-written gold |
| 01.02a | UBL carries a placeholder order reference the CII twin lacks |
| 01.05a | two payment accounts |
| 01.07a | due date, buyer VAT ID |
| 01.14a | purchase order reference |
| 01.18a | type code 384 (corrected invoice) |
| 01.19a | no payment account |
| 01.20a | type code 389 (self-billed), purchase order reference |
| 03.06a | two VAT categories, negative line, gross vs. net price; full hand-written gold |
| 01.06 minimal | only mandatory fields |
