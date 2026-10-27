"""The user's own job requirements and how a saved posting measures up to them.

Code checks what it can read reliably: the monthly salary, the job type, and
the work arrangement. Free-text deal-breakers ("no shift work") need judgment,
so a separate model call rates them, and code verifies each quote it gives.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

HOURS = ("full-time", "part-time")
CONTRACT_TYPES = ("permanent", "contract", "temporary", "internship", "freelance")
WORK_ARRANGEMENTS = ("on-site", "hybrid", "remote")
MAX_DEALBREAKERS = 10

DEFAULT_PREFERENCES = {
    "min_monthly_salary": None,
    "hours": [],
    "contract_types": [],
    "work_arrangements": [],
    "dealbreakers": [],
    "updated_at": None,
}

_PATTERNS = {
    "full-time": r"full[\s-]?time",
    "part-time": r"part[\s-]?time",
    "permanent": r"\bpermanent\b",
    "contract": r"\bcontract(?:ual|or)?\b",
    "temporary": r"\btemp(?:orary)?\b",
    "internship": r"\bintern(?:ship)?\b",
    "freelance": r"\bfree[\s-]?lance(?:r)?\b",
    "on-site": r"\bon[\s-]?site\b|\bin[\s-]office\b|\boffice[\s-]based\b",
    "hybrid": r"\bhybrid\b",
    "remote": r"\bremote\b|\bwork from home\b|\bwfh\b",
}
_EMPTY = {"", "null", "none", "n/a", "na", "-", "—", "not stated", "not specified"}
_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", re.IGNORECASE)
_OTHER_CURRENCY = re.compile(r"\b(?:USD|EUR|GBP|MYR|RM|INR|AUD|HKD|IDR|PHP|THB|CNY|RMB|JPY)\b|US\$|A\$|HK\$|[€£₹¥]")


class PreferenceError(ValueError):
    """The submitted preferences are not valid."""


def _choices(value, allowed: tuple[str, ...], label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(item not in allowed for item in value):
        raise PreferenceError(f"{label} must be a list of: {', '.join(allowed)}.")
    return [item for item in allowed if item in value]


def normalize_preferences(raw) -> dict:
    """Validate submitted preferences and return the record to save."""
    if not isinstance(raw, dict):
        raise PreferenceError("Send the preferences as an object.")
    salary = raw.get("min_monthly_salary")
    if isinstance(salary, str):
        salary = salary.replace(",", "").strip() or None
    if salary is not None:
        try:
            salary = float(salary)
        except (TypeError, ValueError):
            raise PreferenceError("Minimum monthly salary must be a number.") from None
        if isinstance(raw.get("min_monthly_salary"), bool) or not 0 <= salary <= 10_000_000:
            raise PreferenceError("Minimum monthly salary must be a positive number.")
        salary = int(salary) if salary == int(salary) else salary
    dealbreakers = raw.get("dealbreakers") or []
    if not isinstance(dealbreakers, list) or not all(isinstance(item, str) for item in dealbreakers):
        raise PreferenceError("Deal-breakers must be a list of text lines.")
    dealbreakers = list(dict.fromkeys(item.strip() for item in dealbreakers if item.strip()))
    if len(dealbreakers) > MAX_DEALBREAKERS or any(len(item) > 200 for item in dealbreakers):
        raise PreferenceError(f"Use at most {MAX_DEALBREAKERS} deal-breakers of up to 200 characters each.")
    return {
        "min_monthly_salary": salary,
        "hours": _choices(raw.get("hours"), HOURS, "Hours"),
        "contract_types": _choices(raw.get("contract_types"), CONTRACT_TYPES, "Contract type"),
        "work_arrangements": _choices(raw.get("work_arrangements"), WORK_ARRANGEMENTS, "Work arrangement"),
        "dealbreakers": dealbreakers,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def preferences_set(preferences: dict) -> bool:
    """True when the user has stated at least one requirement."""
    return preferences.get("min_monthly_salary") is not None or any(
        preferences.get(key) for key in ("hours", "contract_types", "work_arrangements", "dealbreakers")
    )


def _stated(value) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in _EMPTY else text


def monthly_salary_range(text) -> tuple[float, float, str] | None:
    """Read a salary such as "$8,000 to $11,000 Monthly" as a monthly (low, high, note).

    Returns None when no amount is stated or the period cannot be converted.
    An amount with no period is read as monthly, unless it is so large that it
    must be annual.
    """
    text = _stated(text)
    amounts = []
    for number, thousands in _NUMBER.findall(text):
        value = float(number.replace(",", "")) * (1000 if thousands else 1)
        if value >= 100:
            amounts.append(value)
    if not amounts:
        return None
    low, high = min(amounts), max(amounts)
    lowered = text.casefold()
    if re.search(r"hour|/\s*hr\b|daily|per day|/\s*day", lowered):
        return None
    if re.search(r"year|annum|annual|p\.?\s?a\b|/\s*yr\b", lowered):
        return low / 12, high / 12, "converted from an annual amount"
    if re.search(r"week", lowered):
        return low * 52 / 12, high * 52 / 12, "converted from a weekly amount"
    if low >= 30000:
        return low / 12, high / 12, "read as an annual amount because no period is stated"
    return low, high, ""


def _money(value: float) -> str:
    return f"${value:,.0f}"


def check_salary(posting: dict, minimum) -> dict:
    check = {"key": "salary", "label": f"Minimum monthly salary ({_money(minimum)})", "by": "code"}
    stated = _stated(posting.get("salary"))
    if not stated:
        return {**check, "status": "unknown", "detail": "The posting does not state a salary."}
    found = monthly_salary_range(stated)
    if found is None:
        return {**check, "status": "unknown", "detail": f"The salary “{stated}” could not be read as a monthly amount."}
    low, high, note = found
    shown = _money(low) if low == high else f"{_money(low)}–{_money(high)}"
    detail = f"The posting offers {shown} a month" + (f" ({note})" if note else "")
    if _OTHER_CURRENCY.search(stated):
        return {**check, "status": "unknown", "detail": f"The salary “{stated}” is in another currency; compare it yourself."}
    if low >= minimum:
        return {**check, "status": "met", "detail": detail + "."}
    if high < minimum:
        return {**check, "status": "not met", "detail": detail + f", below your minimum of {_money(minimum)}."}
    return {**check, "status": "partly met", "detail": detail + f"; only the upper part of the range reaches {_money(minimum)}."}


def _found(text: str, options: tuple[str, ...]) -> list[str]:
    return [option for option in options if re.search(_PATTERNS[option], text, re.IGNORECASE)]


def check_job_type(posting: dict, preferences: dict) -> dict:
    wanted = preferences["hours"] + preferences["contract_types"]
    check = {"key": "job_type", "label": "Job type (" + ", ".join(wanted) + ")", "by": "code"}
    stated = _stated(posting.get("employment_type"))
    if not stated:
        return {**check, "status": "unknown", "detail": "The posting does not state an employment type."}
    results = []
    for accepted, options in ((preferences["hours"], HOURS), (preferences["contract_types"], CONTRACT_TYPES)):
        if not accepted:
            continue
        offered = _found(stated, options)
        results.append(None if not offered else any(item in accepted for item in offered))
    if False in results:
        status = "not met"
    elif None in results:
        status = "unknown"
    else:
        status = "met"
    detail = f"The posting says “{stated}”."
    if status == "unknown":
        detail += " It does not say enough to compare with every type you chose."
    return {**check, "status": status, "detail": detail}


def check_work_arrangement(posting: dict, raw_text: str, preferences: dict) -> dict:
    accepted = preferences["work_arrangements"]
    check = {"key": "work_arrangement", "label": "Work arrangement (" + ", ".join(accepted) + ")", "by": "code"}
    text = " ".join(_stated(posting.get(key)) for key in ("employment_type", "location")) + "\n" + (raw_text or "")
    offered = _found(text, WORK_ARRANGEMENTS)
    if not offered:
        return {**check, "status": "unknown", "detail": "The posting does not say whether the role is on-site, hybrid, or remote."}
    detail = "The posting mentions " + " and ".join(offered) + " work."
    return {**check, "status": "met" if any(item in accepted for item in offered) else "not met", "detail": detail}


class DealbreakerDraft(BaseModel):
    dealbreaker: str
    status: Literal["conflict", "no conflict", "not stated"]
    quote: str
    reason: str


class DealbreakerDrafts(BaseModel):
    checks: list[DealbreakerDraft]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def dealbreaker_checks(drafts: DealbreakerDrafts | None, dealbreakers: list[str], raw_text: str) -> list[dict]:
    """Turn the model's ratings into checks; code verifies every quote it cites."""
    by_text = {_squash(draft.dealbreaker): draft for draft in (drafts.checks if drafts else [])}
    haystack = _squash(raw_text or "")
    checks = []
    for index, dealbreaker in enumerate(dealbreakers):
        check = {"key": f"dealbreaker.{index}", "label": dealbreaker, "by": "model"}
        draft = by_text.get(_squash(dealbreaker))
        if draft is None:
            checks.append({**check, "status": "unknown", "detail": "Not assessed."})
            continue
        quote = draft.quote.strip()
        found = bool(quote) and all(_squash(part) in haystack for part in quote.split("...") if part.strip())
        if draft.status == "conflict" and not found:
            checks.append({**check, "status": "unknown",
                           "detail": f"{draft.reason.strip()} (The quoted text could not be found in the posting; check it yourself.)"})
            continue
        status = {"conflict": "not met", "no conflict": "met", "not stated": "unknown"}[draft.status]
        detail = draft.reason.strip()
        if found:
            detail += f" The posting says: “{quote}”."
        checks.append({**check, "status": status, "detail": detail})
    return checks


async def check_preferences(posting: dict, raw_text: str, preferences: dict, ask=None) -> list[dict]:
    """Check one saved posting against the user's requirements."""
    checks = []
    if preferences.get("min_monthly_salary") is not None:
        checks.append(check_salary(posting, preferences["min_monthly_salary"]))
    if preferences.get("hours") or preferences.get("contract_types"):
        checks.append(check_job_type(posting, preferences))
    if preferences.get("work_arrangements"):
        checks.append(check_work_arrangement(posting, raw_text, preferences))
    dealbreakers = preferences.get("dealbreakers") or []
    if dealbreakers:
        drafts = None
        if ask is not None and raw_text:
            payload = {"posting_text": raw_text, "dealbreakers": dealbreakers}
            try:
                drafts = await ask("check", "preference_checker_instructions.md", payload, DealbreakerDrafts)
            except Exception:
                drafts = None
        checks.extend(dealbreaker_checks(drafts, dealbreakers, raw_text))
    return checks
