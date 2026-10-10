"""Model judgment on duplicate candidates found by code (plan section 6).

Code finds candidates by normalized company and shared title words. A
structured model call then labels each candidate. The user always decides:
save as new, link to an existing posting, or skip. Nothing is merged.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from llm import ask, run_blocking
from storage import default_store

JUDGE_TIMEOUT_SECONDS = 45


class DuplicateJudgment(BaseModel):
    job_id: str
    judgment: Literal["likely same posting", "same company, different role", "unrelated"]
    reason: str


class DuplicateJudgments(BaseModel):
    judgments: list[DuplicateJudgment]


def _posting_view(posting: dict) -> dict:
    return {
        "title": posting.get("title"),
        "company": posting.get("company"),
        "department": posting.get("department"),
        "location": posting.get("location"),
        "date_posted": posting.get("date_posted"),
        "requirements": [
            req["text"] if isinstance(req, dict) else str(req) for req in posting.get("requirements") or []
        ],
    }


async def judge_candidates(draft: dict, candidate_ids: list[str]) -> dict[str, dict]:
    """Return {job_id: {judgment, reason}} for each candidate the model judged."""
    store = default_store()
    payload = {
        "new_posting": _posting_view(draft),
        "saved_candidates": [{"job_id": job_id, **_posting_view(store.read_posting(job_id))} for job_id in candidate_ids],
    }
    result: DuplicateJudgments = await asyncio.wait_for(
        ask("analysis", "duplicate_judge_instructions.md", payload, DuplicateJudgments),
        JUDGE_TIMEOUT_SECONDS,
    )
    return {
        item.job_id: {"judgment": item.judgment, "reason": item.reason.strip()}
        for item in result.judgments if item.job_id in candidate_ids
    }


def judge_candidates_blocking(draft: dict, candidate_ids: list[str]) -> dict[str, dict]:
    """Synchronous wrapper used while the approval card is being prepared."""
    return run_blocking(judge_candidates(draft, candidate_ids))
