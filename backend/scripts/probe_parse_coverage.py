"""How much of each page actually survives parsing?

``parse_pdf`` walks a page top to bottom, carving prose out of the vertical gaps
*between* tables. That preserves reading order on a single-column page, which is
what it was written for. On a multi-column page it does two things nobody
measured:

1. Words from different columns that share a y-baseline are joined into one
   line, because a band is cropped at full page width and lines are grouped by
   vertical position alone.
2. Any text sitting *beside* a table — same vertical range, different column —
   is skipped outright, because the band before the table ends at ``table.top``
   and the band after it starts at ``table.bottom``.

The second is silent data loss, and this probe puts a number on it: for every
page, what fraction of the words pdfplumber can see end up in an emitted block?

Coverage is measured on a token multiset rather than on offsets, because a
table's words are re-emitted as Markdown and their positions legitimately move.
A token the page has and no block has was *dropped*.

    PYTHONPATH=. poetry run python scripts/probe_parse_coverage.py
    PYTHONPATH=. poetry run python scripts/probe_parse_coverage.py --pages 7
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

import pdfplumber

from app.ingest.parse import _X_TOLERANCE_RATIO, parse_pdf
from evals.corpus import CORPUS

CORPUS_DIR = pathlib.Path(__file__).resolve().parents[2] / "document corpus"

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,%\-/]*")


def tokens(text: str) -> collections.Counter[str]:
    """Words, lowercased. Table markdown adds pipes and dashes; those are not
    content and must not count as either found or missing."""
    return collections.Counter(
        t.lower() for t in _TOKEN.findall(text) if t not in {"---", "-"}
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", help="comma-separated page numbers to detail")
    parser.add_argument("--doc", help="only this corpus key")
    args = parser.parse_args()
    detail = {int(p) for p in args.pages.split(",")} if args.pages else set()

    grand_page, grand_lost = 0, 0
    for entry in CORPUS:
        if args.doc and entry.key != args.doc:
            continue
        path = CORPUS_DIR / entry.filename
        if path.suffix != ".pdf":
            continue

        data = path.read_bytes()
        result = parse_pdf(data)
        emitted: dict[int, list[str]] = collections.defaultdict(list)
        for block in result.blocks:
            if block.page is not None:
                emitted[block.page].append(block.text)

        print(f"\n{entry.key}  ({entry.filename})")
        print(f"  {'page':>4} {'words':>7} {'kept':>7} {'lost':>6} {'cover':>7}")
        doc_page, doc_lost = 0, 0
        worst: list[tuple[float, int, list[str]]] = []

        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance_ratio=_X_TOLERANCE_RATIO) or []
                have = tokens(" ".join(w["text"] for w in words))
                got = tokens(" ".join(emitted.get(page.page_number, [])))
                # Multiset difference: tokens the page has that no block has.
                missing = have - got
                n_have, n_missing = sum(have.values()), sum(missing.values())
                if not n_have:
                    continue
                cover = 1 - n_missing / n_have
                doc_page += n_have
                doc_lost += n_missing
                flag = "  <-- " + ", ".join(
                    t for t, _ in missing.most_common(6)
                ) if cover < 0.9 else ""
                print(
                    f"  {page.page_number:>4} {n_have:>7} {n_have - n_missing:>7} "
                    f"{n_missing:>6} {cover:>6.1%}{flag}"
                )
                worst.append((cover, page.page_number, [t for t, _ in missing.most_common(12)]))

                if page.page_number in detail:
                    print(f"    all {n_missing} dropped tokens on page {page.page_number}:")
                    print("      " + " ".join(missing.elements()))

        grand_page += doc_page
        grand_lost += doc_lost
        print(f"  {'TOTAL':>4} {doc_page:>7} {doc_page - doc_lost:>7} {doc_lost:>6} "
              f"{1 - doc_lost / doc_page:>6.1%}")
        worst.sort()
        if worst and worst[0][0] < 0.95:
            print(f"  worst page {worst[0][1]} at {worst[0][0]:.1%}: {worst[0][2]}")

    if grand_page:
        print(f"\ncorpus-wide: {grand_page - grand_lost}/{grand_page} words kept "
              f"({1 - grand_lost / grand_page:.1%}), {grand_lost} dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
