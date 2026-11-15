"""Record the README's demo GIF from the running application (`make demo-gif`).

Drives the real review UI and the mock ERP's booking screens through the
README's story with a browser: the five files in sample_invoices/ are dropped
on the review UI, four are booked without review, one is flagged for its
price, a person approves it, and the mock ERP holds five bookings. Each step
is a screenshot with a caption; Pillow joins them into docs/demo.gif.

Needs the whole application on an empty database (`make stack` on a fresh
project): it refuses to run when documents are already stored, so it never
adds invoices to a database in use.

    uv run python deploy/demo_gif.py [--ui URL] [--api URL] [--erp URL] [--out PATH]
"""

import argparse
import io
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Page, expect, sync_playwright

SAMPLES = sorted(Path("sample_invoices").glob("0*"))
FLAGGED = "RE-2026-03563"
REVIEWER = "Demo Reviewer"
VIEWPORT = {"width": 1280, "height": 800}
WIDTH = 960  # of the GIF
CAPTION_HEIGHT = 56
BOOKINGS = 5


def _shot(page: Page, caption: str, seconds: float) -> tuple[Image.Image, float]:
    picture = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    picture = picture.resize((WIDTH, round(picture.height * WIDTH / picture.width)), Image.LANCZOS)
    frame = Image.new("RGB", (WIDTH, picture.height + CAPTION_HEIGHT), "white")
    frame.paste(picture, (0, CAPTION_HEIGHT))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, WIDTH, CAPTION_HEIGHT), fill=(31, 41, 55))
    draw.text(
        (20, CAPTION_HEIGHT // 2),
        caption,
        fill="white",
        font=ImageFont.load_default(size=22),
        anchor="lm",
    )
    return frame, seconds


def record(ui: str, erp: str) -> list[tuple[Image.Image, float]]:
    frames = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport=VIEWPORT)
            page.set_default_timeout(30_000)

            page.goto(ui)
            expect(page.get_by_text("Drop XRechnung")).to_be_visible()
            frames.append(_shot(page, "Five invoices arrive at the review UI", 2.0))

            page.get_by_label("Invoice files").set_input_files([str(p) for p in SAMPLES])
            page.get_by_role("button", name="All documents").click()
            expect(page.get_by_role("button", name="All documents 5")).to_be_visible()
            expect(page.get_by_text("Waiting to be read")).to_have_count(0, timeout=60_000)
            frames.append(
                _shot(page, "Validated, matched to orders, checked: 4 need no review", 3.5)
            )

            page.get_by_role("button", name="To review").click()
            expect(page.get_by_role("link", name=FLAGGED)).to_be_visible()
            frames.append(_shot(page, "One is flagged for a person", 2.0))

            page.get_by_role("link", name=FLAGGED).click()
            expect(page.get_by_text("Why a person needs to look")).to_be_visible()
            page.get_by_text("Price above order").scroll_into_view_if_needed()
            frames.append(_shot(page, "Its price is 12 % above the purchase order", 4.0))

            page.get_by_placeholder("Your name").fill(REVIEWER)
            page.get_by_role("button", name="Approve").click()
            expect(page.get_by_text("Approved", exact=False).first).to_be_visible()
            page.evaluate("window.scrollTo(0, 0)")
            frames.append(_shot(page, "A reviewer checks it and approves", 3.0))

            deadline = time.monotonic() + 90
            while True:
                page.goto(f"{erp}/ui/bookings")
                if page.locator("table[aria-label='Buchungen'] tbody tr").count() >= BOOKINGS:
                    break
                if time.monotonic() > deadline:
                    raise SystemExit(f"the mock ERP did not reach {BOOKINGS} bookings in 90 s")
                time.sleep(2)
            # The ERP's screens are plain, small HTML: enlarged so the five rows read.
            page.evaluate("document.body.style.zoom = '2'")
            frames.append(_shot(page, "All five are booked in the ERP, each exactly once", 4.0))
        finally:
            browser.close()
    return frames


def save(frames: list[tuple[Image.Image, float]], out: Path) -> None:
    height = max(frame.height for frame, _ in frames)
    images = []
    for frame, _ in frames:
        canvas = Image.new("RGB", (WIDTH, height), "white")
        canvas.paste(frame, (0, 0))
        images.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=128))
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=[round(seconds * 1000) for _, seconds in frames],
        loop=0,
        optimize=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ui", default="http://localhost:3000")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--erp", default="http://127.0.0.1:8100")
    parser.add_argument("--out", type=Path, default=Path("docs/demo.gif"))
    args = parser.parse_args()

    stored = httpx.get(f"{args.api}/documents", timeout=10).json()["documents"]
    if stored:
        print(
            f"{len(stored)} documents are already stored: run this on a fresh stack "
            "(see `make demo-gif`), not on a database in use"
        )
        return 1
    if len(SAMPLES) != BOOKINGS:
        print(f"expected {BOOKINGS} files in sample_invoices/, found {len(SAMPLES)}")
        return 1
    save(record(args.ui, args.erp), args.out)
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
