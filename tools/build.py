"""Builds frontend/index.html (served by FastAPI) from frontend/src. No Node toolchain required."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
CHART = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>'


def page(extra_head="", extra_script=""):
    css = (SRC / "styles.css").read_text(encoding="utf-8")
    js = "\n".join((SRC / f).read_text(encoding="utf-8") for f in ("core.js", "pages_a.js", "pages_b.js", "pages_c.js"))
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>FlowGuard · GSC Job-to-Cash control</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%230B1F3A'/%3E%3Cpath d='M12 4 6 7v4c0 4 3 7 6 8 3-1 6-4 6-8V7z' fill='none' stroke='white' stroke-width='1.8'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{css}</style>{CHART}{extra_head}
</head><body><noscript>FlowGuard needs JavaScript.</noscript>{extra_script}
<script>{js}</script></body></html>"""


def main():
    (ROOT / "frontend" / "index.html").write_text(page(), encoding="utf-8")
    print("built", (ROOT / "frontend" / "index.html").stat().st_size)


if __name__ == "__main__":
    main()
