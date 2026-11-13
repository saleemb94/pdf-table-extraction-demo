"""Extract tables from the demo PDFs into one validated Excel workbook.

Two paths, chosen per document:
  text PDF   -> pdfplumber reads the table straight from the text layer
  scanned PDF-> render, deskew, find the table grid, OCR cell by cell

Every extracted row is checked with arithmetic (qty x unit price = amount,
rows sum to the printed totals). A cell that cannot be read or fails a check
is flagged for review. It is never silently "fixed".

Usage: python extract.py
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
import pytesseract
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).parent
INPUT, OUTPUT = ROOT / "input", ROOT / "output"
FLAG_FILL = PatternFill("solid", fgColor="FFF2CC")
HEAD_FILL = PatternFill("solid", fgColor="D9D9D9")
MIN_CONFIDENCE = 60  # tesseract word confidence below this flags the cell


# ---------------------------------------------------------------- helpers
def parse_number(text):
    """'1,207.50' -> 1207.5. Returns None when the text is not a clean number."""
    if text is None:
        return None
    cleaned = text.strip().replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    return float(cleaned)


def as_int(value):
    return int(value) if value is not None and float(value).is_integer() else value


def line_is_consistent(qty, unit_price, amount, tolerance=0.005):
    """True when qty x unit price matches the amount to the cent."""
    if None in (qty, unit_price, amount):
        return False
    return abs(qty * unit_price - amount) <= tolerance


def has_text_layer(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return any((page.extract_text() or "").strip() for page in pdf.pages)


# ---------------------------------------------------------------- text PDF
def extract_statement(pdf_path):
    rows, subtotals, total = [], [], None
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                for cells in table:
                    cells = [(c or "").strip() for c in cells]
                    if cells[0] == "Date":
                        continue  # repeated header
                    label = cells[2].lower()
                    if "subtotal" in label:
                        subtotals.append({"page": page_no, "printed": parse_number(cells[5])})
                    elif "statement total" in label:
                        total = parse_number(cells[5])
                    else:
                        rows.append({
                            "page": page_no, "date": cells[0], "ref": cells[1],
                            "description": cells[2], "qty": as_int(parse_number(cells[3])),
                            "unit_price": parse_number(cells[4]), "amount": parse_number(cells[5]),
                            "flags": [],
                        })
    for r in rows:
        if not line_is_consistent(r["qty"], r["unit_price"], r["amount"]):
            r["flags"].append("qty x unit price does not match amount")
    checks = []
    for s in subtotals:
        computed = round(sum(r["amount"] or 0 for r in rows if r["page"] == s["page"]), 2)
        checks.append({"check": f"Statement page {s['page']} subtotal", "printed": s["printed"],
                       "computed": computed, "ok": s["printed"] == computed})
    computed_total = round(sum(r["amount"] or 0 for r in rows), 2)
    checks.append({"check": "Statement total", "printed": total,
                   "computed": computed_total, "ok": total == computed_total})
    return rows, checks


# ---------------------------------------------------------------- scanned PDF
def render_page(pdf_path, dpi=200):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", str(pdf_path),
                        str(Path(tmp) / "page")], check=True)
        return cv2.imread(str(Path(tmp) / "page.png"), cv2.IMREAD_GRAYSCALE)


def deskew(gray):
    """Estimate the skew from the long table rules and rotate the page level."""
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    lines = cv2.HoughLinesP(binary, 1, np.pi / 1800, threshold=300,
                            minLineLength=gray.shape[1] // 3, maxLineGap=10)
    if lines is None:
        return gray, 0.0
    angles = [np.degrees(np.arctan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4)]
    angles = [a for a in angles if abs(a) < 10]
    if not angles:
        return gray, 0.0
    angle = float(np.median(angles))
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), borderValue=255, flags=cv2.INTER_CUBIC), angle


def cluster(values, gap=12):
    """Merge nearby pixel positions into single line positions."""
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(np.mean(g)) for g in groups]


def find_grid(gray):
    """Return the y positions of horizontal rules and x positions of vertical rules."""
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    h, w = gray.shape
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 8, 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 40)))
    ys = cluster(np.where(horiz.sum(axis=1) > 255 * w * 0.2)[0])
    table = vert[ys[0]:ys[-1], :] if len(ys) >= 2 else vert
    xs = cluster(np.where(table.sum(axis=0) > 255 * table.shape[0] * 0.5)[0])
    return ys, xs


def ocr_cell(gray, box, numeric):
    x0, y0, x1, y1 = box
    pad = 6
    cell = gray[y0 + pad:y1 - pad, x0 + pad:x1 - pad]
    if cell.size == 0:
        return "", 0.0
    cell = cv2.resize(cell, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    cell = cv2.threshold(cell, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    cell = cv2.copyMakeBorder(cell, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    config = "--psm 7"
    if numeric:
        config += " -c tessedit_char_whitelist=0123456789.,"
    data = pytesseract.image_to_data(cell, lang="eng", config=config, output_type=pytesseract.Output.DICT)
    words = [(t, float(c)) for t, c in zip(data["text"], data["conf"]) if t.strip()]
    if not words:
        return "", 0.0
    return " ".join(t for t, _ in words), min(c for _, c in words)


def extract_scanned_invoice(pdf_path):
    gray, angle = deskew(render_page(pdf_path))
    ys, xs = find_grid(gray)
    if len(ys) < 3 or len(xs) != 6:
        raise RuntimeError(f"Table grid not found (rows={len(ys) - 1}, column lines={len(xs)})")
    grid = []
    for r in range(len(ys) - 1):
        row = []
        for c in range(len(xs) - 1):
            # header row and the description column are text; everything else is numeric
            numeric = r > 0 and c != 1
            row.append(ocr_cell(gray, (xs[c], ys[r], xs[c + 1], ys[r + 1]), numeric))
        grid.append(row)

    rows, printed_total = [], None
    for cells in grid[1:]:
        (line_t, line_c), (desc_t, desc_c), (qty_t, qty_c), (price_t, price_c), (amt_t, amt_c) = cells
        if "total" in desc_t.lower():
            printed_total = parse_number(amt_t)
            continue
        row = {"line": parse_number(line_t), "description": desc_t, "qty": parse_number(qty_t),
               "unit_price": parse_number(price_t), "amount": parse_number(amt_t), "flags": []}
        for name, text, conf in (("line", line_t, line_c), ("description", desc_t, desc_c),
                                 ("qty", qty_t, qty_c), ("unit price", price_t, price_c),
                                 ("amount", amt_t, amt_c)):
            if not text:
                row["flags"].append(f"{name}: unreadable")
            elif conf < MIN_CONFIDENCE:
                row["flags"].append(f"{name}: low OCR confidence ({conf:.0f})")
        if None in (row["qty"], row["unit_price"], row["amount"]):
            row["flags"].append("line total cannot be verified")
            if row["unit_price"] is None and row["qty"] and row["amount"] is not None:
                implied = round(row["amount"] / row["qty"], 2)
                row["flags"].append(f"amount / qty implies unit price {implied:.2f}, confirm before use")
        elif not line_is_consistent(row["qty"], row["unit_price"], row["amount"]):
            row["flags"].append("qty x unit price does not match amount")
        for key in ("line", "qty"):
            if row[key] is not None and float(row[key]).is_integer():
                row[key] = int(row[key])
        rows.append(row)
    computed = round(sum(r["amount"] or 0 for r in rows), 2)
    checks = [{"check": "Invoice total", "printed": printed_total, "computed": computed,
               "ok": printed_total == computed}]
    return rows, checks, angle


# ---------------------------------------------------------------- Excel
def write_sheet(ws, headers, keys, rows):
    ws.append(headers)
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True), HEAD_FILL
    for r in rows:
        ws.append([r.get(k) for k in keys] + ["; ".join(r["flags"])])
        if r["flags"]:
            for cell in ws[ws.max_row]:
                cell.fill = FLAG_FILL
    for col in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right")


def main():
    OUTPUT.mkdir(exist_ok=True)
    statement_pdf, invoice_pdf = INPUT / "supplier_statement.pdf", INPUT / "scanned_invoice.pdf"
    assert has_text_layer(statement_pdf) and not has_text_layer(invoice_pdf)

    st_rows, st_checks = extract_statement(statement_pdf)
    inv_rows, inv_checks, angle = extract_scanned_invoice(invoice_pdf)
    checks = st_checks + inv_checks

    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    write_sheet(ws, ["Page", "Date", "Reference", "Description", "Qty", "Unit Price", "Amount", "Review flags"],
                ["page", "date", "ref", "description", "qty", "unit_price", "amount"], st_rows)
    ws = wb.create_sheet("Scanned invoice")
    write_sheet(ws, ["Line", "Description", "Qty", "Unit Price", "Amount", "Review flags"],
                ["line", "description", "qty", "unit_price", "amount"], inv_rows)
    ws = wb.create_sheet("Validation")
    ws.append(["Check", "Printed in document", "Computed from rows", "Result"])
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True), HEAD_FILL
    for c in checks:
        ws.append([c["check"], c["printed"], c["computed"], "OK" if c["ok"] else "MISMATCH"])
        if not c["ok"]:
            for cell in ws[ws.max_row]:
                cell.fill = FLAG_FILL
    flagged = sum(1 for r in st_rows + inv_rows if r["flags"])
    ws.append([])
    ws.append(["Rows flagged for review", flagged])
    for col, width in zip("ABCD", (30, 22, 22, 12)):
        ws.column_dimensions[col].width = width
    wb.save(OUTPUT / "demo_extraction.xlsx")

    (OUTPUT / "extraction.json").write_text(json.dumps(
        {"statement": st_rows, "scanned_invoice": inv_rows, "checks": checks,
         "scan_skew_degrees": round(angle, 2)}, indent=2))

    print(f"Statement: {len(st_rows)} rows from the text layer")
    print(f"Scanned invoice: {len(inv_rows)} rows by OCR (skew corrected: {angle:.2f} degrees)")
    for c in checks:
        print(f"  {'OK      ' if c['ok'] else 'MISMATCH'} {c['check']}: printed {c['printed']}, computed {c['computed']}")
    print(f"Rows flagged for review: {flagged}")
    print("Wrote output/demo_extraction.xlsx and output/extraction.json")


if __name__ == "__main__":
    main()
