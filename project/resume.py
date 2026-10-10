"""Resume parsing into a nested draft, and flattening into citable entries.

A resume is parsed by a structured model call (not by the chat agent) into
items such as roles, degrees, and projects, each with its bullet-point details,
plus a flat skills list. When saved, each item becomes one experience entry
and each detail becomes a child entry pointing at it through ``parent_id``.
Every line stays individually citable, but displays as a nested list.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from llm import ask

ITEM_CATEGORIES = ("work", "project", "education", "leadership", "award", "other")


class ResumeDetail(BaseModel):
    text: str
    skills: list[str]


class ResumeItem(BaseModel):
    category: Literal["work", "project", "education", "leadership", "award", "other"]
    text: str
    organization: str | None
    dates: str | None
    skills: list[str]
    details: list[ResumeDetail]


class ResumeDraft(BaseModel):
    items: list[ResumeItem]
    skills: list[str]


async def parse_resume_text(text: str) -> dict:
    """Parse resume text into a nested draft using the analysis model."""
    draft: ResumeDraft = await ask("analysis", "resume_parser_instructions.md", text, ResumeDraft)
    return normalize_draft(draft.model_dump())


def _clean_text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_list(values) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(item.strip() for item in values if isinstance(item, str) and item.strip()))


def normalize_draft(draft) -> dict:
    """Validate a parsed or user-edited draft, dropping empty lines and fixing types."""
    if not isinstance(draft, dict):
        raise ValueError("The resume draft must be an object with items and skills.")
    items = []
    for raw in draft.get("items") or []:
        if not isinstance(raw, dict):
            raise ValueError("Each resume item must be an object.")
        text = _clean_text(raw.get("text"))
        details = []
        for detail in raw.get("details") or []:
            if isinstance(detail, str):
                detail = {"text": detail, "skills": []}
            if isinstance(detail, dict) and _clean_text(detail.get("text")):
                details.append({"text": detail["text"].strip(), "skills": _clean_list(detail.get("skills"))})
        if not text:
            if details:
                raise ValueError("Every resume item with details needs a headline.")
            continue
        category = raw.get("category") if raw.get("category") in ITEM_CATEGORIES else "other"
        items.append({
            "category": category,
            "text": text,
            "organization": _clean_text(raw.get("organization")),
            "dates": _clean_text(raw.get("dates")),
            "skills": _clean_list(raw.get("skills")),
            "details": details,
        })
    skills = _clean_list(draft.get("skills"))
    if not items and not skills:
        raise ValueError("The resume draft is empty.")
    return {"items": items, "skills": skills}


def flatten_draft(draft: dict) -> list[dict]:
    """Turn a nested draft into experience fields; children carry ``parent_index``."""
    entries = []
    for item in draft["items"]:
        parent_index = len(entries)
        entries.append({
            "category": item["category"], "text": item["text"], "skills": item["skills"],
            "organization": item["organization"], "dates": item["dates"],
        })
        for detail in item["details"]:
            entries.append({
                "category": item["category"], "text": detail["text"], "skills": detail["skills"],
                "organization": item["organization"], "dates": item["dates"],
                "parent_index": parent_index,
            })
    for skill in draft["skills"]:
        entries.append({"category": "skill", "text": skill, "skills": [skill], "organization": None, "dates": None})
    return entries


def draft_summary(draft: dict) -> dict:
    """Short description of a draft for the chat model."""
    return {
        "items": [
            {"category": item["category"], "headline": item["text"], "details": len(item["details"])}
            for item in draft["items"]
        ],
        "skills": draft["skills"],
        "total_entries": len(flatten_draft(draft)),
    }
