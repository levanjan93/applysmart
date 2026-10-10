"""Link each extracted posting field to its source in the user's pasted text.

The model gives a short exact quote for each item it extracted. Code checks
that every quote really appears in the pasted text; an item whose quote is
missing or made up falls back to code's own search and is flagged when nothing
is found. Code also splits the pasted text into lines labelled as headers,
bullets, or plain text for display. The text itself is never changed: every
line and span is a pair of character offsets into the original paste.
"""

from __future__ import annotations

import re

SCALAR_FIELDS = (
    "title", "company", "department", "location", "employment_type", "date_posted", "salary",
)

_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_BULLET = re.compile(r"\s*(?:[•·▪◦‣●○■□➢➤✓✔*\-–—]|\d{1,2}[.)])\s+")
_SECTION = re.compile(
    r"(about (the )?(job|role|us|company|team|you)|job (description|summary|highlights)|description|"
    r"(key |main |your )?responsibilities|(key |minimum |basic |preferred )?(requirements|qualifications)|"
    r"what you('|’)ll (do|bring|need)|what you will (do|bring|need)|what we('|’)re looking for|"
    r"what we (offer|look for)|who you are|(key |required )?skills|benefits|perks|why join us|the role|"
    r"your role|overview|nice to haves?|must haves?|good to have)\s*:?",
    re.IGNORECASE,
)
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
_EDGE_PUNCTUATION = " \t\"'“”‘’.,;:•-–—*"
_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
_APPROXIMATE_SHARE = 0.6


def _folded(text: str) -> tuple[str, list[int]]:
    """Casefold and collapse whitespace, keeping each output char's source offset."""
    out, offsets = [], []
    previous_space = True
    for index, char in enumerate(text.translate(_FOLD)):
        if char.isspace():
            if not previous_space:
                out.append(" ")
                offsets.append(index)
            previous_space = True
            continue
        previous_space = False
        for lowered in char.lower():
            out.append(lowered)
            offsets.append(index)
    return "".join(out), offsets


def _normal(text: str) -> str:
    return _folded(text)[0].strip()


def locate_quote(raw_text: str, quote: str | None) -> list[list[int]] | None:
    """Return [start, end] spans where the quote appears in the raw text, or None.

    Matching ignores case, whitespace runs, and curly quotes. A quote that
    elides text with "..." must match each piece in order.
    """
    if not quote or not quote.strip():
        return None
    haystack, offsets = _folded(raw_text)
    pieces = [piece.strip(_EDGE_PUNCTUATION) for piece in _ELLIPSIS.split(quote)]
    pieces = [_normal(piece) for piece in pieces if piece]
    if not pieces:
        return None
    spans, position = [], 0
    for piece in pieces:
        found = haystack.find(piece, position)
        if found < 0:
            return None
        end = found + len(piece)
        spans.append([offsets[found], offsets[end - 1] + 1])
        position = end
    return spans


def format_lines(raw_text: str) -> list[dict]:
    """Split the paste into display lines: header, bullet, item, text, or blank.

    ``marker_end`` is where a bullet's marker ("-", "•", "1.") ends, so the GUI
    can style it without removing it. "item" is a plain line inside a section,
    shown with a list dot because pasted pages often lose their bullets.
    """
    lines, start = [], 0
    for text in raw_text.split("\n"):
        end = start + len(text)
        lines.append({"start": start, "end": end, "kind": "text", "marker_end": None})
        stripped = text.strip()
        bullet = _BULLET.match(text)
        if not stripped:
            lines[-1]["kind"] = "blank"
        elif bullet and bullet.end() < len(text):
            lines[-1]["kind"] = "bullet"
            lines[-1]["marker_end"] = start + bullet.end()
        elif _is_header(stripped):
            lines[-1]["kind"] = "header"
        start = end + 1
    _mark_section_items(lines)
    return lines


