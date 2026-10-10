"""Job-search tools. Deterministic persistence stays in :mod:`storage`."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from calendar import monthrange
from datetime import date, timedelta
from typing import Literal, TypedDict

from agents import function_tool

import analysis
import resume
from storage import default_store, normalize_company, title_keywords

_title_words = title_keywords
from uploads import resume_uploads


_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)


class RequirementFields(TypedDict):
    """One extracted requirement. Python assigns its REQ ID after approval."""

    text: str
    type: Literal["required", "preferred"]


SAVE_FAILED = "SAVE FAILED"


def save_failed(_ctx, error: Exception) -> str:
    """Tell the model plainly that an approved write did not happen."""
    return (
        f"{SAVE_FAILED}: nothing was saved ({type(error).__name__}: {error}). "
        "Tell the user the save failed and give this reason. Do not claim it was saved."
    )


_RELATIVE_DATE = re.compile(r"^(\d+)\s+(day|week|month|year)s?\s+ago$", re.IGNORECASE)


def normalize_posted_date(value: str | None, *, today: date | None = None) -> tuple[str | None, bool]:
    """Normalize an ISO or relative posting date using the local date."""
    if value is None or not value.strip():
        return None, True
    current = today or date.today()
    candidate = value.strip().casefold()
    if candidate == "today":
        return current.isoformat(), False
    if candidate == "yesterday":
        return (current - timedelta(days=1)).isoformat(), False
    relative = _RELATIVE_DATE.fullmatch(candidate)
    if relative:
        count = int(relative.group(1))
        unit = relative.group(2).casefold()
        if unit == "day":
            result = current - timedelta(days=count)
        elif unit == "week":
            result = current - timedelta(weeks=count)
        else:
            months = count if unit == "month" else count * 12
            month_index = current.year * 12 + current.month - 1 - months
            year, month0 = divmod(month_index, 12)
            month = month0 + 1
            result = date(year, month, min(current.day, monthrange(year, month)[1]))
        return result.isoformat(), True
    try:
        return date.fromisoformat(value.strip()).isoformat(), False
    except ValueError:
        # Unknown date phrases are not guessed. Storage fills today's date and
        # marks it as an estimate when date_posted is null.
        return None, True


def duplicate_candidates(company: str, title: str) -> list[dict]:
    """Find saved postings with the same normalized company and title overlap."""
    target_company = normalize_company(company)
    target_title = title_keywords(title)
    if not target_company or not target_title:
        return []
    matches = []
    for posting in default_store().list_postings():
        if normalize_company(posting["company"]) != target_company:
            continue
        overlap = sorted(target_title & title_keywords(posting["title"]))
        if overlap:
            matches.append({
                "id": posting["id"],
                "title": posting["title"],
                "company": posting["company"],
                "date_added": posting["date_added"],
                "status": posting["status"],
                "overlapping_title_keywords": overlap,
            })
    return matches


# User messages from the current conversation. The runtime sets these before each
# turn so a saved posting keeps the user's own pasted text, never the model's copy.
_user_messages: list[str] = []


def set_user_messages(messages: list[str]) -> None:
    """Record the user's messages for this turn (called by the runtime)."""
    _user_messages[:] = [text for text in messages if isinstance(text, str)]


def find_pasted_text(title: str, company: str) -> str | None:
    """Return the user's own message that contains this posting, exactly as typed.

    Scores each user message by the posting's title words and company words it
    contains; the best match wins and ties go to the most recent message.
    """
    title_words = title_keywords(title)
    company_words = set(normalize_company(company).split())
    best, best_score = None, 0
    for text in _user_messages:
        words = {word.casefold() for word in _WORD.findall(text)}
        title_hits = len(title_words & words)
        company_hit = bool(company_words) and company_words <= words
        if not company_hit and title_hits * 2 < max(len(title_words), 1):
            continue
        score = title_hits + (2 if company_hit else 0)
        if score >= best_score and score > 0:
            best, best_score = text, score
    return best


@function_tool
def list_postings(status: str | None = None) -> list[dict]:
    """List saved jobs as short ID, title, company, date, and status summaries."""
    return default_store().list_postings(status=status)


@function_tool
def read_posting(job_id: str) -> dict:
    """Read the structured record for a saved posting such as JOB-0001."""
    return default_store().read_posting(job_id)


@function_tool
def search_postings(
    company: str | None = None,
    department: str | None = None,
    title_keywords: str | None = None,
    status: str | None = None,
) -> dict:
    """Search saved postings by company, department, title words, or status.

    Company names match across variants ("DBS", "DBS Bank Ltd", "dbs bank").
    Each match includes its status and when it was applied to, from the tracking
    log. When nothing matches, ``near_misses`` lists similar postings.
    """
    return search_with_history(company, department, title_keywords, status)


