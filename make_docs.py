"""Generate the synthetic demo documents and their ground truth.

Nothing here is real: company names, items, and amounts are invented.

Outputs
  input/supplier_statement.pdf   3-page text PDF with one long table
  input/scanned_invoice.pdf      image-only PDF (rotated, noisy scan)
  truth/*.json                   the values the PDFs were built from
"""
import json
import random
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).parent
INPUT, TRUTH = ROOT / "input", ROOT / "truth"
STYLES = getSampleStyleSheet()

ITEMS = [
    ("A4 copy paper, 80 gsm, box of 5 reams", 18.50), ("Ballpoint pens, blue, pack of 50", 7.25),
    ("Toner cartridge, black, high yield", 64.00), ("Stapler, heavy duty", 12.90),
    ("Whiteboard markers, assorted, pack of 12", 9.40), ("Lever arch files, pack of 10", 21.00),
    ("Desk organizer, 5 compartments", 14.75), ("Sticky notes, 76 x 76 mm, pack of 12", 6.80),
    ("USB flash drive, 64 GB", 8.95), ("Wireless mouse", 11.50),
    ("HDMI cable, 2 m", 5.60), ("Laminating pouches, A4, pack of 100", 13.20),
    ("Printer drum unit", 48.30), ("Document shredder, cross cut", 89.00),
    ("Envelopes, C4, box of 250", 16.40), ("Correction tape, pack of 10", 8.10),
    ("Label printer tape, 12 mm", 10.25), ("Cleaning wipes, tub of 100", 4.90),
    ("Keyboard, wired, US layout", 15.80), ("Monitor stand, adjustable", 27.50),
    ("Paper clips, box of 1000", 3.60), ("Notebook, A5, ruled, pack of 5", 9.95),
    ("Extension lead, 4 socket, 3 m", 12.30), ("Calculator, 12 digit", 7.70),
    ("Packing tape, 48 mm, pack of 6", 8.45), ("Flip chart pad, 20 sheets", 11.90),
]


def _cluster(values, gap=12):
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(np.mean(g)) for g in groups]


def money(x):
    return f"{x:,.2f}"


def table_style(header_rows=1):
    return TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#e6e6e6")),
        ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])


def build_statement(rng):
    """3 pages, 26 line items, a subtotal row per page and a grand total."""
    rows = []
    for i, (desc, price) in enumerate(ITEMS):
        qty = rng.randint(1, 40)
        rows.append({
            "date": f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}",
            "ref": f"INV-{4100 + i}",
            "description": desc, "qty": qty, "unit_price": price,
            "amount": round(qty * price, 2),
        })
    rows.sort(key=lambda r: r["date"])
    pages = [rows[0:9], rows[9:18], rows[18:26]]
    header = ["Date", "Reference", "Description", "Qty", "Unit Price", "Amount"]
    story, subtotals = [], []
    for n, page in enumerate(pages, start=1):
        story.append(Paragraph("Northfield Office Supplies Ltd. (fictional)", STYLES["Title"]))
        story.append(Paragraph(f"Statement of account: Harbor Street Trading Co. &nbsp;&nbsp; Page {n} of 3", STYLES["Normal"]))
        story.append(Spacer(1, 6 * mm))
        data = [header] + [[r["date"], r["ref"], r["description"], str(r["qty"]),
                            money(r["unit_price"]), money(r["amount"])] for r in page]
        sub = round(sum(r["amount"] for r in page), 2)
        subtotals.append(sub)
        data.append(["", "", f"Page {n} subtotal", "", "", money(sub)])
        if n == 3:
            data.append(["", "", "Statement total", "", "", money(round(sum(subtotals), 2))])
        t = Table(data, colWidths=[24 * mm, 24 * mm, 74 * mm, 14 * mm, 24 * mm, 26 * mm], repeatRows=1)
        t.setStyle(table_style())
        story.append(t)
        if n < 3:
            story.append(PageBreak())
    SimpleDocTemplate(str(INPUT / "supplier_statement.pdf"), pagesize=A4,
                      leftMargin=12 * mm, rightMargin=12 * mm, topMargin=15 * mm).build(story)
    truth = {"rows": rows, "page_subtotals": subtotals, "total": round(sum(subtotals), 2)}
    (TRUTH / "supplier_statement.json").write_text(json.dumps(truth, indent=2))


