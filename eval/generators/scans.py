"""Turn generated PDFs into scan-like images: the OCR stress test.

Each PDF page is rasterised and then degraded the way paper handling does it:
skew, blur, sensor noise, uneven exposure, a received/paid stamp, low
resolution and JPEG compression. Three severity profiles alternate.

    python -m eval.generators.scans --count 100 --out eval/out/scans

Pixel values come out of floating-point image operations, so the files are
reproducible on one machine but not guaranteed byte-identical across platforms
or library versions.
"""

import argparse
import io
import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.models import DocumentFormat, Invoice
from eval.generators import pdf as pdf_generator
from eval.generators.structured import DEFAULT_SEED, GOLD_FILE


@dataclass(frozen=True)
class Profile:
    name: str
    dpi: int
    max_skew_degrees: float
    blur_radius: float
    noise_sigma: float
    contrast: float
    brightness: float
    jpeg_quality: int
    stamp: bool


# Calibrated by eye: on the heavy profile every gold field is still readable
# by a person at normal zoom. Anything harsher made small print (IBAN, footer)
# illegible, which would turn the set into a test of guessing.
PROFILES = (
    Profile("light", 150, 0.6, 0.3, 3.0, 0.95, 1.00, 75, stamp=False),
    Profile("medium", 140, 1.5, 0.5, 6.0, 0.90, 1.04, 55, stamp=True),
    Profile("heavy", 130, 3.0, 0.7, 9.0, 0.85, 0.95, 42, stamp=True),
)
PAPER = 250
STAMP_TEXTS = ("EINGEGANGEN", "BEZAHLT", "GEBUCHT", "RECEIVED")


def rasterise(pdf: bytes, dpi: int) -> Image.Image:
    document = pdfium.PdfDocument(pdf)
    try:
        return document[0].render(scale=dpi / 72).to_pil().convert("L")
    finally:
        document.close()


def _stamp(page: Image.Image, rng: random.Random, text: str) -> Image.Image:
    """A slanted office stamp in the upper half, where it can cover header data."""
    font = ImageFont.load_default(size=max(14, page.width // 28))
    box = ImageDraw.Draw(page).textbbox((0, 0), text, font=font)
    width, height = box[2] + 24, box[3] + 20
    layer = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(layer)
    draw.rectangle((2, 2, width - 3, height - 3), outline=150, width=3)
    draw.text((12, 6), text, fill=150, font=font)
    layer = layer.rotate(rng.uniform(-18, 18), expand=True)
    x = rng.randint(page.width // 3, max(page.width // 3, page.width - layer.width - 10))
    y = rng.randint(page.height // 12, page.height // 3)
    ink = Image.new("L", layer.size, 70)
    page.paste(ink, (x, y), layer)
    return page


def degrade(page: Image.Image, profile: Profile, rng: random.Random, stamp_text: str) -> bytes:
    if profile.stamp:
        page = _stamp(page, rng, stamp_text)
    skew = rng.uniform(-profile.max_skew_degrees, profile.max_skew_degrees)
    page = page.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=PAPER)
    page = page.filter(ImageFilter.GaussianBlur(profile.blur_radius))
    page = ImageEnhance.Contrast(page).enhance(profile.contrast)
    page = ImageEnhance.Brightness(page).enhance(profile.brightness)

    noise = np.random.default_rng(rng.getrandbits(32)).normal(
        0, profile.noise_sigma, page.size[::-1]
    )
    pixels = np.clip(np.asarray(page, dtype=np.float32) + noise, 0, 255).astype(np.uint8)

    buffer = io.BytesIO()
    Image.fromarray(pixels, mode="L").save(buffer, format="JPEG", quality=profile.jpeg_quality)
    return buffer.getvalue()


@dataclass(frozen=True)
class GeneratedScan:
    doc_id: str
    filename: str
    content: bytes
    layout: str
    split: str
    profile: str
    invoice: Invoice

    def gold_record(self) -> dict:
        return {
            "id": self.doc_id,
            "file": self.filename,
            "format": DocumentFormat.IMAGE.value,
            "layout": self.layout,
            "split": self.split,
            "profile": self.profile,
            "expected_valid": None,
            "expected_rule": None,
            "invoice": self.invoice.model_dump(mode="json"),
        }


def generate(count: int, seed: int = DEFAULT_SEED) -> Iterator[GeneratedScan]:
    for index, source in enumerate(pdf_generator.generate(count, seed)):
        rng = random.Random(f"scan:{seed}:{index}")  # noqa: S311 - reproducible test data
        # Cycle profiles independently of layouts (16 layouts, 3 profiles).
        profile = PROFILES[index % len(PROFILES)]
        stamp_text = rng.choice(STAMP_TEXTS)
        if stamp_text == "EINGEGANGEN":
            # A second date on the page that is neither issue nor due date.
            received = source.invoice.issue_date + timedelta(days=rng.randint(2, 6))
            stamp_text = f"EINGEGANGEN {received:%d.%m.%Y}"
        doc_id = f"scan-{index:04d}"
        yield GeneratedScan(
            doc_id=doc_id,
            filename=f"{doc_id}.jpg",
            content=degrade(rasterise(source.content, profile.dpi), profile, rng, stamp_text),
            layout=source.layout,
            split=source.split,
            profile=profile.name,
            invoice=source.invoice.model_copy(update={"source_format": DocumentFormat.IMAGE}),
        )


def write_set(out_dir: Path, count: int, seed: int = DEFAULT_SEED) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with (out_dir / GOLD_FILE).open("w", encoding="utf-8") as gold:
        for document in generate(count, seed):
            (out_dir / document.filename).write_bytes(document.content)
            gold.write(json.dumps(document.gold_record(), ensure_ascii=False, sort_keys=True))
            gold.write("\n")
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/scans"))
    args = parser.parse_args()
    print(f"wrote {write_set(args.out, args.count, args.seed)} documents to {args.out}")


if __name__ == "__main__":
    main()
