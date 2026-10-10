"""Local JSON storage for postings, experience, and the tracking log.

IDs, dates, and file writes are decided here. Callers never choose an ID.
A status change updates the posting and appends the tracking log together;
if the log write fails, the posting is restored.
"""

from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

STATUSES = ("saved", "applied", "interviewing", "offer", "rejected", "withdrawn")
EXPERIENCE_SOURCES = ("resume", "user-added")
EXPERIENCE_CATEGORIES = (
    "work", "project", "education", "skill", "leadership", "award", "other",
)

_JOB_ID = re.compile(r"JOB-(\d{4,})$")
_EXP_ID = re.compile(r"EXP-(\d{4,})$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_ANALYSIS_ID = re.compile(r"JOB-\d{4,}_\d{8}T\d{6}(-\d+)?$")
_REPLACE_ATTEMPTS = 12
_REPLACE_DELAY = 0.05

_POSTING_INPUTS = {
    "title", "company", "department", "location", "employment_type", "source",
    "source_url", "date_posted", "date_posted_is_estimate", "requirements",
    "responsibilities", "salary", "status", "notes", "related_postings",
}
_EXPERIENCE_INPUTS = {
    "source", "on_resume", "category", "text", "skills", "organization", "dates", "parent_id",
}

_COMPANY_SUFFIXES = {
    "pte", "private", "limited", "ltd", "inc", "incorporated", "corp", "corporation",
    "company", "co", "bank", "plc", "llc", "llp", "holdings", "group",
}
_NAME_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_TITLE_FILLER = {"and", "or", "the", "of", "for", "to", "in", "a", "an", "senior", "junior", "lead", "assistant"}

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "analysis_instructions.md"
_AGENT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "agent_instructions.md"


class StorageError(ValueError):
    """A storage request was invalid or a record was missing."""


class Store:
    """File store rooted at a project directory. Data lives in ``root/data``."""

    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.data_dir = self.root / "data"
        self.postings_dir = self.data_dir / "postings"
        self.raw_dir = self.postings_dir / "raw"
        self.experience_dir = self.data_dir / "experience"
        self.experience_path = self.experience_dir / "experience.json"
        self.resume_dir = self.experience_dir / "resume_source"
        self.tracking_dir = self.data_dir / "tracking"
        self.log_path = self.tracking_dir / "log.jsonl"
        self.config_dir = self.data_dir / "config"
        self.ids_path = self.config_dir / "ids.json"
        self.instructions_path = self.config_dir / "analysis_instructions.md"
        self.analyses_dir = self.data_dir / "analyses"
        self.interaction_log_path = self.data_dir / "interaction_log.jsonl"
        self._lock = self._lock_for(self.root)

    @classmethod
    def _lock_for(cls, root: Path) -> threading.RLock:
        key = str(root)
        with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks[key] = lock
            return lock

    def ensure_layout(self) -> Path:
        """Create the data folders and default files. Existing files are kept."""
        with self._lock:
            for folder in (
                self.postings_dir, self.raw_dir, self.experience_dir, self.resume_dir,
                self.tracking_dir, self.config_dir, self.analyses_dir,
            ):
                folder.mkdir(parents=True, exist_ok=True)
            if not self.ids_path.exists():
                self._write_json(self.ids_path, {"next_job": 1, "next_exp": 1})
            if not self.experience_path.exists():
                self._write_json(self.experience_path, [])
            if not self.log_path.exists():
                self._write_text(self.log_path, "")
            if not self.interaction_log_path.exists():
                self._write_text(self.interaction_log_path, "")
            if not self.instructions_path.exists():
                if not _TEMPLATE.is_file():
                    raise StorageError(f"Missing analysis template: {_TEMPLATE}")
                self._write_text(self.instructions_path, _TEMPLATE.read_text(encoding="utf-8"))
            agent_instructions = self.config_dir / "agent_instructions.md"
            if not agent_instructions.exists():
                if not _AGENT_TEMPLATE.is_file():
                    raise StorageError(f"Missing agent instructions template: {_AGENT_TEMPLATE}")
                self._write_text(agent_instructions, _AGENT_TEMPLATE.read_text(encoding="utf-8"))
            return self.data_dir

    def create_posting(self, fields: dict, raw_text: str) -> dict:
        """Save a structured posting and its exact raw text. Assigns the job ID."""
        with self._lock:
            self.ensure_layout()
            record = _build_posting(fields)
            raw = _exact_text(raw_text, "raw_text")
            for related in record["related_postings"]:
                if not self._posting_path(related).is_file():
                    raise StorageError(f"Cannot link to {related}: no such posting.")
            record["id"] = self._allocate_id("job")
            record["date_added"] = _today()
            if record["date_posted"] is None:
                record["date_posted"] = record["date_added"]
                record["date_posted_is_estimate"] = True
            try:
                self._write_text(self._raw_path(record["id"]), raw)
                self._write_json(self._posting_path(record["id"]), record)
                self._append_jsonl(self.log_path, {
                    "timestamp": _timestamp(),
                    "job_id": record["id"],
                    "event": "added",
                    "details": {
                        "title": record["title"],
                        "company": record["company"],
                        "source": record["source"],
                    },
                })
            except Exception:
                self._remove_posting_files(record["id"])
                raise
            return deepcopy(record)

    def read_posting(self, job_id: str) -> dict:
        """Return one posting. Raises StorageError if the ID is unknown."""
        with self._lock:
            self.ensure_layout()
            _require_job_id(job_id)
            path = self._posting_path(job_id)
            if not path.is_file():
                raise StorageError(f"No posting with id {job_id}.")
            record = self._read_json(path)
            if not isinstance(record, dict) or record.get("id") != job_id:
                raise StorageError(f"Posting file for {job_id} is invalid.")
            return record

    def read_raw_text(self, job_id: str) -> str:
        """Return the exact text saved beside a posting."""
        with self._lock:
            self.read_posting(job_id)
            path = self._raw_path(job_id)
            if not path.is_file():
                raise StorageError(f"No raw text saved for {job_id}.")
            return _read_text(path)

    def list_postings(self, status: str | None = None) -> list[dict]:
        """Short summary of each posting, optionally filtered by status."""
        with self._lock:
            self.ensure_layout()
            wanted = _canonical_status(status) if status is not None else None
            summaries = []
            for record in self._load_postings():
                if wanted is None or record["status"] == wanted:
                    summaries.append(_summary(record))
            summaries.sort(key=lambda item: _id_number(item["id"]))
            return summaries

    def search_postings(
        self,
        company: str | None = None,
        department: str | None = None,
        title_keywords: str | list[str] | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Case-insensitive keyword search. Each hit includes the fields that matched."""
        with self._lock:
            self.ensure_layout()
            company_q = _blank_to_none(company)
            department_q = _blank_to_none(department)
            keywords = _keywords(title_keywords)
            wanted = _canonical_status(status) if status is not None and str(status).strip() else None
            hits = []
            for record in self._load_postings():
                matched = {}
                if company_q is not None:
                    value = record.get("company") or ""
                    if not company_matches(company_q, value):
                        continue
                    matched["company"] = value
                if department_q is not None:
                    value = record.get("department") or ""
                    if department_q.casefold() not in value.casefold():
                        continue
                    matched["department"] = record.get("department")
                if keywords:
                    title = record.get("title") or ""
                    folded = title.casefold()
                    if any(word.casefold() not in folded for word in keywords):
                        continue
                    matched["title"] = title
                if wanted is not None:
                    if record.get("status") != wanted:
                        continue
                    matched["status"] = wanted
                hit = _summary(record)
                hit["department"] = record.get("department")
                hit["matched"] = matched
                hits.append(hit)
            hits.sort(key=lambda item: _id_number(item["id"]))
            return hits

    def update_status(self, job_id: str, new_status: str, note: str | None = None) -> dict:
        """Set a posting's status and append a status_changed event in one operation."""
        with self._lock:
            canonical = _canonical_status(new_status)
            note_text = _optional_note(note)
            posting = self.read_posting(job_id)
            previous = posting["status"]

            def mutate(record: dict) -> None:
                record["status"] = canonical

            return self._commit_posting(
                posting,
                mutate,
                "status_changed",
                {"from": previous, "to": canonical, "note": note_text},
            )

    def add_note(self, job_id: str, note: str) -> dict:
        """Replace the posting note and append a note event. The log keeps older notes."""
        with self._lock:
            text = _required_text(note, "note")
            posting = self.read_posting(job_id)

            def mutate(record: dict) -> None:
                record["notes"] = text

            return self._commit_posting(posting, mutate, "note", {"note": text})

    def read_tracking_log(self, job_id: str | None = None) -> list[dict]:
        """Return tracking events, oldest first. Pass a job ID to filter."""
        with self._lock:
            self.ensure_layout()
            if job_id is not None:
                _require_job_id(job_id)
                if not self._posting_path(job_id).is_file():
                    raise StorageError(f"No posting with id {job_id}.")
            events = []
            text = _read_text(self.log_path)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StorageError(f"Invalid tracking log line {line_number}.") from exc
                if not isinstance(event, dict):
                    raise StorageError(f"Invalid tracking log line {line_number}.")
                if job_id is None or event.get("job_id") == job_id:
                    events.append(event)
            return events

    def add_experience(self, fields: dict) -> dict:
        """Append one experience entry. Assigns the experience ID."""
        with self._lock:
            self.ensure_layout()
            if not isinstance(fields, dict):
                raise StorageError("Experience fields must be an object.")
            if "id" in fields or "date_added" in fields:
                raise StorageError("Experience id and date_added are assigned by code.")
            entry = _build_experience(fields)
            entries = self._read_experience_list()
            _require_parent(entry, entries)
            entry["id"] = self._allocate_id("exp")
            entry["date_added"] = _today()
            entries.append(entry)
            self._write_json(self.experience_path, entries)
            return deepcopy(entry)

    def import_resume_data(self, proposed: list[dict], source_name: str, source_bytes: bytes) -> dict:
        """Save reviewed resume facts and uploaded source bytes as one batch."""
        with self._lock:
            self.ensure_layout()
            if not isinstance(proposed, list) or not proposed:
                raise StorageError("Resume import needs at least one experience entry.")
            if not isinstance(source_bytes, bytes) or not source_bytes:
                raise StorageError("Resume source is empty.")
            built, parents = [], []
            for index, fields in enumerate(proposed):
                if not isinstance(fields, dict):
                    raise StorageError("Each resume entry must be an object.")
                clean = dict(fields)
                parent_index = clean.pop("parent_index", None)
                if parent_index is not None and (
                    not isinstance(parent_index, int) or not 0 <= parent_index < index
                    or parents[parent_index] is not None
                ):
                    raise StorageError("A resume detail must point at an earlier top-level entry.")
                parents.append(parent_index)
                clean["source"] = "resume"
                clean["on_resume"] = True
                built.append(_build_experience(clean))

            entries = self._read_experience_list()
            for entry, entry_id in zip(built, self._allocate_ids("exp", len(built))):
                entry["id"] = entry_id
                entry["date_added"] = _today()
            for entry, parent_index in zip(built, parents):
                if parent_index is not None:
                    entry["parent_id"] = built[parent_index]["id"]
            destination = self._resume_source_data_path(source_name, source_bytes)
            created_copy = not destination.exists()
            try:
                if created_copy:
                    destination.write_bytes(source_bytes)
                self._write_json(self.experience_path, entries + built)
            except Exception:
                if created_copy and destination.is_file():
                    destination.unlink()
                raise
            return {
                "entries": deepcopy(built),
                "source_file": destination.name,
            }

    def read_experience(
        self,
        category: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """Return experience entries, optionally filtered by category and source."""
        with self._lock:
            self.ensure_layout()
            category_q = _canonical_choice(category, EXPERIENCE_CATEGORIES, "category")
            source_q = _canonical_choice(source, EXPERIENCE_SOURCES, "source")
            entries = []
            for entry in self._read_experience_list():
                if category_q is not None and entry["category"] != category_q:
                    continue
                if source_q is not None and entry["source"] != source_q:
                    continue
                entries.append(entry)
            return entries

    def search_experience(self, keywords: str | list[str]) -> list[dict]:
        """Return entries whose text, skills, or organization contain every keyword."""
        with self._lock:
            self.ensure_layout()
            words = _keywords(keywords)
            if not words:
                return []
            hits = []
            for entry in self._read_experience_list():
                haystack = " ".join([
                    entry.get("text") or "",
                    " ".join(entry.get("skills") or []),
                    entry.get("organization") or "",
                ]).casefold()
                if all(word.casefold() in haystack for word in words):
                    hits.append(entry)
            return hits

    def update_experience(self, exp_id: str, updates: dict) -> dict:
        """Replace fields on one experience entry. The ID does not change."""
        with self._lock:
            self.ensure_layout()
            _require_exp_id(exp_id)
            if not isinstance(updates, dict) or not updates:
                raise StorageError("update_experience needs at least one field to change.")
            unknown = set(updates) - _EXPERIENCE_INPUTS
            if unknown:
                raise StorageError(f"Unknown experience field: {sorted(unknown)[0]}.")
            entries = self._read_experience_list()
            for index, entry in enumerate(entries):
                if entry["id"] != exp_id:
                    continue
                merged = dict(entry)
                merged.update(updates)
                rebuilt = _build_experience(merged)
                _require_parent(rebuilt, [other for other in entries if other["id"] != exp_id])
                rebuilt["id"] = entry["id"]
                rebuilt["date_added"] = entry["date_added"]
                entries[index] = rebuilt
                self._write_json(self.experience_path, entries)
                return deepcopy(rebuilt)
            raise StorageError(f"No experience entry with id {exp_id}.")

    def delete_experience(self, exp_id: str) -> None:
        """Remove one experience entry."""
        with self._lock:
            self.ensure_layout()
            _require_exp_id(exp_id)
            entries = self._read_experience_list()
            kept = [entry for entry in entries if entry["id"] != exp_id]
            if len(kept) == len(entries):
                raise StorageError(f"No experience entry with id {exp_id}.")
            # Details of a deleted entry are kept as standalone entries.
            for entry in kept:
                if entry.get("parent_id") == exp_id:
                    entry["parent_id"] = None
            self._write_json(self.experience_path, kept)

    def save_analysis(self, job_id: str, analysis: dict) -> str:
        """Save a new analysis for a posting. Assigns the analysis ID from the time."""
        with self._lock:
            self.read_posting(job_id)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            analysis_id = f"{job_id}_{stamp}"
            number = 2
            while self._analysis_path(analysis_id).exists():
                analysis_id = f"{job_id}_{stamp}-{number}"
                number += 1
            self._write_json(self._analysis_path(analysis_id), {"analysis_id": analysis_id, **analysis})
            return analysis_id

    def read_analysis(self, analysis_id: str) -> dict:
        """Return one saved analysis."""
        with self._lock:
            self.ensure_layout()
            path = self._analysis_path(analysis_id)
            if not path.is_file():
                raise StorageError(f"No analysis with id {analysis_id}.")
            return self._read_json(path)

    def write_analysis(self, analysis_id: str, analysis: dict) -> None:
        """Replace a saved analysis, for example after a re-check."""
        with self._lock:
            self.read_analysis(analysis_id)
            if analysis.get("analysis_id") != analysis_id:
                raise StorageError("Analysis id cannot change.")
            self._write_json(self._analysis_path(analysis_id), analysis)

    def list_analyses(self, job_id: str | None = None) -> list[str]:
        """Return saved analysis IDs, oldest first, optionally for one posting."""
        with self._lock:
            self.ensure_layout()
            ids = [path.stem for path in self.analyses_dir.glob("JOB-*.json") if _ANALYSIS_ID.fullmatch(path.stem)]
            if job_id is not None:
                ids = [item for item in ids if item.split("_", 1)[0] == job_id]
            return sorted(ids)

    def review_claim(self, analysis_id: str, requirement_id: str, decision: str) -> dict:
        """Record the user's agree/disagree on one fit claim and return that claim."""
        with self._lock:
            if decision not in {"agree", "disagree"}:
                raise StorageError("Decision must be agree or disagree.")
            analysis = self.read_analysis(analysis_id)
            for claim in analysis["claims"]:
                if claim["requirement_id"] == requirement_id:
                    claim["review"] = {"decision": decision, "at": _timestamp()}
                    self._write_json(self._analysis_path(analysis_id), analysis)
                    return deepcopy(claim)
            raise StorageError(f"{analysis_id} has no claim for {requirement_id}.")

    def append_interaction(self, record: dict) -> dict:
        """Append one human decision to the interaction log, stamped with the time."""
        with self._lock:
            self.ensure_layout()
            if not isinstance(record, dict):
                raise StorageError("Interaction record must be an object.")
            entry = {"timestamp": _timestamp(), **record}
            self._append_jsonl(self.interaction_log_path, entry)
            return entry

    def read_interactions(self) -> list[dict]:
        """Return logged human decisions, oldest first."""
        with self._lock:
            self.ensure_layout()
            return [
                json.loads(line)
                for line in _read_text(self.interaction_log_path).splitlines()
                if line.strip()
            ]

    def read_analysis_instructions(self) -> str:
        """Return the current analysis instructions. Creates the default file if needed."""
        with self._lock:
            self.ensure_layout()
            return _read_text(self.instructions_path)

    def write_analysis_instructions(self, text: str) -> None:
        """Replace the analysis instructions. Does not run during ensure_layout."""
        with self._lock:
            self.ensure_layout()
            self._write_text(self.instructions_path, _exact_text(text, "analysis instructions"))

    def _commit_posting(self, posting: dict, mutate, event: str, details: dict) -> dict:
        original = deepcopy(posting)
        mutate(posting)
        self._write_json(self._posting_path(posting["id"]), posting)
        try:
            self._append_jsonl(self.log_path, {
                "timestamp": _timestamp(),
                "job_id": posting["id"],
                "event": event,
                "details": details,
            })
        except Exception:
            self._write_json(self._posting_path(original["id"]), original)
            raise
        return deepcopy(posting)

    def _allocate_id(self, kind: str) -> str:
        """Return the next unused ID and advance the counter. Caller holds the lock."""
        return self._allocate_ids(kind, 1)[0]

    def _allocate_ids(self, kind: str, count: int) -> list[str]:
        """Reserve ``count`` consecutive IDs with one counter write. Caller holds the lock."""
        self.ensure_layout()
        state = self._read_json(self.ids_path)
        if not isinstance(state, dict):
            raise StorageError("ID counter file is invalid.")
        if kind == "job":
            key, prefix, scanned = "next_job", "JOB", self._max_job_number()
        elif kind == "exp":
            key, prefix, scanned = "next_exp", "EXP", self._max_exp_number()
        else:
            raise StorageError(f"Unknown id kind: {kind}.")
        number = scanned + 1
        stored = state.get(key, 1)
        if isinstance(stored, int) and not isinstance(stored, bool) and stored > number:
            number = stored
        state["next_job"] = _counter_value(state.get("next_job"), 1)
        state["next_exp"] = _counter_value(state.get("next_exp"), 1)
        state[key] = number + count
        self._write_json(self.ids_path, state)
        return [f"{prefix}-{value:04d}" for value in range(number, number + count)]

    def _max_job_number(self) -> int:
        numbers = []
        for path in self.postings_dir.glob("JOB-*.json"):
            match = _JOB_ID.fullmatch(path.stem)
            if match:
                numbers.append(int(match.group(1)))
        return max(numbers, default=0)

    def _max_exp_number(self) -> int:
        numbers = []
        for entry in self._read_experience_list():
            match = _EXP_ID.fullmatch(str(entry.get("id", "")))
            if match:
                numbers.append(int(match.group(1)))
        return max(numbers, default=0)

    def _load_postings(self) -> list[dict]:
        records = []
        for path in self.postings_dir.glob("JOB-*.json"):
            if not _JOB_ID.fullmatch(path.stem):
                continue
            record = self._read_json(path)
            if not isinstance(record, dict):
                raise StorageError(f"Posting file {path.name} is invalid.")
            records.append(record)
        return records

    def _read_experience_list(self) -> list[dict]:
        payload = self._read_json(self.experience_path)
        if not isinstance(payload, list):
            raise StorageError("experience.json must be a list.")
        seen = set()
        for entry in payload:
            if not isinstance(entry, dict) or not _EXP_ID.fullmatch(str(entry.get("id", ""))):
                raise StorageError("experience.json contains an entry without a valid id.")
            if entry["id"] in seen:
                raise StorageError(f"Duplicate experience id {entry['id']}.")
            seen.add(entry["id"])
        return payload

    def _posting_path(self, job_id: str) -> Path:
        return self.postings_dir / f"{job_id}.json"

    def _analysis_path(self, analysis_id: str) -> Path:
        if not isinstance(analysis_id, str) or not _ANALYSIS_ID.fullmatch(analysis_id):
            raise StorageError(f"Analysis id {analysis_id!r} must look like JOB-0001_20261007T120000.")
        return self.analyses_dir / f"{analysis_id}.json"

    def _raw_path(self, job_id: str) -> Path:
        return self.raw_dir / f"{job_id}.txt"

    def _resume_source_data_path(self, source_name: str, source_bytes: bytes) -> Path:
        """Choose a collision-safe source filename for uploaded bytes."""
        name = Path(source_name).name
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._") or "resume"
        suffix = Path(name).suffix.casefold()
        candidate = self.resume_dir / f"{safe_stem}{suffix}"
        number = 2
        while candidate.exists() and candidate.read_bytes() != source_bytes:
            candidate = self.resume_dir / f"{safe_stem}_{number}{suffix}"
            number += 1
        return candidate

    def _remove_posting_files(self, job_id: str) -> None:
        for path in (self._posting_path(job_id), self._raw_path(job_id)):
            if path.is_file():
                path.unlink()

    def _read_json(self, path: Path):
        try:
            return json.loads(_read_text(path))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"Could not read {path.name}.") from exc

    def _write_json(self, path: Path, payload) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._write_text(path, text)

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        # Cloud-sync clients (iCloud, OneDrive) briefly lock files on Windows,
        # which makes the rename fail with "Access is denied". Retry briefly.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    temporary.unlink(missing_ok=True)
                    raise
                time.sleep(_REPLACE_DELAY * (attempt + 1))

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(", ", ": ")) + "\n"
        before = path.stat().st_size if path.exists() else 0
        try:
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
        except Exception:
            self._truncate(path, before)
            raise

    def _truncate(self, path: Path, size: int) -> None:
        if not path.exists():
            return
        with path.open("r+b") as handle:
            handle.truncate(size)


def normalize_company(value: str) -> str:
    """Normalize a company name: lowercase, no punctuation, no trailing "Pte Ltd", "Bank", etc."""
    words, initials = [], []
    # Rejoin runs of single letters split by punctuation: "D.B.S." -> "dbs".
    for word in [word.casefold() for word in _NAME_WORD.findall(value or "")] + [""]:
        if len(word) == 1:
            initials.append(word)
            continue
        if initials:
            words.append("".join(initials))
            initials = []
        if word:
            words.append(word)
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def title_keywords(value: str) -> set[str]:
    """Return meaningful title tokens, excluding common job-title filler."""
    return {word.casefold() for word in _NAME_WORD.findall(value or "") if word.casefold() not in _TITLE_FILLER}


def company_matches(query: str, company: str) -> bool:
    """True when normalized names match or one contains the other ("DBS" / "DBS Bank Ltd")."""
    wanted, actual = normalize_company(query), normalize_company(company)
    return bool(wanted) and bool(actual) and (wanted in actual or actual in wanted)


def default_store() -> Store:
    """Store for this project directory."""
    return Store(Path(__file__).resolve().parent)


def _build_posting(fields: dict) -> dict:
    if not isinstance(fields, dict):
        raise StorageError("Posting fields must be an object.")
    if "id" in fields or "date_added" in fields:
        raise StorageError("Posting id and date_added are assigned by code.")
    unknown = set(fields) - _POSTING_INPUTS
    if unknown:
        raise StorageError(f"Unknown posting field: {sorted(unknown)[0]}.")
    title = _required_text(fields.get("title"), "title")
    company = _required_text(fields.get("company"), "company")
    source = _required_text(fields.get("source"), "source")
    location = fields.get("location", "")
    if location is None:
        location = ""
    if not isinstance(location, str):
        raise StorageError("location must be a string.")
    status = _canonical_status(fields.get("status", "saved"))
    posted = _optional_date(fields.get("date_posted"), "date_posted")
    estimate = fields.get("date_posted_is_estimate", False if posted else True)
    if not isinstance(estimate, bool):
        raise StorageError("date_posted_is_estimate must be true or false.")
    return {
        "id": None,
        "title": title,
        "company": company,
        "department": _optional_text(fields.get("department"), "department"),
        "location": location.strip(),
        "employment_type": _optional_text(fields.get("employment_type"), "employment_type"),
        "source": source,
        "source_url": _optional_text(fields.get("source_url"), "source_url"),
        "date_added": None,
        "date_posted": posted,
        "date_posted_is_estimate": estimate,
        "requirements": _normalize_requirements(fields.get("requirements", [])),
        "responsibilities": _string_list(fields.get("responsibilities", []), "responsibilities"),
        "salary": _optional_text(fields.get("salary"), "salary"),
        "status": status,
        "notes": _optional_text(fields.get("notes"), "notes"),
        "related_postings": _job_id_list(fields.get("related_postings")),
    }


def _job_id_list(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StorageError("related_postings must be a list of job ids.")
    for item in value:
        _require_job_id(item)
    return list(dict.fromkeys(value))


def _build_experience(fields: dict) -> dict:
    if not isinstance(fields, dict):
        raise StorageError("Experience fields must be an object.")
    unknown = set(fields) - _EXPERIENCE_INPUTS - {"id", "date_added"}
    if unknown:
        raise StorageError(f"Unknown experience field: {sorted(unknown)[0]}.")
    source = _canonical_choice(fields.get("source"), EXPERIENCE_SOURCES, "source")
    if source is None:
        raise StorageError("Experience source must be 'resume' or 'user-added'.")
    category = _canonical_choice(fields.get("category"), EXPERIENCE_CATEGORIES, "category")
    if category is None:
        raise StorageError(
            "Experience category must be one of: " + ", ".join(EXPERIENCE_CATEGORIES) + "."
        )
    on_resume = fields.get("on_resume", source == "resume")
    if not isinstance(on_resume, bool):
        raise StorageError("on_resume must be true or false.")
    skills = fields.get("skills", [])
    return {
        "id": None,
        "source": source,
        "on_resume": on_resume,
        "category": category,
        "text": _required_text(fields.get("text"), "text"),
        "skills": _string_list(skills, "skills"),
        "organization": _optional_text(fields.get("organization"), "organization"),
        "dates": _optional_text(fields.get("dates"), "dates"),
        "parent_id": _optional_exp_id(fields.get("parent_id")),
        "date_added": None,
    }


def _normalize_requirements(items) -> list[dict]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise StorageError("requirements must be a list.")
    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise StorageError(f"Requirement {index} must be an object.")
        if "id" in item:
            raise StorageError("Requirement ids are assigned by code.")
        extra = set(item) - {"text", "type"}
        if extra:
            raise StorageError(f"Requirement {index} has an unknown field: {sorted(extra)[0]}.")
        text = _required_text(item.get("text"), f"requirement {index} text")
        req_type = item.get("type", "required")
        if req_type not in {"required", "preferred"}:
            raise StorageError(f"Requirement {index} type must be 'required' or 'preferred'.")
        req_id = f"REQ-{index}"
        normalized.append({"id": req_id, "text": text, "type": req_type})
    return normalized


def _summary(record: dict) -> dict:
    return {
        "id": record["id"],
        "title": record["title"],
        "company": record["company"],
        "date_added": record["date_added"],
        "status": record["status"],
    }


def _canonical_status(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageError("Status must be one of: " + ", ".join(STATUSES) + ".")
    folded = value.strip().casefold()
    for status in STATUSES:
        if status == folded:
            return status
    raise StorageError("Status must be one of: " + ", ".join(STATUSES) + ".")


def _canonical_choice(value, allowed: tuple[str, ...], label: str) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise StorageError(f"{label} must be one of: " + ", ".join(allowed) + ".")
    folded = value.strip().casefold()
    for item in allowed:
        if item == folded:
            return item
    raise StorageError(f"{label} must be one of: " + ", ".join(allowed) + ".")


def _counter_value(value, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return default


def _optional_exp_id(value) -> str | None:
    if value is None or value == "":
        return None
    _require_exp_id(value)
    return value


def _require_parent(entry: dict, entries: list[dict]) -> None:
    """A parent must exist and be top level, so nesting stays one level deep."""
    parent_id = entry.get("parent_id")
    if parent_id is None:
        return
    parent = next((other for other in entries if other["id"] == parent_id), None)
    if parent is None:
        raise StorageError(f"Parent entry {parent_id} does not exist.")
    if parent.get("parent_id"):
        raise StorageError(f"{parent_id} is itself a detail and cannot have details.")


def _require_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise StorageError(f"Job id {job_id!r} must look like JOB-0001.")


def _require_exp_id(exp_id: str) -> None:
    if not isinstance(exp_id, str) or not _EXP_ID.fullmatch(exp_id):
        raise StorageError(f"Experience id {exp_id!r} must look like EXP-0001.")


def _required_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageError(f"{label} must be non-empty text.")
    return value.strip()


def _optional_text(value, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and label == "salary":
        return str(value)
    if not isinstance(value, str):
        raise StorageError(f"{label} must be text or null.")
    stripped = value.strip()
    return stripped or None


def _optional_date(value, label: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise StorageError(f"{label} must be a YYYY-MM-DD date.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise StorageError(f"{label} must be a YYYY-MM-DD date.") from exc
    return value


def _optional_note(value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise StorageError("note must be text.")
    return value.strip()


def _exact_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageError(f"{label} must be non-empty text.")
    return value


def _string_list(value, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StorageError(f"{label} must be a list of strings.")
    cleaned = [item.strip() for item in value if item.strip()]
    return cleaned


def _keywords(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.split() if part]
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        words = []
        for part in value:
            words.extend(piece for piece in part.split() if piece)
        return words
    raise StorageError("keywords must be a string or a list of strings.")


def _blank_to_none(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageError("Search text must be a string.")
    stripped = value.strip()
    return stripped or None


def _id_number(record_id: str) -> int:
    return int(str(record_id).split("-", 1)[1])


def _read_text(path: Path) -> str:
    """Read text without changing line endings. Path.read_text cannot do this on Python 3.11."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _today() -> str:
    return date.today().isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    print(f"Storage folders are ready at {default_store().ensure_layout()}")
