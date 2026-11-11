from pathlib import Path

SUITE = Path(__file__).parents[1] / "fixtures" / "xrechnung-testsuite"


def ubl(case: str) -> bytes:
    return _read(case, "ubl")


def cii(case: str) -> bytes:
    return _read(case, "uncefact")


def _read(case: str, syntax: str) -> bytes:
    (path,) = SUITE.glob(f"{case}*_{syntax}.xml")
    return path.read_bytes()
