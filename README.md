# einvoice-agent

> An agent that turns incoming German invoices (XRechnung, ZUGFeRD, PDF, scans) into booked, matched and checked entries — and only asks a human when it's unsure.

<!-- badges: CI | license | eval score -->

Since 2025 every German business must be able to receive e-invoices; issuing becomes mandatory in 2027 (turnover > €800k) and 2028 (all). Structured XML makes *reading* invoices easy — the remaining work is **matching** them to purchase orders, delivery notes and bank transactions, catching **exceptions** (wrong VAT, price deviations, duplicates), handling **non-structured invoices** from foreign suppliers, and getting the result into **systems without an API**.

**Why this one:** structured-first pipeline (validate XML before any LLM), confidence-based human review, browser-automation fallback for legacy systems, and an eval on a labeled document set.

## Demo
<!-- GIF: drop 5 invoices → 4 auto-matched, 1 flagged (price +12% vs PO) → reviewer approves → entry appears in mock ERP -->

## Results (fill in from eval/)
| Metric | Rule-based baseline | **einvoice-agent** |
|---|---|---|
| Field accuracy (PDF/scan) | TBD | **TBD** |
| PO / bank match accuracy | TBD | **TBD** |
| Exception detection F1 | TBD | **TBD** |
| Auto-processed at ≤1% error | TBD | **TBD** |
| Cost / document | – | **TBD** |

## Features
- [ ] Ingest: email inbox (IMAP), folder watch, upload UI, API
- [x] Format detection: XRechnung (UBL/CII), ZUGFeRD/Factur-X (PDF/A-3 with embedded XML), plain PDF, scan
- [x] Structured path: parse + validate (KoSIT validator / Mustang) — no LLM needed _(KoSIT only so far: ZUGFeRD profiles outside EN 16931 and PDF/A-3 conformance are not validated)_
- [ ] Unstructured path: OCR + vision LLM → schema-constrained extraction (EN 16931 fields)
- [ ] Matching agent: invoice ↔ PO ↔ delivery note ↔ bank transaction (3-way match + fuzzy)
- [ ] Checks: VAT rate/ID plausibility, totals, duplicates, IBAN changes (fraud signal), price/quantity deviations
- [ ] Human-in-the-loop review UI for low-confidence fields and exceptions
- [ ] Output connectors: CSV/DATEV-style export, REST webhook, mock ERP via API, Playwright fallback for UI-only systems
- [ ] Audit trail per document (source, extracted values, who approved)

## Quickstart
```bash
docker compose up -d          # api, worker, postgres, ui, mock-erp, validator
make seed                     # synthetic suppliers, POs, bank transactions
make demo                     # processes sample_invoices/
open http://localhost:3000
```

## Architecture
See [ARCHITECTURE.md](ARCHITECTURE.md).

## Data & privacy
- Repo contains **only synthetic or fully anonymized** invoices
- Local-model mode for on-prem use; no document leaves the machine
- Not tax advice; archiving rules (GoBD) are out of scope for v1 and documented as such

## Roadmap
- [x] v0.1 structured path: parse + validate XRechnung/ZUGFeRD
- [ ] v0.2 PDF/scan extraction + review UI
- [ ] v0.3 matching agent + checks
- [ ] v0.4 connectors (export, mock ERP, Playwright fallback)
- [ ] v0.5 eval report published
- [ ] v1.0 multi-tenant, IMAP ingest, German/English UI

## Tech stack
Python (FastAPI, Pydantic, lxml) · KoSIT validator / Mustang (Java, via container) · factur-x / pypdf · OCR (Tesseract or docTR) + vision LLM · Postgres · Celery/RQ · Playwright · Next.js review UI · Docker Compose · pytest

## References
- German e-invoicing mandate (Wachstumschancengesetz, §14 UStG); EN 16931
- GitHub trending: vercel-labs/agent-desktop (computer-use CLI), D4Vinci/Scrapling
- Papers: "WebHarbor: Docking Real Websites for Evolving GUI Agent Environments" (HF 2611.03634); "Do We Need an LLM to Know Why Sources Conflict? Knowledge-Conflict Typing in RAG" (HF 2610.36915)

## License
Apache-2.0