def build_scanned_invoice(rng):
    """One-page invoice, rasterized, rotated and degraded so it has no text layer."""
    picks = rng.sample(ITEMS, 8)
    rows = []
    for i, (desc, price) in enumerate(picks, start=1):
        qty = rng.randint(1, 25)
        rows.append({"line": i, "description": desc, "qty": qty,
                     "unit_price": price, "amount": round(qty * price, 2)})
    total = round(sum(r["amount"] for r in rows), 2)
    data = [["#", "Description", "Qty", "Unit Price", "Amount"]]
    data += [[str(r["line"]), r["description"], str(r["qty"]), money(r["unit_price"]), money(r["amount"])] for r in rows]
    data.append(["", "Invoice total", "", "", money(total)])
    t = Table(data, colWidths=[12 * mm, 92 * mm, 18 * mm, 28 * mm, 30 * mm], rowHeights=11 * mm)
    style = table_style()
    style.add("FONTSIZE", (0, 0), (-1, -1), 11)
    style.add("ALIGN", (2, 0), (-1, -1), "RIGHT")
    t.setStyle(style)
    story = [Paragraph("Bluegate Stationery (fictional)", STYLES["Title"]),
             Paragraph("Invoice BG-2026-0318 &nbsp;&nbsp; Bill to: Harbor Street Trading Co.", STYLES["Normal"]),
             Spacer(1, 8 * mm), t]
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean.pdf"
        SimpleDocTemplate(str(clean), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=20 * mm).build(story)
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-singlefile", str(clean), str(Path(tmp) / "page")], check=True)
        img = cv2.imread(str(Path(tmp) / "page.png"), cv2.IMREAD_GRAYSCALE)
    # A stain over one cell (line 5, unit price), so the demo has something
    # that genuinely cannot be read and must be flagged rather than guessed.
    h, w = img.shape
    dark = img < 128
    ys = _cluster(np.where(dark.sum(axis=1) > 0.4 * w)[0])
    xs = _cluster(np.where(dark[ys[0]:ys[-1]].sum(axis=0) > 0.5 * (ys[-1] - ys[0]))[0])
    x0, x1, y0, y1 = xs[3], xs[4], ys[5], ys[6]
    stain = np.zeros_like(img)
    cv2.ellipse(stain, (int(x0 + 0.68 * (x1 - x0)), (y0 + y1) // 2),
                (int(0.27 * (x1 - x0)), int(0.36 * (y1 - y0))), 8, 0, 360, 255, -1)
    stain = cv2.GaussianBlur(stain, (21, 21), 0).astype(float) / 255
    img = (img.astype(float) * (1 - 0.88 * stain) + 70 * 0.88 * stain).astype(np.uint8)
    # Simulate a scan: slight rotation, blur, sensor noise, grey paper.
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 1.6, 1.0)
    img = cv2.warpAffine(img, m, (w, h), borderValue=255, flags=cv2.INTER_LINEAR)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    noise = np.random.default_rng(7).normal(0, 9, img.shape)
    img = np.clip(img.astype(float) * 0.93 + 12 + noise, 0, 255).astype(np.uint8)
    Image.fromarray(img).convert("RGB").save(INPUT / "scanned_invoice.pdf", resolution=200.0)
    (TRUTH / "scanned_invoice.json").write_text(json.dumps({"rows": rows, "total": total,
                    "stained_cell": {"line": 5, "field": "unit_price"}}, indent=2))


if __name__ == "__main__":
    INPUT.mkdir(exist_ok=True)
    TRUTH.mkdir(exist_ok=True)
    rng = random.Random(42)
    build_statement(rng)
    build_scanned_invoice(rng)
    print("Wrote input/supplier_statement.pdf, input/scanned_invoice.pdf and truth/*.json")
