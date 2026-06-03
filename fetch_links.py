#!/usr/bin/env python3
"""
fetch_links.py
==============

Scrape the Makeover Monday master table from https://makeovermonday.co.uk/ and
turn it into a structured list of weekly challenges. This is the authoritative
source of truth: it covers every week (2016-present) and its *actual* data link,
which is usually data.world but is sometimes Maven Analytics, a shortened link,
or another site.

Output (written next to this script):
    links_master.csv   - one row per week, easy to inspect / hand-edit
    links_master.json  - same data as JSON for the downloader

Each row has:
    year, week, date, kind, data_url, resolved_url, title, article_url, source_url

  * kind = "dataworld"  -> resolved_url is a data.world dataset (full archive)
           "file"       -> resolved_url is a direct, downloadable data file
           "external"   -> a page (e.g. Maven) where the data needs manual download

Usage:
    python fetch_links.py                 # scrape, resolve links, write csv+json
    python fetch_links.py --no-resolve    # skip following shortened links (faster)
    python fetch_links.py --year 2023     # only keep rows for one or more years
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit("The 'requests' package is required.  pip install requests")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("The 'beautifulsoup4' package is required.  pip install beautifulsoup4")

SITE_URL = "https://makeovermonday.co.uk/"
USER_AGENT = "makeovermonday-archiver/1.0 (+https://github.com/)"

# Link shorteners we should follow to discover the real destination.
SHORTENERS = {
    "bit.ly", "l1nq.com", "sl1nk.com", "tinyurl.com", "t.co", "lnkd.in",
    "rebrand.ly", "ow.ly", "buff.ly", "cutt.ly", "rb.gy", "shorturl.at",
}

# Extensions that indicate a directly downloadable data file.
DATA_EXTS = {
    ".csv", ".tsv", ".xlsx", ".xls", ".json", ".zip", ".txt", ".geojson",
    ".hyper", ".tde", ".twbx", ".parquet", ".xml", ".numbers",
}


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{level}] {msg}", flush=True)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def cell_text(cell) -> str:
    return cell.get_text(" ", strip=True) if cell else ""


def first_link(cell):
    if not cell:
        return "", ""
    a = cell.find("a", href=True)
    if a:
        return a.get_text(" ", strip=True), a["href"].strip()
    return cell_text(cell), ""


def _looks_like_header(cells_text: list) -> bool:
    joined = " ".join(c.lower() for c in cells_text)
    return "week" in joined and "data" in joined


def parse_tables(html: str) -> list:
    """Return raw rows from every table that matches the Week/Date/Data schema."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        # Confirm this table is the challenge list by inspecting its header row.
        header_cells = [cell_text(c) for c in trs[0].find_all(["th", "td"])]
        if not _looks_like_header(header_cells):
            continue
        for tr in trs[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            week_txt = cell_text(cells[0])
            if not week_txt.split("-")[0].strip().isdigit():
                continue
            date_txt = cell_text(cells[1])
            _, data_url = first_link(cells[2])
            title, article_url = first_link(cells[3]) if len(cells) > 3 else ("", "")
            _, source_url = first_link(cells[4]) if len(cells) > 4 else ("", "")
            rows.append(
                {
                    "week_txt": week_txt,
                    "date": date_txt,
                    "data_url": data_url,
                    "title": title,
                    "article_url": article_url,
                    "source_url": source_url,
                }
            )
    return rows


def year_from_date(date_txt: str) -> str:
    # Expected format DD/MM/YYYY; be tolerant of other separators.
    for sep in ("/", "-", "."):
        parts = [p for p in date_txt.split(sep) if p.strip()]
        if len(parts) == 3:
            for p in parts:
                if len(p) == 4 and p.isdigit():
                    return p
    # Fallback: any 4-digit 20xx in the string
    import re

    m = re.search(r"20\d{2}", date_txt)
    return m.group(0) if m else "unknown"


def week_from_txt(week_txt: str) -> str:
    digits = "".join(ch for ch in week_txt.split("-")[0] if ch.isdigit())
    return f"{int(digits):02d}" if digits else week_txt.strip()


def domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def resolve_url(session: requests.Session, url: str) -> str:
    """Follow redirects (used for shortened links). Returns final URL or original."""
    try:
        r = session.head(url, allow_redirects=True, timeout=30)
        if r.url:
            return r.url
    except requests.RequestException:
        try:
            r = session.get(url, allow_redirects=True, timeout=30, stream=True)
            return r.url or url
        except requests.RequestException:
            return url
    return url


def classify(url: str) -> str:
    d = domain(url)
    if "data.world" in d:
        return "dataworld"
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in DATA_EXTS):
        return "file"
    return "external"


def build_records(rows: list, session, resolve: bool) -> list:
    records = []
    for r in rows:
        data_url = r["data_url"]
        resolved = data_url
        if resolve and data_url and domain(data_url) in SHORTENERS:
            resolved = resolve_url(session, data_url)
        kind = classify(resolved) if resolved else "external"
        records.append(
            {
                "year": year_from_date(r["date"]),
                "week": week_from_txt(r["week_txt"]),
                "date": r["date"],
                "kind": kind,
                "data_url": data_url,
                "resolved_url": resolved,
                "title": r["title"],
                "article_url": r["article_url"],
                "source_url": r["source_url"],
            }
        )
    return records


FIELDS = [
    "year", "week", "date", "kind",
    "data_url", "resolved_url", "title", "article_url", "source_url",
]


def write_outputs(records: list, out_dir: Path) -> None:
    csv_path = out_dir / "links_master.csv"
    json_path = out_dir / "links_master.json"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for rec in records:
            w.writerow(rec)
    json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Wrote {csv_path.name} and {json_path.name} ({len(records)} rows)")


def load_records(path: Path) -> list:
    """Used by the downloader to read a previously-scraped list."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fetch_records(resolve: bool = True, years: list | None = None) -> list:
    session = make_session()
    log(f"Fetching master table from {SITE_URL}")
    resp = session.get(SITE_URL, timeout=60)
    resp.raise_for_status()
    rows = parse_tables(resp.text)
    if not rows:
        raise RuntimeError(
            "No challenge table found on the page. The site layout may have "
            "changed; inspect the HTML and adjust parse_tables()."
        )
    records = build_records(rows, session, resolve)
    if years:
        wanted = {str(y) for y in years}
        records = [r for r in records if r["year"] in wanted]
    return records


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape the Makeover Monday master list of weekly challenges.")
    ap.add_argument("--no-resolve", action="store_true", help="Don't follow shortened links.")
    ap.add_argument("--year", nargs="+", help="Keep only these year(s).")
    ap.add_argument("--out", type=Path, default=Path("."), help="Where to write the csv/json (default: here).")
    args = ap.parse_args(argv)

    records = fetch_records(resolve=not args.no_resolve, years=args.year)

    # Summary
    by_kind: dict = {}
    for r in records:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    log(f"Parsed {len(records)} weeks: " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    external = [r for r in records if r["kind"] != "dataworld"]
    if external:
        log(f"{len(external)} non-data.world week(s) (will be capture+flagged):", "WARN")
        for r in external[:20]:
            log(f"   {r['year']} W{r['week']}: {r['kind']} -> {r['resolved_url']}", "WARN")

    write_outputs(records, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
