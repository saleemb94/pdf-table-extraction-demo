"""Score the extraction against the ground truth the PDFs were generated from.

Usage: python evaluate.py   (run extract.py first)
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent


def score(extracted, truth, fields):
    correct, wrong, flagged_blank = 0, [], 0
    for got, want in zip(extracted, truth):
        for f in fields:
            g, w = got.get(f), want.get(f)
            if isinstance(w, (int, float)) and g is not None:
                ok = abs(float(g) - float(w)) < 0.005
            else:
                ok = g == w
            if ok:
                correct += 1
            elif g in (None, "") and got["flags"]:
                flagged_blank += 1  # left empty and flagged: not a silent error
            else:
                wrong.append((f, g, w))
    return correct, flagged_blank, wrong, len(truth) * len(fields)


def main():
    out = json.loads((ROOT / "output" / "extraction.json").read_text())
    results = {}
    for name, key, fields in (
        ("Supplier statement (text PDF)", "statement", ["date", "ref", "description", "qty", "unit_price", "amount"]),
        ("Scanned invoice (OCR)", "scanned_invoice", ["line", "description", "qty", "unit_price", "amount"]),
    ):
        truth = json.loads((ROOT / "truth" / {"statement": "supplier_statement.json",
                                              "scanned_invoice": "scanned_invoice.json"}[key]).read_text())["rows"]
        rows = out[key]
        assert len(rows) == len(truth), f"{name}: expected {len(truth)} rows, got {len(rows)}"
        correct, flagged_blank, wrong, total = score(rows, truth, fields)
        results[key] = {"cells": total, "correct": correct, "flagged_unreadable": flagged_blank,
                        "silently_wrong": len(wrong)}
        print(f"{name}: {correct} of {total} cells correct, "
              f"{flagged_blank} left blank and flagged, {len(wrong)} silently wrong")
        for f, g, w in wrong:
            print(f"   WRONG {f}: got {g!r}, expected {w!r}")
    (ROOT / "output" / "accuracy.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
