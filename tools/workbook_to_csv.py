"""Converts a GSC Job-to-Cash workbook (.xlsx) into one CSV file that FlowGuard's Imports page accepts.

    python tools/workbook_to_csv.py <workbook.xlsx> [<output.csv>]

Every sheet goes into the same file, each starting with a "#SHEET,<sheet name>" row followed by the sheet's own
rows exactly as they appear (header row first). Opened in Excel it reads as the four tables stacked one above
the other. The output is data: keep it out of version control (data/ is git-ignored).
"""
import csv
import sys
from datetime import date, datetime
from pathlib import Path


def cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")  # same format the workbook uses for its text dates
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def convert(src: Path, dst: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(src, data_only=True, read_only=True)
    counts = {}
    with open(dst, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel opens the £ signs correctly
        w = csv.writer(f)
        for name in wb.sheetnames:
            rows = [r for r in wb[name].iter_rows(values_only=True) if any(v not in (None, "") for v in r)]
            w.writerow(["#SHEET", name])
            for r in rows:
                cells = [cell(v) for v in r]
                while cells and cells[-1] == "":
                    cells.pop()
                w.writerow(cells)
            counts[name] = max(len(rows) - 1, 0)
    wb.close()
    return counts


def main(argv):
    if not argv or len(argv) > 2:
        print(__doc__)
        return 2
    src = Path(argv[0])
    dst = Path(argv[1]) if len(argv) == 2 else src.with_suffix(".csv")
    counts = convert(src, dst)
    print(f"Wrote {dst} ({dst.stat().st_size // 1024} KB): " + ", ".join(f"{k} {v} rows" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