def search_with_history(company=None, department=None, title_words=None, status=None) -> dict:
    """Search postings, add tracking dates, and suggest near misses when empty."""
    store = default_store()
    matches = store.search_postings(
        company=company, department=department, title_keywords=title_words, status=status,
    )
    for match in matches:
        match.update(application_dates(store.read_tracking_log(match["id"])))
    near_misses = []
    if not matches and (company or title_words):
        wanted_company = normalize_company(company or "")
        wanted_words = _title_words(title_words or "")
        for posting in store.list_postings():
            company_score = (
                SequenceMatcher(None, wanted_company, normalize_company(posting["company"])).ratio()
                if wanted_company else 0.0
            )
            shared = sorted(wanted_words & _title_words(posting["title"]))
            if company_score >= 0.6 or shared:
                near_misses.append({**posting, "company_similarity": round(company_score, 2), "shared_title_words": shared})
    return {"matches": matches, "near_misses": near_misses[:5]}


def application_dates(events: list[dict]) -> dict:
    """Summarize when a posting was added, applied to, and last changed."""
    applied = [e["timestamp"][:10] for e in events if e.get("event") == "status_changed" and e["details"].get("to") == "applied"]
    changes = [e for e in events if e.get("event") == "status_changed"]
    return {
        "applied_on": applied[-1] if applied else None,
        "last_status_change": (
            {"date": changes[-1]["timestamp"][:10], "from": changes[-1]["details"]["from"], "to": changes[-1]["details"]["to"]}
            if changes else None
        ),
    }


@function_tool
def update_status(job_id: str, new_status: Literal["saved", "applied", "interviewing", "offer", "rejected", "withdrawn"], note: str | None = None) -> dict:
    """Change a saved posting's application status and record it in the tracking log.

    Use only when the user clearly states what happened to a specific job, for
    example "I applied to JOB-0003". If the job is ambiguous, ask first.
    """
    store = default_store()
    before = store.read_posting(job_id)["status"]
    posting = store.update_status(job_id, new_status, note)
    return {
        "saved": True,
        "status_change": {"job_id": job_id, "title": posting["title"], "company": posting["company"], "from": before, "to": posting["status"]},
    }


@function_tool
def read_tracking_log(job_id: str | None = None) -> list[dict]:
    """Read the application tracking log (added, status changes, notes), oldest first."""
    return default_store().read_tracking_log(job_id)


@function_tool
def read_experience(category: str | None = None, source: str | None = None) -> list[dict]:
    """Read saved experience facts, optionally filtered by category or source."""
    return default_store().read_experience(category=category, source=source)


@function_tool
def search_experience(keywords: str) -> list[dict]:
    """Search experience text, skills, and organizations for all supplied words."""
    return default_store().search_experience(keywords)


@function_tool
async def parse_resume(upload_id: str) -> dict:
    """Parse a resume selected through the browser upload control into a draft.

    Code runs a structured parser that keeps the resume's categories and nests
    bullet points under their role, degree, or project. The draft is held with
    the upload; call ``import_resume`` with the same upload ID to show it for
    approval. This tool cannot accept or access a filesystem path.
    """
    upload = resume_uploads.get(upload_id)
    draft = await resume.parse_resume_text(upload.text)
    resume_uploads.set_draft(upload_id, draft)
    return {"upload_id": upload_id, "original_name": upload.original_name, **resume.draft_summary(draft)}


_REQUIREMENT_PREFIX = re.compile(r"^\s*\(?(required|preferred)\)?\s*[:\-–]\s*", re.IGNORECASE)
_REQUIREMENT_SUFFIX = re.compile(r"\s*[\(\[](required|preferred)[\)\]]\s*$", re.IGNORECASE)


def normalize_requirements(requirements: list[RequirementFields | str] | None) -> list[dict]:
    """Accept requirement objects or plain strings such as "preferred: Banking knowledge".

    Some models send strings despite the schema. A leading "required:" or
    "preferred:" label, or a trailing "(required)" or "(preferred)", sets the
    type and is removed from the text; otherwise the requirement is required.
    """
    normalized = []
    for item in requirements or []:
        if isinstance(item, str):
            text, kind = item.strip(), "required"
            label = _REQUIREMENT_PREFIX.match(text) or _REQUIREMENT_SUFFIX.search(text)
            if label:
                kind = label.group(1).casefold()
                text = (text[label.end():] if label.start() == 0 else text[:label.start()]).strip()
            normalized.append({"text": text, "type": kind})
        elif isinstance(item, dict):
            normalized.append(dict(item))
        else:
            raise ValueError("Each requirement must be an object or a text string.")
    return normalized


