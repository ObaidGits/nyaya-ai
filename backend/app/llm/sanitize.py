"""Reasoning isolation and prompt-echo detection (production defect fix).

A reasoning-capable model can put its chain-of-thought into the very fields
the providers read as answer text (``choices[].message.content``,
``message.content``, Gemini parts) — confirmed in production with a
model that answered the grounding prompt with "Here's a thinking process: …"
echoing the system prompt, the evidence blocks and the internal
regeneration instruction. This module gives every provider one shared,
provider-agnostic defense:

* :func:`strip_reasoning_wrappers` — removes ``<think>…</think>`` (and
  equivalent) wrapper blocks from completed text, including an unclosed
  opening wrapper (everything after it is reasoning, not answer).
* :class:`ReasoningStreamFilter` — the streaming counterpart: suppresses
  tokens inside a wrapper even when the wrapper tags arrive split across
  chunk boundaries.
* :func:`is_prompt_echo` — conservative structural detection that the
  "answer" is an echo of the internal prompt (evidence headers, rule
  headings, regeneration instructions, or a long verbatim fragment of any
  request message) rather than an assistant answer.

Detection is deliberately structural: exact internal marker strings and
long verbatim overlaps. Ordinary legal answers — even ones that quote
statutory wording or carry citations — never trip it.

Reasoning FIELDS (``reasoning``, ``reasoning_content``, ``reasoning_details``,
``thinking``) are simply never read by the providers; they are dropped at
the provider boundary and never reach the application, never mind the SSE
stream.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# --- Reasoning wrappers ----------------------------------------------------

#: Opening/closing tags models use to wrap chain-of-thought. Matched
#: case-insensitively; whitespace inside the tag is tolerated.
_OPEN_WRAPPER_RE = re.compile(r"<\s*(think|thinking|reasoning)\s*>", re.IGNORECASE)
_CLOSE_WRAPPER_RE = re.compile(r"<\s*/\s*(think|thinking|reasoning)\s*>", re.IGNORECASE)
#: A trailing unfinished bracket that could still grow into a wrapper tag
#: ("<", "<t", "<thin", "</", ...). Held back until the next chunk resolves it.
_PARTIAL_TAG_RE = re.compile(r"<\s*/?\s*[a-z]{0,10}\s*$", re.IGNORECASE)


def strip_reasoning_wrappers(text: str) -> str:
    """Remove reasoning wrapper blocks from completed answer text.

    Closed blocks (``<think>…</think>``) are removed whole. An unclosed
    opening wrapper means the model never left its reasoning section —
    everything after it is treated as reasoning and dropped.
    """
    if not text:
        return text
    out: list[str] = []
    cursor = 0
    open_match = _OPEN_WRAPPER_RE.search(text, cursor)
    while open_match is not None:
        out.append(text[cursor : open_match.start()])
        close_match = _CLOSE_WRAPPER_RE.search(text, open_match.end())
        if close_match is None:
            # Unclosed: the remainder is reasoning.
            return "".join(out).strip()
        cursor = close_match.end()
        open_match = _OPEN_WRAPPER_RE.search(text, cursor)
    out.append(text[cursor:])
    return "".join(out).strip()


class ReasoningStreamFilter:
    """Streaming counterpart of :func:`strip_reasoning_wrappers`.

    Feed each answer-content delta through :meth:`push`; only text that is
    provably outside a reasoning wrapper is emitted. A possibly-partial
    opening tag at the end of a chunk is held back until the next chunk
    resolves it (the held-back tail is flushed by :meth:`flush` at end of
    stream when it turned out not to be a tag).
    """

    _MAX_TAG_LEN = len("<reasoning>")  # longest opening/closing tag

    def __init__(self) -> None:
        self._inside_wrapper = False
        self._pending = ""

    def push(self, delta: str) -> str:
        """Consume one delta; return the safe-to-emit portion."""
        self._pending += delta
        emit = ""
        while self._pending:
            if self._inside_wrapper:
                close = _CLOSE_WRAPPER_RE.search(self._pending)
                if close is None:
                    # Still inside reasoning; keep a tail that might hold a
                    # partial closing tag, drop the rest.
                    keep = self._MAX_TAG_LEN + 2
                    if len(self._pending) > keep:
                        self._pending = self._pending[-keep:]
                    return emit
                self._pending = self._pending[close.end() :]
                self._inside_wrapper = False
                continue
            open_match = _OPEN_WRAPPER_RE.search(self._pending)
            if open_match is not None:
                emit += self._pending[: open_match.start()]
                self._pending = self._pending[open_match.end() :]
                self._inside_wrapper = True
                continue
            # No full tag present. Emit everything except a tail that could
            # be the start of an opening tag ("<", "<t", "<th", ...).
            tail_len = self._partial_tag_tail_len(self._pending)
            emit += self._pending[: len(self._pending) - tail_len]
            self._pending = self._pending[len(self._pending) - tail_len :]
            return emit
        return emit

    def flush(self) -> str:
        """End of stream: release any held-back partial-tag text."""
        out = "" if self._inside_wrapper else self._pending
        self._pending = ""
        return out

    @classmethod
    def _partial_tag_tail_len(cls, text: str) -> int:
        """Length of the trailing slice that could still grow into a
        wrapper tag — i.e. an unfinished ``<think``-style bracket."""
        match = _PARTIAL_TAG_RE.search(text)
        if match is None:
            return 0
        return len(text) - match.start()


# --- Prompt-echo detection -------------------------------------------------

#: Exact structural markers that only ever appear inside the internal
#: grounding prompt — never in a legitimate answer. Each is a full-line /
#: distinctive fragment of the prompt contract (prompt.py, service.py).
_ECHO_MARKERS: tuple[str, ...] = (
    "--- STATUTE EVIDENCE",
    "--- UNTRUSTED DOCUMENT EVIDENCE",
    "STRICT RULES",
    "Using ONLY the evidence above",
    # Internal regeneration instruction (generation/service.py).
    "Continue the answer using ONLY the evidence above",
    # Confirmed production preamble of a reasoning echo.
    "Here's a thinking process",
    "Here is a thinking process",
)

#: A verbatim run of at least this many characters copied from any request
#: message (system prompt or evidence blocks) counts as a prompt echo.
_VERBATIM_MIN_LEN = 60


def _normalized_lines(text: str) -> set[str]:
    return {line.strip().lower() for line in text.splitlines() if line.strip()}


def is_prompt_echo(text: str, messages: Sequence[object]) -> bool:
    """True when ``text`` looks like an echo of the internal prompt.

    Two conservative structural signals, either sufficient:

    1. An exact internal marker string (evidence block header, rule
       heading, regeneration instruction) appears in the text.
    2. A verbatim run of ``>= 60`` characters of any request message
       (including the system prompt and evidence blocks) appears in the
       text — normal quoting ("imprisonment for life [TS s.103]") is far
       shorter; only wholesale copying trips this.
    """
    if not text:
        return False
    lowered = " ".join(text.split()).lower()
    for marker in _ECHO_MARKERS:
        if marker.lower() in lowered:
            return True
    internal = _internal_messages(messages)
    for message in internal:
        content = getattr(message, "content", "")
        if not content:
            continue
        if _longest_common_run(content.lower(), lowered) >= _VERBATIM_MIN_LEN:
            return True
    return False


def _internal_messages(messages: Sequence[object]) -> list[object]:
    """The messages that carry internal prompt content.

    The system prompt and the code-built grounding user message (evidence
    blocks, citation instructions) are internal. Conversation HISTORY is
    excluded: a follow-up turn legitimately repeats the previous answer's
    wording ("as stated above, murder is punishable…"), and user turns are
    the user's own words — echoing either is not leakage.
    """
    system_messages = [m for m in messages if str(getattr(m, "role", "")) == "system"]
    user_messages = [m for m in messages if str(getattr(m, "role", "")) == "user"]
    # The grounding prompt is the LAST user message; earlier user turns are
    # conversation history.
    return [*system_messages, *user_messages[-1:]]


def _longest_common_run(source: str, other: str) -> int:
    """Longest verbatim run of ``source`` present in ``other``.

    Slides a fixed-length window (step 10, so any run of ~70+ chars is
    guaranteed a hit; shorter quoting stays far under the threshold) over a
    whitespace-normalized ``source`` and reports the window length when a
    copy is found. Word-boundary agnostic: models re-wrap prompt lines, so
    line-exact matching would miss real echoes.
    """
    probe_source = " ".join(source.split()).lower()
    if len(probe_source) < _VERBATIM_MIN_LEN:
        return 0
    step = 10
    for start in range(0, len(probe_source) - _VERBATIM_MIN_LEN + 1, step):
        window = probe_source[start : start + _VERBATIM_MIN_LEN]
        if window in other:
            return _VERBATIM_MIN_LEN
    return 0


def sanitize_answer_text(text: str) -> str:
    """Provider-boundary text cleanup: strip reasoning wrappers only.

    Applied by every provider to the answer field it extracts. Echo
    detection is NOT applied here — it needs request context and lives in
    the generation layer (defense in depth, two independent checks).
    """
    return strip_reasoning_wrappers(text)


__all__ = [
    "ReasoningStreamFilter",
    "is_prompt_echo",
    "sanitize_answer_text",
    "strip_reasoning_wrappers",
]
