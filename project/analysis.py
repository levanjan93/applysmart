"""Fit analysis with code-validated citations and an independent claim check.

The model judges fit and writes explanations. Code validates every cited ID,
fills in requirements the model skipped, decides what belongs under "Add to
your resume", and saves the result. A second model call, with its own
instructions, rates whether each cited piece of evidence supports its claim.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from llm import ask as _ask, model_names
from preferences import check_preferences
from storage import default_store

MATCHES = ("strong match", "partial match")
_WORD = re.compile(r"[a-z0-9+#]+", re.IGNORECASE)


class ClaimDraft(BaseModel):
    requirement_id: str
    verdict: Literal["strong match", "partial match", "gap"]
    evidence: list[str]
    explanation: str


class QuestionDraft(BaseModel):
    requirement_id: str
    question: str


class AnalysisDraft(BaseModel):
    claims: list[ClaimDraft]
    questions: list[QuestionDraft]


class CheckDraft(BaseModel):
    requirement_id: str
    rating: Literal["supported", "stretched", "unsupported"]
    reason: str


class CheckDrafts(BaseModel):
    checks: list[CheckDraft]


def validate_analysis(draft: AnalysisDraft, posting: dict, experience: list[dict]) -> dict:
    """Turn a model draft into claims whose IDs are checked against saved records."""
    requirements = {req["id"]: req for req in posting["requirements"]}
    entries = {entry["id"]: entry for entry in experience}
    drafts: dict[str, ClaimDraft] = {}
    stray = []
    for claim in draft.claims:
        if claim.requirement_id not in requirements:
            stray.append({
                "requirement_id": claim.requirement_id,
                "issue": f"{claim.requirement_id} is not a requirement of {posting['id']}.",
            })
        elif claim.requirement_id not in drafts:
            drafts[claim.requirement_id] = claim

    claims = []
    for req_id, requirement in requirements.items():
        claim = drafts.get(req_id)
        issues = []
        if claim is None:
            verdict, cited, explanation = "gap", [], "The analysis did not assess this requirement."
            issues.append("Not assessed by the model; treated as a gap.")
        else:
            verdict, cited, explanation = claim.verdict, list(dict.fromkeys(claim.evidence)), claim.explanation
        valid_ids = [exp_id for exp_id in cited if exp_id in entries]
        invalid_ids = [exp_id for exp_id in cited if exp_id not in entries]
        issues += [f"{exp_id} does not exist in the experience database." for exp_id in invalid_ids]
        if verdict == "gap":
            valid_ids = []
        elif not valid_ids:
            issues.append("A match must cite at least one existing experience entry.")
        claims.append({
            "requirement_id": req_id,
            "requirement_text": requirement["text"],
            "requirement_type": requirement["type"],
            "verdict": verdict,
            "evidence": valid_ids,
            "invalid_evidence": invalid_ids,
            "explanation": explanation.strip(),
            "valid": verdict == "gap" or (bool(valid_ids) and not invalid_ids),
            "issues": issues,
            "check": None,
            "review": None,
        })

    # Decided by code, not the model: saved user-added facts that are not on the
    # resume yet and that support a valid match.
    add_to_resume: dict[str, dict] = {}
    for claim in claims:
        if claim["verdict"] not in MATCHES or not claim["valid"]:
            continue
        for exp_id in claim["evidence"]:
            entry = entries[exp_id]
            if entry["source"] == "user-added" and not entry["on_resume"]:
                item = add_to_resume.setdefault(exp_id, {"exp_id": exp_id, "text": entry["text"], "requirement_ids": []})
                item["requirement_ids"].append(claim["requirement_id"])

    questions = [
        {"requirement_id": q.requirement_id, "question": q.question.strip()}
        for q in draft.questions
        if q.requirement_id in requirements and q.question.strip()
    ]
    return {
        "claims": claims,
        "add_to_resume": list(add_to_resume.values()),
        "questions": questions,
        "invalid_claims": stray,
    }


async def check_analysis(analysis: dict, experience: list[dict]) -> dict:
    """Rate each match claim's support with the separate checker model."""
    entries = {entry["id"]: entry for entry in experience}
    to_check = []
    for claim in analysis["claims"]:
        if claim["verdict"] not in MATCHES:
            claim["check"] = None
        elif not claim["evidence"]:
            claim["check"] = {
                "rating": "unsupported", "reason": "No existing experience entry was cited.", "by": "code",
            }
        else:
            to_check.append(claim)
    if to_check:
        payload = {"claims": [
            {
                "requirement_id": claim["requirement_id"],
                "requirement": claim["requirement_text"],
                "claimed_verdict": claim["verdict"],
                "evidence": [
                    {"id": exp_id, "text": entries[exp_id]["text"]}
                    for exp_id in claim["evidence"] if exp_id in entries
                ],
            }
            for claim in to_check
        ]}
        try:
            result: CheckDrafts = await _ask("check", "checker_instructions.md", payload, CheckDrafts)
        except Exception as exc:
            analysis["check_status"] = f"failed: {type(exc).__name__}"
            for claim in to_check:
                claim["check"] = None
            return analysis
        ratings = {check.requirement_id: check for check in result.checks}
        for claim in to_check:
            check = ratings.get(claim["requirement_id"])
            claim["check"] = (
                {"rating": check.rating, "reason": check.reason.strip(), "by": "model"} if check else None
            )
    analysis["check_status"] = "done"
    analysis["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return analysis


async def run_fit_analysis(job_id: str) -> dict:
    """Analyze fit, validate citations, check claims, and save the result."""
    store = default_store()
    posting = store.read_posting(job_id)
    if not posting["requirements"]:
        raise ValueError(f"{job_id} has no requirements to analyze.")
    experience = store.read_experience()
    if not experience:
        raise ValueError("The experience database is empty. Import a resume or add experience first.")
    instructions = store.read_analysis_instructions()
    payload = {
        "posting": {"id": posting["id"], "title": posting["title"], "company": posting["company"]},
        "requirements": posting["requirements"],
        "experience": [
            {key: entry.get(key) for key in ("id", "parent_id", "category", "text", "skills", "organization", "dates", "source", "on_resume")}
            for entry in experience
        ],
        "user_analysis_preferences": instructions,
    }
    draft: AnalysisDraft = await _ask("analysis", "analyst_instructions.md", payload, AnalysisDraft)
    analysis = {
        "job_id": posting["id"],
        "job_title": posting["title"],
        "company": posting["company"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": model_names(),
        **validate_analysis(draft, posting, experience),
    }
    await check_analysis(analysis, experience)
    try:
        raw_text = store.read_raw_text(posting["id"])
    except Exception:
        raw_text = ""
    # The user's own requirements (salary, job type, deal-breakers), as they
    # stood when this analysis ran.
    analysis["preference_checks"] = await check_preferences(
        posting, raw_text, store.read_job_preferences(), ask=_ask,
    )
    analysis_id = store.save_analysis(posting["id"], analysis)
    return store.read_analysis(analysis_id)


async def recheck_analysis(analysis_id: str) -> dict:
    """Run the independent check again on a saved analysis."""
    store = default_store()
    analysis = store.read_analysis(analysis_id)
    await check_analysis(analysis, store.read_experience())
    store.write_analysis(analysis_id, analysis)
    return analysis


def rating_rank(claim: dict) -> int:
    """Sort key that puts claims worth the user's attention first."""
    if not claim["valid"]:
        return 0
    rating = (claim.get("check") or {}).get("rating")
    return {"unsupported": 1, "stretched": 2, None: 3, "supported": 4}.get(rating, 3)


def agent_summary(analysis: dict) -> dict:
    """Compact analysis for the chat model, plus the user's formatting instructions."""
    claims = sorted(analysis["claims"], key=rating_rank)
    return {
        "analysis_id": analysis["analysis_id"],
        "job": f"{analysis['job_id']} ({analysis['job_title']}, {analysis['company']})",
        "claims": [
            {
                "requirement_id": claim["requirement_id"],
                "requirement": claim["requirement_text"],
                "type": claim["requirement_type"],
                "verdict": claim["verdict"],
                "evidence": claim["evidence"],
                "explanation": claim["explanation"],
                "check": claim["check"],
                "valid": claim["valid"],
                "issues": claim["issues"],
            }
            for claim in claims
        ],
        "preference_checks": analysis.get("preference_checks", []),
        "add_to_resume": analysis["add_to_resume"],
        "questions": analysis["questions"],
        "invalid_claims": analysis["invalid_claims"],
        "check_status": analysis.get("check_status"),
        "format_instructions": default_store().read_analysis_instructions(),
    }


def locate_requirement(raw_text: str, requirement: str) -> int | None:
    """Return the index of the raw posting line that best contains a requirement."""
    wanted = {word.casefold() for word in _WORD.findall(requirement) if len(word) > 2}
    if not wanted:
        return None
    best, best_score = None, 0.0
    for index, line in enumerate(raw_text.splitlines()):
        words = {word.casefold() for word in _WORD.findall(line)}
        score = len(wanted & words) / len(wanted)
        if score > best_score:
            best, best_score = index, score
    return best if best_score >= 0.6 else None


def evidence_view(analysis_id: str) -> dict:
    """Everything the verification panel needs: claims, posting text, cited entries."""
    store = default_store()
    analysis = store.read_analysis(analysis_id)
    try:
        raw_text = store.read_raw_text(analysis["job_id"])
    except Exception:
        raw_text = ""
    entries = {entry["id"]: entry for entry in store.read_experience()}
    cited = {exp_id for claim in analysis["claims"] for exp_id in claim["evidence"]}
    cited |= {item["exp_id"] for item in analysis["add_to_resume"]}
    for claim in analysis["claims"]:
        claim["raw_line"] = locate_requirement(raw_text, claim["requirement_text"]) if raw_text else None
    analysis["claims"].sort(key=rating_rank)
    return {
        "analysis": analysis,
        "raw_lines": raw_text.splitlines(),
        "experience": {exp_id: entries.get(exp_id) for exp_id in sorted(cited)},
    }