def import_resume_action(upload_id: str, draft: dict | None = None) -> dict:
    """Save a parsed resume draft (or the user's edited draft) as experience entries."""
    upload = resume_uploads.get(upload_id)
    if draft is None:
        draft = resume_uploads.draft(upload_id)
        if draft is None:
            raise ValueError("This resume has not been parsed yet. Call parse_resume first.")
    saved = default_store().import_resume_data(
        resume.flatten_draft(resume.normalize_draft(draft)), upload.original_name, upload.content,
    )
    resume_uploads.discard(upload_id)
    return {"saved": True, **saved}


def import_resume_tool(upload_id: str) -> dict:
    """Save the parsed resume draft for this upload after the user approves it.

    Call ``parse_resume`` first. The approval card shows every item and detail;
    the user may edit, remove, or add lines before saving.
    """
    return import_resume_action(upload_id)


def propose_experience_action(
    text: str,
    category: Literal["work", "project", "education", "skill", "leadership", "award", "other"],
    skills: list[str],
    organization: str | None = None,
    dates: str | None = None,
) -> dict:
    """Propose one user-stated atomic experience fact for approval.

    Keep the user's level of responsibility and impact exactly as stated. Never
    turn participation into leadership or add metrics, outcomes, or skills.
    """
    saved = default_store().add_experience({
        "source": "user-added", "on_resume": False, "category": category,
        "text": text, "skills": skills, "organization": organization, "dates": dates,
    })
    return {"saved": True, "entry": saved}


def update_experience_action(
    exp_id: str,
    text: str,
    category: Literal["work", "project", "education", "skill", "leadership", "award", "other"],
    skills: list[str],
    organization: str | None = None,
    dates: str | None = None,
    on_resume: bool | None = None,
) -> dict:
    """Replace an existing experience entry after approval of the exact fields."""
    updates = {
        "text": text, "category": category, "skills": skills,
        "organization": organization, "dates": dates,
    }
    if on_resume is not None:
        updates["on_resume"] = on_resume
    updated = default_store().update_experience(exp_id, updates)
    return {"saved": True, "entry": updated}


def delete_experience_action(exp_id: str) -> dict:
    """Permanently delete one saved experience entry after the user approves.

    Use when the user asks to remove an entry, for example one added by mistake.
    If it has nested details, they are kept as standalone entries. The approval
    card shows exactly what will be deleted.
    """
    store = default_store()
    entries = store.read_experience()
    entry = next((item for item in entries if item["id"] == exp_id), None)
    if entry is None:
        raise ValueError(f"No experience entry with id {exp_id}.")
    detached = [item["id"] for item in entries if item.get("parent_id") == exp_id]
    store.delete_experience(exp_id)
    return {"saved": True, "deleted": entry, "detached_details": detached}


@function_tool
def find_duplicate_candidates(company: str, title: str) -> list[dict]:
    """Find possible duplicate jobs by normalized company and overlapping title words.

    This is a candidate search only. Compare each result with the draft and explain
    the match to the user. Never merge, skip, or link records without the user's choice.
    """
    return duplicate_candidates(company, title)


def add_posting_action(
    source: str,
    title: str,
    company: str,
    location: str,
    requirements: list[RequirementFields | str],
    responsibilities: list[str],
    department: str | None = None,
    employment_type: str | None = None,
    source_url: str | None = None,
    date_posted: str | None = None,
    date_posted_is_estimate: bool = False,
    salary: str | None = None,
    notes: str | None = None,
    related_postings: list[str] | None = None,
) -> dict:
    """Save an extracted job posting after explicit human approval.

    Before calling, extract a clean posting from the user's supplied text and run
    ``find_duplicate_candidates``. The approval screen shows these exact fields.
    Exclude page chrome and all recruiter or other people's names/profile details.
    Do not pass the source text: code saves the user's own pasted message
    verbatim beside the record. Python generates the JOB ID, date added, and REQ IDs.
    Leave ``related_postings`` empty; the user links duplicates on the approval card.
    """
    raw_text = find_pasted_text(title, company)
    if raw_text is None:
        raise ValueError(
            "The pasted posting text could not be found in this conversation. "
            "Ask the user to paste the posting again."
        )
    normalized_date, is_estimate = normalize_posted_date(date_posted)
    fields = {
        "title": title,
        "company": company,
        "department": department,
        "location": location,
        "employment_type": employment_type,
        "source": source,
        "source_url": source_url,
        "date_posted": normalized_date,
        "date_posted_is_estimate": (is_estimate or date_posted_is_estimate) if date_posted else True,
        "requirements": normalize_requirements(requirements),
        "responsibilities": responsibilities,
        "salary": salary,
        "notes": notes,
        "related_postings": related_postings or [],
    }
    saved = default_store().create_posting(fields, raw_text)
    return {"saved": True, "posting": saved}


