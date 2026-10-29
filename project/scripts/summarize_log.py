"""Summarize data/interaction_log.jsonl into counts for the evaluation.

Run from the project folder:  python scripts/summarize_log.py  [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import default_store  # noqa: E402

APPROVAL_DECISIONS = ("approve", "edit", "link", "reject")


def summarize(records: list[dict]) -> dict:
    """Group human decisions into approval, claim-review, and direct-edit summaries."""
    approvals: dict[str, Counter] = defaultdict(Counter)
    approval_times: dict[str, list[float]] = defaultdict(list)
    reviews: dict[str, Counter] = defaultdict(Counter)
    review_times: list[float] = []
    direct: Counter = Counter()
    clarifications: Counter = Counter()
    clarification_times: list[float] = []
    for record in records:
        kind = record.get("event_type")
        seconds = record.get("time_to_decision_seconds")
        if kind == "approval":
            approvals[record.get("item_type", "?")][record.get("decision", "?")] += 1
            if isinstance(seconds, (int, float)):
                approval_times[record.get("item_type", "?")].append(seconds)
        elif kind == "claim_review":
            before = record.get("before") or {}
            rating = "invalid" if before.get("valid") is False else ((before.get("check") or {}).get("rating") or "not checked")
            reviews[rating][record.get("decision", "?")] += 1
            if isinstance(seconds, (int, float)):
                review_times.append(seconds)
        elif kind == "clarification":
            clarifications[record.get("decision", "?")] += 1
            if isinstance(seconds, (int, float)):
                clarification_times.append(seconds)
        elif kind == "direct_edit":
            direct[f"{record.get('item_type', '?')} {record.get('decision', '?')}"] += 1

    approval_rows = []
    for item_type in sorted(approvals):
        counts = approvals[item_type]
        total = sum(counts.values())
        times = approval_times[item_type]
        approval_rows.append({
            "item_type": item_type,
            "total": total,
            **{decision: counts.get(decision, 0) for decision in APPROVAL_DECISIONS},
            "changed_by_user_rate": round((counts.get("edit", 0) + counts.get("link", 0)) / total, 2) if total else None,
            "rejected_rate": round(counts.get("reject", 0) / total, 2) if total else None,
            "median_seconds": round(median(times), 1) if times else None,
        })
    review_rows = []
    for rating in ("supported", "stretched", "unsupported", "invalid", "not checked"):
        if rating in reviews:
            counts = reviews[rating]
            total = sum(counts.values())
            review_rows.append({
                "checker_rating": rating, "total": total,
                "agree": counts.get("agree", 0), "disagree": counts.get("disagree", 0),
                "disagree_rate": round(counts.get("disagree", 0) / total, 2),
            })
    return {
        "records": len(records),
        "approvals": approval_rows,
        "claim_reviews": review_rows,
        "claim_review_mean_seconds": round(mean(review_times), 1) if review_times else None,
        "clarifications": {
            "asked": sum(clarifications.values()),
            "answered": clarifications.get("answer", 0),
            "skipped": clarifications.get("skip", 0),
            "median_seconds": round(median(clarification_times), 1) if clarification_times else None,
        },
        "direct_edits": dict(sorted(direct.items())),
    }


def print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("  (none)")
        return
    widths = {c: max(len(c), *(len("—" if r[c] is None else str(r[c])) for r in rows)) for c in columns}
    print("  " + "  ".join(c.ljust(widths[c]) for c in columns))
    for row in rows:
        print("  " + "  ".join(("—" if row[c] is None else str(row[c])).ljust(widths[c]) for c in columns))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args()
    summary = summarize(default_store().read_interactions())
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"Interaction log: {summary['records']} decisions\n")
    print("Approval cards (what the assistant proposed, and what you did)")
    print_table(summary["approvals"], ["item_type", "total", *APPROVAL_DECISIONS, "changed_by_user_rate", "rejected_rate", "median_seconds"])
    print("\nFit-claim reviews, by the independent checker's rating")
    print("  (disagreeing with 'stretched' or 'unsupported' claims shows flagged claims being caught)")
    print_table(summary["claim_reviews"], ["checker_rating", "total", "agree", "disagree", "disagree_rate"])
    if summary["claim_review_mean_seconds"] is not None:
        print(f"  mean time to decide: {summary['claim_review_mean_seconds']} s")
    asked = summary["clarifications"]
    print("\nClarifying questions the assistant asked (ask_user)")
    if asked["asked"]:
        print(f"  asked {asked['asked']}, answered {asked['answered']}, skipped {asked['skipped']}"
              + (f", median {asked['median_seconds']} s to answer" if asked["median_seconds"] is not None else ""))
    else:
        print("  (none)")
    print("\nDirect edits in the Postings and Experience views")
    if summary["direct_edits"]:
        for key, count in summary["direct_edits"].items():
            print(f"  {key}: {count}")
    else:
        print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
