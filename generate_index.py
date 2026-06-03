#!/usr/bin/env python3
"""
generate_index.py
=================

Scan the ``output/`` archive and build a browsable index of every year/week,
then write it into README.md between the markers:

    <!-- INDEX:START -->
    ... generated table ...
    <!-- INDEX:END -->

Re-run this any time after archiving new weeks. It only touches the marked
section, so the usage docs above it are left untouched.

Usage:
    python generate_index.py                 # uses ./output and ./README.md
    python generate_index.py --output output --readme README.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

START = "<!-- INDEX:START -->"
END = "<!-- INDEX:END -->"
WEEK_RE = re.compile(r"w(\d+)", re.IGNORECASE)


def load_week(meta_path: Path) -> dict:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def data_files(week_dir: Path) -> list:
    d = week_dir / "data"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def build_index(output_root: Path) -> str:
    lines = []
    total_weeks = 0
    total_files = 0

    years = sorted(
        [p for p in output_root.iterdir() if p.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    for year_dir in years:
        week_dirs = sorted(
            [p for p in year_dir.iterdir() if p.is_dir()],
            key=lambda p: int(WEEK_RE.search(p.name).group(1)) if WEEK_RE.search(p.name) else 0,
        )
        if not week_dirs:
            continue
        lines.append(f"### {year_dir.name}  \n")
        lines.append("| Week | Title | Source | Data file(s) | Links |")
        lines.append("|------|-------|--------|--------------|-------|")
        for wk in week_dirs:
            meta = load_week(wk / "metadata.json")
            files = data_files(wk)
            total_weeks += 1
            total_files += len(files)
            rel = f"{output_root.name}/{year_dir.name}/{wk.name}"
            wk_num = WEEK_RE.search(wk.name)
            wk_label = f"W{int(wk_num.group(1))}" if wk_num else wk.name
            local_link = f"[folder]({rel}/) · [summary]({rel}/README.md)"

            # An external week's metadata.json is the scraped record (has
            # "resolved_url"); a data.world week's is the API metadata.
            is_external = "resolved_url" in meta or meta.get("kind") not in (None, "dataworld")
            manual = (wk / "MANUAL_DOWNLOAD_NEEDED.txt").exists()

            if is_external:
                title = (meta.get("title") or wk.name).replace("|", "\\|").strip()
                src_url = meta.get("resolved_url") or meta.get("data_url") or ""
                src_dom = src_url.split("/")[2] if "://" in src_url else "external"
                src_cell = f"⚠️ {src_dom}" if manual else src_dom
                files_txt = "_manual download_" if (manual and not files) else (
                    "<br>".join(f"`{f}`" for f in files) if files else "_none_"
                )
                source_link = f"[link]({src_url})" if src_url else ""
            else:
                title = (meta.get("title") or wk.name).replace("|", "\\|").strip()
                owner = meta.get("owner", "makeovermonday")
                ds_id = meta.get("id", "")
                src_cell = "data.world"
                files_txt = "<br>".join(f"`{f}`" for f in files) if files else "_none_"
                source_link = (
                    f"[data.world](https://data.world/{owner}/{ds_id})" if ds_id else ""
                )

            links = " · ".join(x for x in [local_link, source_link] if x)
            lines.append(f"| {wk_label} | {title} | {src_cell} | {files_txt} | {links} |")
        lines.append("")

    summary = f"_Archive contains **{total_weeks} weeks** across **{len(years)} year(s)**, {total_files} data file(s)._\n"
    return "## Archive index\n\n" + summary + "\n" + "\n".join(lines)


def write_into_readme(readme_path: Path, index_md: str) -> None:
    block = f"{START}\n{index_md}\n{END}"
    if readme_path.exists():
        text = readme_path.read_text(encoding="utf-8")
    else:
        text = "# Makeover Monday Archive\n\n"
    if START in text and END in text:
        # count=1 + a function replacement: only the FIRST marker block is
        # replaced, and the replacement is inserted literally (so table text
        # containing backslashes or group-like sequences is never interpreted).
        text = re.sub(
            re.escape(START) + r".*?" + re.escape(END),
            lambda _m: block,
            text,
            count=1,
            flags=re.DOTALL,
        )
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    readme_path.write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate the archive index into README.md")
    ap.add_argument("--output", type=Path, default=Path("output"))
    ap.add_argument("--readme", type=Path, default=Path("README.md"))
    args = ap.parse_args(argv)

    if not args.output.is_dir():
        print(f"[ERROR] Output folder not found: {args.output}")
        return 2

    index_md = build_index(args.output)
    write_into_readme(args.readme, index_md)
    print(f"[INFO] Index written into {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
