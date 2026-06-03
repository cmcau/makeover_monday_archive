#!/usr/bin/env python3
"""
download_makeovermonday.py
==========================

Iterate over a list of data.world Makeover Monday datasets, download every data
file and the full "Summary" content (text + images), and lay everything out in a
clean, GitHub-friendly folder structure.

For each dataset it produces:

    output/<year>/w<week>/
        README.md          <- the Summary / description (Markdown), images rewritten to local paths
        metadata.json      <- raw dataset metadata from the data.world API
        data/              <- every data file in the dataset (csv, xlsx, etc.)
        images/            <- every image referenced in the Summary

Authentication
--------------
data.world's API needs a (free) read token. Get one at:
    https://data.world/settings/advanced  ->  "Read/Write" or "Read-Only" token

Provide it in any of these ways (checked in order):
    1. --token YOUR_TOKEN
    2. environment variable  DW_AUTH_TOKEN
    3. a ".env" file in the working directory containing  DW_AUTH_TOKEN=YOUR_TOKEN

Usage examples
--------------
    # From an explicit list of URLs (one per line in links.txt)
    python download_makeovermonday.py --links-file links.txt

    # Auto-generate a year/week range
    python download_makeovermonday.py --year 2023 --weeks 1-52

    # Multiple years
    python download_makeovermonday.py --year 2022 2023 --weeks 1-52

    # Combine both a file and a generated range
    python download_makeovermonday.py --links-file links.txt --year 2024 --weeks 1-10

    # See what would happen without downloading
    python download_makeovermonday.py --year 2023 --weeks 1-5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, unquote

try:
    import requests
except ImportError:
    sys.exit(
        "The 'requests' package is required.\n"
        "Install it with:  pip install requests"
    )

API_BASE = "https://api.data.world/v0"
DEFAULT_OWNER = "makeovermonday"
USER_AGENT = "makeovermonday-archiver/1.0 (+https://github.com/)"

# Matches data.world dataset URLs like:
#   https://data.world/makeovermonday/2023w1
#   https://data.world/makeovermonday/2023-w01-some-title
DW_URL_RE = re.compile(
    r"data\.world/(?P<owner>[^/\s]+)/(?P<dataset>[^/\s?#]+)", re.IGNORECASE
)

# Pull a year + week out of a dataset slug, e.g. "2023w1", "2023-w01", "2023week1"
YEAR_WEEK_RE = re.compile(r"(?P<year>20\d{2})\D*?w(?:eek)?\D*?(?P<week>\d{1,2})", re.IGNORECASE)

# Markdown image:  ![alt](url "title")   and HTML <img src="url">
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
HTML_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def log(msg: str, level: str = "INFO") -> None:
    print(f"[{level}] {msg}", flush=True)


def load_token(cli_token: str | None) -> str | None:
    """Resolve the API token from CLI arg, env var, or a .env file."""
    if cli_token:
        return cli_token.strip()
    if os.environ.get("DW_AUTH_TOKEN"):
        return os.environ["DW_AUTH_TOKEN"].strip()
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DW_AUTH_TOKEN") and "=" in line:
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def parse_year_arg(values: list[str] | None) -> list[int]:
    years: list[int] = []
    for v in values or []:
        years.append(int(v))
    return years


def parse_weeks(spec: str | None) -> list[int]:
    """Parse a week spec like '1-52' or '1,3,5' or '1-10,20'."""
    if not spec:
        return []
    weeks: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            weeks.extend(range(int(lo), int(hi) + 1))
        else:
            weeks.append(int(part))
    return weeks


def links_from_file(path: Path) -> list[tuple[str, str]]:
    """Read a links file, returning (owner, dataset) tuples. Ignores blanks/comments."""
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = DW_URL_RE.search(line)
        if m:
            out.append((m.group("owner").lower(), m.group("dataset")))
        else:
            log(f"Could not parse a data.world link from: {line!r}", "WARN")
    return out


def links_from_range(owner: str, years: list[int], weeks: list[int]) -> list[tuple[str, str]]:
    """Generate (owner, dataset) tuples like (makeovermonday, 2023w1)."""
    out: list[tuple[str, str]] = []
    for year in years:
        for week in weeks:
            out.append((owner, f"{year}w{week}"))
    return out


def dedupe(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for p in pairs:
        key = (p[0].lower(), p[1].lower())
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def derive_year_week(dataset: str, meta: dict) -> tuple[str, str]:
    """Work out a (year, week) folder name. Falls back to the slug if unknown."""
    m = YEAR_WEEK_RE.search(dataset)
    if not m:
        # Try the dataset title as a fallback
        m = YEAR_WEEK_RE.search(meta.get("title", "") if meta else "")
    if m:
        year = m.group("year")
        week = f"{int(m.group('week')):02d}"
        return year, week
    return "unsorted", dataset


def safe_filename(name: str) -> str:
    name = unquote(name)
    name = name.split("?")[0].split("#")[0]
    name = os.path.basename(name)
    name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name).strip()
    return name or "file"


# --------------------------------------------------------------------------- #
# data.world API client
# --------------------------------------------------------------------------- #
class DataWorldClient:
    def __init__(self, token: str, timeout: int = 60, retries: int = 3, pause: float = 0.5):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        self.timeout = timeout
        self.retries = retries
        self.pause = pause

    def _get(self, url: str, *, stream: bool = False, accept_json: bool = True):
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                headers = {} if accept_json else {"Accept": "*/*"}
                resp = self.session.get(
                    url, timeout=self.timeout, stream=stream, headers=headers
                )
                if resp.status_code == 429:  # rate limited
                    wait = float(resp.headers.get("Retry-After", 2 * attempt))
                    log(f"Rate limited; waiting {wait:.0f}s", "WARN")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.HTTPError as e:
                # 404 etc. — don't retry client errors other than 429
                if e.response is not None and 400 <= e.response.status_code < 500:
                    raise
                last_exc = e
            except requests.RequestException as e:
                last_exc = e
            if attempt < self.retries:
                time.sleep(self.pause * attempt)
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Failed to GET {url}")

    def get_dataset(self, owner: str, dataset: str) -> dict:
        url = f"{API_BASE}/datasets/{owner}/{dataset}"
        return self._get(url).json()

    def download_file(self, owner: str, dataset: str, filename: str, dest: Path) -> None:
        url = f"{API_BASE}/file_download/{owner}/{dataset}/{filename}"
        resp = self._get(url, stream=True, accept_json=False)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)

    def download_url(self, url: str, dest: Path) -> None:
        resp = self._get(url, stream=True, accept_json=False)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)


# --------------------------------------------------------------------------- #
# Core processing
# --------------------------------------------------------------------------- #
def extract_summary(meta: dict) -> str:
    """data.world exposes the rich text as 'summary'; fall back to 'description'."""
    summary = (meta.get("summary") or "").strip()
    description = (meta.get("description") or "").strip()
    if summary and description and description not in summary:
        return f"{description}\n\n{summary}"
    return summary or description


def find_image_urls(markdown: str) -> list[str]:
    urls = MD_IMAGE_RE.findall(markdown or "")
    urls += HTML_IMG_RE.findall(markdown or "")
    # Keep only http(s) images; ignore already-relative paths
    return [u for u in dict.fromkeys(urls) if u.lower().startswith("http")]


def process_dataset(
    client: DataWorldClient,
    owner: str,
    dataset: str,
    out_root: Path,
    *,
    skip_existing: bool = True,
    pause: float = 0.5,
    force_year: str | None = None,
    force_week: str | None = None,
    extra_links: dict | None = None,
) -> bool:
    # Fast path: if this week's folder already exists, skip it entirely —
    # no metadata call, no downloads. Use --overwrite to force a refresh.
    if skip_existing:
        if force_year and force_week:
            pre_y, pre_w = force_year, force_week
        else:
            pre_y, pre_w = derive_year_week(dataset, {})
        pre_folder = out_root / pre_y / (f"w{pre_w}" if str(pre_w).isdigit() else str(pre_w))
        if folder_has_content(pre_folder):
            log(f"  Skipping {owner}/{dataset} — folder already exists ({pre_folder})")
            return True

    log(f"Fetching {owner}/{dataset} ...")
    try:
        meta = client.get_dataset(owner, dataset)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        log(f"  Skipping {owner}/{dataset} (HTTP {code})", "WARN")
        return False

    # Prefer the authoritative year/week from the master table when supplied.
    if force_year and force_week:
        year, week = force_year, force_week
    else:
        year, week = derive_year_week(dataset, meta)
    folder = out_root / year / (f"w{week}" if week.isdigit() else week)
    data_dir = folder / "data"
    img_dir = folder / "images"
    folder.mkdir(parents=True, exist_ok=True)

    # 1) Raw metadata
    (folder / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 2) Summary -> README.md, with images downloaded and relinked
    summary = extract_summary(meta)
    image_urls = find_image_urls(summary)
    url_to_local: dict[str, str] = {}
    if image_urls:
        log(f"  {len(image_urls)} image(s) in summary")
    for i, url in enumerate(image_urls, start=1):
        fname = safe_filename(url)
        if "." not in fname:
            fname = f"image_{i}.png"
        # avoid collisions
        fname = f"{i:02d}_{fname}"
        dest = img_dir / fname
        if skip_existing and dest.exists():
            url_to_local[url] = f"images/{fname}"
            continue
        try:
            client.download_url(url, dest)
            url_to_local[url] = f"images/{fname}"
            time.sleep(pause)
        except Exception as e:  # noqa: BLE001 - keep going on a single bad image
            log(f"    Could not download image {url}: {e}", "WARN")

    readme = summary
    for url, local in url_to_local.items():
        readme = readme.replace(url, local)

    title = meta.get("title") or f"{owner}/{dataset}"
    source_url = f"https://data.world/{owner}/{dataset}"
    extra = ""
    if extra_links:
        if extra_links.get("article_url"):
            extra += f"**Article / original viz:** [{extra_links['article_url']}]({extra_links['article_url']})\n\n"
        if extra_links.get("source_url"):
            extra += f"**Original data source:** [{extra_links['source_url']}]({extra_links['source_url']})\n\n"
    header = (
        f"# {title}\n\n"
        f"**Data (data.world):** [{source_url}]({source_url})\n\n"
        + extra
        + f"**Dataset:** `{owner}/{dataset}`  \n"
        f"**Last updated on data.world:** {meta.get('updated', 'unknown')}\n\n"
        "---\n\n"
    )
    (folder / "README.md").write_text(header + (readme or "_No summary provided._\n"),
                                      encoding="utf-8")

    # 3) Data files
    files = meta.get("files") or []
    if not files:
        log("  No data files listed", "WARN")
    for f in files:
        fname = f.get("name")
        if not fname:
            continue
        dest = data_dir / safe_filename(fname)
        if skip_existing and dest.exists() and dest.stat().st_size > 0:
            continue
        try:
            log(f"  Downloading file: {fname}")
            client.download_file(owner, dataset, fname, dest)
            time.sleep(pause)
        except Exception as e:  # noqa: BLE001
            log(f"    Failed to download {fname}: {e}", "WARN")

    log(f"  Done -> {folder}")
    return True


# --------------------------------------------------------------------------- #
# Site-driven mode (master list from makeovermonday.co.uk)
# --------------------------------------------------------------------------- #
def folder_has_content(folder: Path) -> bool:
    """True if the folder exists and contains at least one file/subfolder."""
    try:
        return folder.exists() and any(folder.iterdir())
    except OSError:
        return False


def parse_dw_owner_dataset(url: str) -> tuple[str, str] | None:
    """Extract (owner, dataset) from a data.world dataset URL."""
    m = DW_URL_RE.search(url or "")
    if m:
        return m.group("owner").lower(), m.group("dataset")
    return None


def write_external_week(session, rec: dict, out_root: Path, *, skip_existing: bool, pause: float) -> bool:
    """
    Handle a week whose data is NOT on data.world (Maven, a direct file, etc.).
    Always writes a README capturing the title and every link. If the data link
    is a direct file, download it; otherwise flag it for manual download.
    """
    year = rec.get("year", "unsorted")
    week = rec.get("week", "")
    folder = out_root / year / (f"w{week}" if str(week).isdigit() else str(week) or "unknown")
    data_dir = folder / "data"

    # Fast path: skip the whole week if its folder already exists.
    if skip_existing and folder_has_content(folder):
        log(f"  Skipping {year} W{week} — folder already exists ({folder})")
        return True

    folder.mkdir(parents=True, exist_ok=True)

    title = rec.get("title") or f"{year} W{week}"
    kind = rec.get("kind", "external")
    data_url = rec.get("resolved_url") or rec.get("data_url") or ""

    downloaded_note = ""
    manual = False
    if kind == "file" and data_url:
        fname = safe_filename(data_url) or f"{year}_w{week}_data"
        dest = data_dir / fname
        if not (skip_existing and dest.exists() and dest.stat().st_size > 0):
            try:
                log(f"  Downloading external file: {data_url}")
                r = session.get(data_url, timeout=60, stream=True)
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if chunk:
                            fh.write(chunk)
                time.sleep(pause)
                downloaded_note = f"Data file downloaded to `data/{fname}`.\n"
            except Exception as e:  # noqa: BLE001
                manual = True
                downloaded_note = f"> Automatic download failed ({e}). Fetch it manually from the link above.\n"
        else:
            downloaded_note = f"Data file already present in `data/{fname}`.\n"
    else:
        manual = True

    links_md = ""
    if rec.get("data_url"):
        links_md += f"**Data link:** [{rec['data_url']}]({rec['data_url']})\n\n"
    if rec.get("resolved_url") and rec["resolved_url"] != rec.get("data_url"):
        links_md += f"**Resolved to:** [{rec['resolved_url']}]({rec['resolved_url']})\n\n"
    if rec.get("article_url"):
        links_md += f"**Article / original viz:** [{rec['article_url']}]({rec['article_url']})\n\n"
    if rec.get("source_url"):
        links_md += f"**Original data source:** [{rec['source_url']}]({rec['source_url']})\n\n"

    manual_banner = (
        "> ⚠️ **Manual download needed.** This week's data is hosted outside "
        "data.world (e.g. a Maven Analytics challenge or a JavaScript page) and "
        "can't be fetched automatically. Use the links below to grab the file and "
        "drop it in this folder's `data/` directory.\n\n"
        if manual else ""
    )

    readme = (
        f"# {title}\n\n"
        f"**Type:** external source (`{kind}`)\n\n"
        + manual_banner
        + links_md
        + ("\n" + downloaded_note if downloaded_note else "")
    )
    (folder / "README.md").write_text(readme, encoding="utf-8")
    (folder / "metadata.json").write_text(
        json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if manual:
        (folder / "MANUAL_DOWNLOAD_NEEDED.txt").write_text(
            f"Data for {year} W{week} is hosted externally and must be downloaded by hand.\n"
            f"Data link: {data_url}\n",
            encoding="utf-8",
        )
        log(f"  External (manual) -> {folder}", "WARN")
    else:
        log(f"  External -> {folder}")
    return not manual


def process_record(client, session, rec: dict, out_root: Path, *, skip_existing: bool, pause: float) -> bool:
    """Dispatch one master-table row to the right handler."""
    kind = rec.get("kind", "external")
    if kind == "dataworld":
        od = parse_dw_owner_dataset(rec.get("resolved_url") or rec.get("data_url") or "")
        if not od:
            log(f"  Could not parse data.world URL for {rec.get('year')} W{rec.get('week')}", "WARN")
            return False
        owner, dataset = od
        return process_dataset(
            client, owner, dataset, out_root,
            skip_existing=skip_existing, pause=pause,
            force_year=rec.get("year"), force_week=rec.get("week"),
            extra_links={"article_url": rec.get("article_url"), "source_url": rec.get("source_url")},
        )
    return write_external_week(session, rec, out_root, skip_existing=skip_existing, pause=pause)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Archive data.world Makeover Monday datasets into a GitHub-ready folder layout.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--from-site", action="store_true",
                   help="Discover every week from the makeovermonday.co.uk master table "
                        "(handles data.world AND non-data.world weeks). Recommended.")
    p.add_argument("--master-json", type=Path,
                   help="Use a previously-saved links_master.json instead of scraping live.")
    p.add_argument("--no-resolve", action="store_true",
                   help="With --from-site, don't follow shortened links.")
    p.add_argument("--links-file", type=Path, help="Text file with one data.world URL per line.")
    p.add_argument("--year", nargs="+", help="One or more years to generate, e.g. --year 2023 2024.")
    p.add_argument("--weeks", help="Week spec to generate, e.g. '1-52' or '1,3,5'. Used with --year.")
    p.add_argument("--owner", default=DEFAULT_OWNER,
                   help=f"data.world owner/org for generated links (default: {DEFAULT_OWNER}).")
    p.add_argument("--output", type=Path, default=Path("output"),
                   help="Output root directory (default: ./output).")
    p.add_argument("--token", help="data.world API token (overrides DW_AUTH_TOKEN / .env).")
    p.add_argument("--pause", type=float, default=0.5,
                   help="Seconds to pause between requests (default: 0.5).")
    p.add_argument("--overwrite", action="store_true",
                   help="Re-download files even if they already exist.")
    p.add_argument("--dry-run", action="store_true",
                   help="List the datasets that would be processed and exit.")
    return p


def run_from_site(args, token: str | None) -> int:
    """Site-driven mode: archive every week from the makeovermonday.co.uk table."""
    try:
        import fetch_links
    except ImportError:
        log("fetch_links.py must be in the same folder for --from-site.", "ERROR")
        return 2

    if args.master_json:
        if not args.master_json.exists():
            log(f"Master JSON not found: {args.master_json}", "ERROR")
            return 2
        records = fetch_links.load_records(args.master_json)
    else:
        try:
            records = fetch_links.fetch_records(resolve=not args.no_resolve, years=args.year)
        except Exception as e:  # noqa: BLE001
            log(f"Could not fetch the master table: {e}", "ERROR")
            return 2

    if args.year:
        wanted = {str(y) for y in args.year}
        records = [r for r in records if r.get("year") in wanted]

    dw = [r for r in records if r.get("kind") == "dataworld"]
    ext = [r for r in records if r.get("kind") != "dataworld"]
    log(f"{len(records)} week(s): {len(dw)} on data.world, {len(ext)} external.")

    if args.dry_run:
        for r in records:
            print(f"  {r.get('year')} W{r.get('week')}  [{r.get('kind')}]  {r.get('resolved_url')}")
        log("Dry run complete; nothing downloaded.")
        return 0

    session = make_session_for_files()

    client = None
    if dw:
        if not token:
            log("data.world weeks need a token. Set DW_AUTH_TOKEN, --token, or .env.", "ERROR")
            return 2
        client = DataWorldClient(token, pause=args.pause)

    args.output.mkdir(parents=True, exist_ok=True)
    ok = 0
    manual = 0
    failed: list[str] = []
    for r in records:
        label = f"{r.get('year')} W{r.get('week')}"
        try:
            success = process_record(
                client, session, r, args.output,
                skip_existing=not args.overwrite, pause=args.pause,
            )
            if success:
                ok += 1
            elif r.get("kind") != "dataworld":
                manual += 1
            else:
                failed.append(label)
        except KeyboardInterrupt:
            log("Interrupted by user.", "WARN")
            break
        except Exception as e:  # noqa: BLE001
            log(f"Unexpected error on {label}: {e}", "ERROR")
            failed.append(label)

    log(f"Finished. {ok} archived, {manual} flagged for manual download, {len(failed)} failed.")
    if failed:
        log("Failed: " + ", ".join(failed), "WARN")
    return 0


def make_session_for_files():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.from_site or args.master_json:
        return run_from_site(args, load_token(args.token))

    # Assemble the work list
    pairs: list[tuple[str, str]] = []
    if args.links_file:
        if not args.links_file.exists():
            log(f"Links file not found: {args.links_file}", "ERROR")
            return 2
        pairs += links_from_file(args.links_file)
    if args.year:
        years = parse_year_arg(args.year)
        weeks = parse_weeks(args.weeks) or list(range(1, 53))
        pairs += links_from_range(args.owner, years, weeks)

    pairs = dedupe(pairs)

    if not pairs:
        log("Nothing to do. Provide --links-file and/or --year (+ --weeks).", "ERROR")
        build_arg_parser().print_help()
        return 2

    log(f"{len(pairs)} dataset(s) queued.")
    if args.dry_run:
        for owner, dataset in pairs:
            print(f"  https://data.world/{owner}/{dataset}")
        log("Dry run complete; nothing downloaded.")
        return 0

    token = load_token(args.token)
    if not token:
        log(
            "No API token found. Set DW_AUTH_TOKEN, pass --token, or add it to a .env file.\n"
            "       Get a token at https://data.world/settings/advanced",
            "ERROR",
        )
        return 2

    client = DataWorldClient(token, pause=args.pause)
    args.output.mkdir(parents=True, exist_ok=True)

    ok = 0
    failed: list[str] = []
    for owner, dataset in pairs:
        try:
            if process_dataset(
                client, owner, dataset, args.output,
                skip_existing=not args.overwrite, pause=args.pause,
            ):
                ok += 1
            else:
                failed.append(f"{owner}/{dataset}")
        except KeyboardInterrupt:
            log("Interrupted by user.", "WARN")
            break
        except Exception as e:  # noqa: BLE001
            log(f"Unexpected error on {owner}/{dataset}: {e}", "ERROR")
            failed.append(f"{owner}/{dataset}")

    log(f"Finished. {ok} succeeded, {len(failed)} failed/skipped.")
    if failed:
        log("Failed/skipped: " + ", ".join(failed), "WARN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
