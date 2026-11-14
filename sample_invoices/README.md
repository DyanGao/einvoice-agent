# Sample invoices

The five invoices `make demo` processes. **All synthetic**: suppliers, orders, amounts and bank
accounts are invented by `eval/generators/world.py` (seed 20261113), not taken from real invoices.
Regenerate with `uv run python -m eval.generators.demo --out sample_invoices`.

| File | Format | Supplier | Order | Total | Expected |
|---|---|---|---|---|---|
| `01-RE-2026-04319.xml` | XRechnung UBL | Wiesner Drucktechnik e.K. | PO-141246 | 5982.45 EUR | booked without review |
| `02-R26004655.xml` | XRechnung CII | Thalmann Werkzeughandel GmbH | PO-815914 | 4396.27 EUR | booked without review |
| `03-2026-4910.pdf` | ZUGFeRD | Vogelsang Gartenbau GmbH & Co. KG | PO-929675 | 5837.41 EUR | booked without review |
| `04-RE-2026-02775.xml` | XRechnung UBL | Albrecht Catering GmbH | PO-554941 | 22292.03 EUR | booked without review |
| `05-RE-2026-03563.xml` | XRechnung CII | Petersen Werkzeughandel OHG | PO-672261 | 9320.80 EUR | flagged: line 1 priced 12 % above the order |