def _is_header(text: str) -> bool:
    if len(text) > 60:
        return False
    if _SECTION.fullmatch(text):
        return True
    if text.endswith(":") and len(text) <= 50:
        return True
    letters = [char for char in text if char.isalpha()]
    # All capitals, but not a short acronym line such as "SQL" in a skills list.
    return len(letters) >= 5 and text.upper() == text


def _mark_section_items(lines: list[dict]) -> None:
    """Show runs of two or more plain lines under a header as list items."""
    in_section = False
    index = 0
    while index < len(lines):
        kind = lines[index]["kind"]
        if kind == "header":
            in_section = True
        if kind != "text":
            index += 1
            continue
        run_end = index
        while run_end < len(lines) and lines[run_end]["kind"] == "text":
            run_end += 1
        if in_section and run_end - index >= 2:
            for line in lines[index:run_end]:
                line["kind"] = "item"
        index = run_end


def _best_line(raw_text: str, lines: list[dict], value: str) -> list[int] | None:
    """The line holding most of the value's words (at least 60%), as a span."""
    wanted = {word.casefold() for word in _WORD.findall(value) if len(word) > 2}
    if not wanted:
        return None
    best, best_share = None, 0.0
    for line in lines:
        if line["kind"] == "blank":
            continue
        words = {word.casefold() for word in _WORD.findall(raw_text[line["start"]:line["end"]])}
        share = len(wanted & words) / len(wanted)
        if share > best_share:
            best, best_share = line, share
    if best is None or best_share < _APPROXIMATE_SHARE:
        return None
    return [best["start"], best["end"]]


def _items(arguments: dict, requirements: list[dict]) -> list[tuple[str, str, str]]:
    """(key, field, value) for every item the card shows as checkable."""
    items = []
    for field in SCALAR_FIELDS:
        value = arguments.get(field)
        if isinstance(value, str) and value.strip():
            items.append((field, field, value))
    for index, requirement in enumerate(requirements):
        items.append((f"requirements.{index}", "requirement", requirement.get("text") or ""))
    for index, text in enumerate(arguments.get("responsibilities") or []):
        if isinstance(text, str):
            items.append((f"responsibilities.{index}", "responsibility", text))
    return items


def _quote_index(quotes) -> dict:
    """Map (field, normalized value) and bare scalar field names to quotes."""
    index = {}
    for entry in quotes or []:
        if not isinstance(entry, dict):
            continue
        field, quote = entry.get("field"), entry.get("quote")
        if not isinstance(field, str) or not isinstance(quote, str):
            continue
        value = entry.get("value") if isinstance(entry.get("value"), str) else ""
        index.setdefault((field, _normal(value)), quote)
        if field in SCALAR_FIELDS:
            index.setdefault((field, None), quote)
    return index


def source_map(raw_text: str, arguments: dict, requirements: list[dict]) -> dict:
    """Display lines plus, for every extracted item, where it came from.

    Each source has ``status``: ``quoted`` (the model's quote was verified in
    the text), ``found`` (the value itself appears verbatim), ``approximate``
    (a line shares most of its words), or ``missing``. ``quote_not_found``
    marks a quote the model gave that is not in the text.
    """
    lines = format_lines(raw_text)
    quotes = _quote_index(arguments.get("source_quotes"))
    sources = {}
    for key, field, value in _items(arguments, requirements):
        quote = quotes.get((field, _normal(value)))
        if quote is None and field in SCALAR_FIELDS:
            quote = quotes.get((field, None))
        spans = locate_quote(raw_text, quote)
        status = "quoted"
        if spans is None:
            spans, status = locate_quote(raw_text, value), "found"
        if spans is None:
            line = _best_line(raw_text, lines, value)
            spans, status = ([line], "approximate") if line else ([], "missing")
        sources[key] = {
            "status": status,
            "spans": spans,
            "quote": quote,
            "quote_not_found": bool(quote) and status != "quoted",
        }
    return {"lines": lines, "sources": sources}