@function_tool
async def analyze_fit(job_id: str) -> dict:
    """Analyze how the user's saved experience fits a saved posting such as JOB-0001.

    Every claim cites REQ and EXP IDs that code has validated, and a separate
    checker has already rated each match as supported, stretched, or unsupported.
    The result is saved and shown in the evidence review panel. Write your reply
    from this result only, following its ``format_instructions``.
    """
    return analysis.agent_summary(await analysis.run_fit_analysis(job_id))


@function_tool
async def check_claims(analysis_id: str) -> dict:
    """Re-run the independent claim check on a saved analysis and return the ratings."""
    return analysis.agent_summary(await analysis.recheck_analysis(analysis_id))


def propose_analysis_instructions_action(new_text: str, reason: str) -> dict:
    """Replace the user's analysis formatting instructions after approval.

    Use only after the user has corrected the analysis style more than once
    (for example "shorter" or "put gaps first"). Pass the complete new file
    text; the approval card shows a before/after diff.
    """
    store = default_store()
    before = store.read_analysis_instructions()
    if isinstance(new_text, str) and not new_text.endswith("\n"):
        new_text += "\n"
    store.write_analysis_instructions(new_text)
    return {"saved": True, "instructions_updated": True, "reason": reason, "changed": before != new_text}


def _write_tool(action, name: str):
    """Register a write action as an SDK tool that pauses for human approval."""
    return function_tool(
        action, name_override=name, needs_approval=True, failure_error_function=save_failed,
    )


# Plain write functions by tool name. The SDK tools below call these after an
# approval; an edited approval calls them directly with the user's exact version.
WRITE_ACTIONS = {
    "add_posting": add_posting_action,
    "import_resume": import_resume_action,
    "propose_experience": propose_experience_action,
    "update_experience": update_experience_action,
    "delete_experience": delete_experience_action,
    "propose_analysis_instructions": propose_analysis_instructions_action,
}
add_posting = _write_tool(add_posting_action, "add_posting")
import_resume = _write_tool(import_resume_tool, "import_resume")
propose_experience = _write_tool(propose_experience_action, "propose_experience")
update_experience = _write_tool(update_experience_action, "update_experience")
delete_experience = _write_tool(delete_experience_action, "delete_experience")
propose_analysis_instructions = _write_tool(propose_analysis_instructions_action, "propose_analysis_instructions")


def execute_edited(tool_name: str, arguments: dict) -> dict:
    """Save the user's edited arguments with the same code an approved call runs."""
    action = WRITE_ACTIONS.get(tool_name)
    if action is None:
        raise ValueError(f"{tool_name} cannot be edited.")
    if not isinstance(arguments, dict):
        raise ValueError("Edited arguments must be an object.")
    try:
        return action(**arguments)
    except TypeError as exc:
        raise ValueError(f"The edited fields do not match {tool_name}: {exc}") from exc


def approval_details(interruption) -> dict:
    """Decode SDK approval arguments for a reviewable terminal confirmation."""
    arguments = getattr(interruption, "arguments", None)
    if isinstance(arguments, str):
        import json
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {"arguments": arguments}
    return arguments if isinstance(arguments, dict) else {"arguments": arguments}


def approval_tool_name(interruption) -> str:
    """Return the SDK tool name attached to an approval interruption."""
    name = getattr(interruption, "tool_name", None) or getattr(interruption, "name", None)
    if not name:
        name = getattr(getattr(interruption, "raw_item", None), "name", None)
    return str(name or "write_tool")


__all__ = [
    "SAVE_FAILED", "WRITE_ACTIONS", "add_posting", "delete_experience", "propose_analysis_instructions",
    "read_tracking_log", "search_with_history", "update_status", "analyze_fit", "check_claims", "approval_details", "approval_tool_name",
    "duplicate_candidates", "execute_edited", "save_failed",
    "find_duplicate_candidates", "find_pasted_text", "list_postings", "normalize_company",
    "normalize_posted_date", "normalize_requirements", "import_resume",
    "propose_experience", "read_experience", "read_posting", "parse_resume",
    "search_experience", "search_postings", "set_user_messages", "title_keywords",
    "update_experience",
]
