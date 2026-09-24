"""Retrieved chunks -> a grounded, cited answer from Claude.

The context sent to Claude is bounded by `context_char_budget` (roughly 15k
tokens of code). Every line of code carries its real line number, so
Claude's citations are copied, not guessed. After the call, we check each
`file:line` citation in the answer against what Claude was actually shown.
Any that point at code outside it are marked [unverified] inline, with a
caveat, before the answer is returned.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

import anthropic

from config import settings

from .retrieve import Retrieval, Retrieved
from .store import RepoIndex

SYSTEM_PROMPT = """\
You answer questions about a code repository using only the code excerpts \
provided in <context>. The excerpts were selected by a search system and may be \
incomplete or irrelevant. You have no other knowledge of this repository.

Ground every claim in the excerpts, citing the file path and line numbers \
shown in the left margin, in the form path/to/file.py:12-30 (or \
path/to/file.py:12 for a single line). Don't cite anything that isn't in the \
excerpts, and don't fill gaps with what the code "probably" does elsewhere. \
Separate excerpts from the same file aren't contiguous; the lines between them \
weren't shown to you. So cite each excerpt by its own line numbers, never one \
range that spans several excerpts.

If the excerpts don't contain the answer, say so plainly: say which files you \
checked and what's missing, rather than guessing. You don't need to cite line \
ranges for excerpts that turned out to be irrelevant; naming the files is enough. \
A partial answer that's clearly marked as partial is better than a \
complete-sounding one that isn't supported.

End with a line starting "Files consulted:" listing the files you actually \
relied on. After it, on a final line of its own, write exactly one of:
Answer status: answered  (the excerpts answer the question)
Answer status: partial  (the excerpts answer part of it, and some of what was asked is missing)
Answer status: not_found  (the excerpts don't contain what was asked; use this even if you can't rule \
out that it exists in files you weren't shown)"""

_STATUS_LINE = re.compile(
    r"\n?[ \t*_]*Answer status:[ \t*_]*(answered|partial|not_found)[ \t*_.]*\Z", re.IGNORECASE
)


def split_status(text: str) -> tuple[str, str | None]:
    """Strip the trailing "Answer status: ..." line; return (text, status or None)."""
    text = text.rstrip()
    m = _STATUS_LINE.search(text)
    if not m:
        return text, None
    return text[: m.start()].rstrip(), m[1].lower()

_NOTE_RANGE = re.compile(r"L(\d+)-(\d+)")

_CITATION = re.compile(
    r"(?P<file>[A-Za-z0-9_./@+\-]+\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|mts|cts))"
    r":(?P<start>\d+)(?:\s*[-–]\s*(?P<end>\d+))?"
)


class MissingCredentials(Exception):
    pass


@dataclass
class ContextChunk:
    ref: int
    item: Retrieved
    shown_start: int
    shown_end: int  # smaller than chunk.end_line if the chunk was truncated for space


@dataclass
class Citation:
    file: str
    start: int
    end: int
    valid: bool
    # Why a citation counts as valid:
    #   "excerpt":     every line of code in the range was shown to the model.
    #   "class_index": an exact member range from a split class's index note. A
    #                  real, parser-generated location, but its code wasn't shown.
    #   None:          invalid; it points at code the model wasn't given.
    basis: str | None = None


@dataclass
class Answer:
    text: str
    context: list[ContextChunk]
    citations: list[Citation]
    model: str | None
    stop_reason: str | None
    usage: dict | None
    # answered | partial | not_found | refused, as stated by the model (None if it didn't)
    status: str | None = None


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def build_context(retrieval: Retrieval, budget: int) -> tuple[str, list[ContextChunk]]:
    parts, used, remaining = [], [], budget
    for ref, item in enumerate(retrieval.results, start=1):
        c = item.chunk
        lines = c.text.split("\n")
        numbered, shown_end, size = [], c.start_line - 1, 0
        for offset, line in enumerate(lines):
            rendered = f"{c.start_line + offset:>5}| {line}"
            if size + len(rendered) + 1 > remaining - 400:
                break
            numbered.append(rendered)
            size += len(rendered) + 1
            shown_end = c.start_line + offset
        if not numbered:
            break  # out of budget
        truncated = shown_end < c.end_line
        attrs = (
            f'ref="{ref}" file="{c.path}" lines="{c.start_line}-{c.end_line}" symbol="{c.symbol}" '
            f'kind="{c.kind}" retrieved_by="{item.source}: {item.reason}"'
        )
        body = "\n".join(numbered)
        if truncated:
            body += f"\n  ... [truncated for space; this {c.kind} continues to line {c.end_line}]"
        note = f"<note>{c.note}</note>\n" if c.note else ""
        block = f"<excerpt {attrs}>\n{note}{body}\n</excerpt>"
        parts.append(block)
        remaining -= len(block)
        used.append(ContextChunk(ref, item, c.start_line, shown_end))
    return "\n\n".join(parts), used


def _in_any(line: int, ranges: list[tuple[int, int]]) -> bool:
    return any(s <= line <= e for s, e in ranges)


def _citation_is_valid(file: str, start: int, end: int, shown: dict, code: dict) -> bool:
    """True if every line of code in the cited range was shown to the model.

    Lines that belong to no indexed chunk (blank lines, or comments between
    definitions) carry no content, so a range that spans two adjacent excerpts
    across a blank line is still accurate. Without `code` for a file, every
    cited line must have been shown.
    """
    shown_ranges, code_ranges = shown.get(file, []), code.get(file)
    saw_any = False
    for line in range(start, end + 1):
        if _in_any(line, shown_ranges):
            saw_any = True
        elif code_ranges is None or _in_any(line, code_ranges):
            return False  # this line holds code the model never saw
    return saw_any


def _citation_matches(text: str):
    for m in _CITATION.finditer(text):
        file = m["file"].removeprefix("./")
        start = int(m["start"])
        end = int(m["end"]) if m["end"] else start
        yield m, file, start, end


def check_citations(
    text: str, context: list[ContextChunk], code_ranges: dict[str, list[tuple[int, int]]] | None = None
) -> list[Citation]:
    """Check every `file:line` citation against the lines the model was shown.

    `code_ranges` maps each file to the line ranges of all its indexed chunks,
    which tells us which uncovered lines are code and which are just blank.
    """
    shown: dict[str, list[tuple[int, int]]] = {}
    listed: set[tuple[str, int, int]] = set()  # member ranges named in class-index notes
    for cc in context:
        chunk = cc.item.chunk
        shown.setdefault(chunk.path, []).append((cc.shown_start, cc.shown_end))
        for m in _NOTE_RANGE.finditer(chunk.note):
            listed.add((chunk.path, int(m[1]), int(m[2])))
    code = code_ranges or {}
    citations, seen = [], set()
    for _, file, start, end in _citation_matches(text):
        if (file, start, end) in seen:
            continue
        seen.add((file, start, end))
        if _citation_is_valid(file, start, end, shown, code):
            basis = "excerpt"
        elif (file, start, end) in listed:
            basis = "class_index"
        else:
            basis = None
        citations.append(Citation(file, start, end, basis is not None, basis))
    return citations


UNVERIFIED_MARK = " [unverified]"


def flag_unverified(text: str, citations: list[Citation]) -> str:
    """Mark each invalid citation inline and add a caveat, so no unverified
    reference reaches the reader looking like a checked one.

    We mark rather than delete: removing a citation mid-sentence leaves text
    like "an example CLI ()".
    """
    invalid = {(c.file, c.start, c.end) for c in citations if not c.valid}
    if not invalid:
        return text
    out, last = [], 0
    for m, file, start, end in _citation_matches(text):
        if (file, start, end) not in invalid:
            continue
        cut = m.end()
        if text[cut : cut + 1] == "`":  # keep the mark outside an inline code span
            cut += 1
        out.append(text[last:cut] + UNVERIFIED_MARK)
        last = cut
    out.append(text[last:])
    if len(invalid) == 1:
        note = ("1 citation marked [unverified] points to lines that weren't in the code excerpts "
                "provided, so it couldn't be checked against the code. Treat it as approximate.")
    else:
        note = (f"{len(invalid)} citations marked [unverified] point to lines that weren't in the code "
                "excerpts provided, so they couldn't be checked against the code. Treat them as approximate.")
    return "".join(out) + "\n\nNote: " + note


def answer_question(index: RepoIndex, question: str, retrieval: Retrieval) -> Answer:
    context_text, context = build_context(retrieval, settings.context_char_budget)
    if not context:
        return Answer(
            "This repository has no indexed code to search, so there is nothing to answer from.",
            [], [], None, None, None, status="not_found",
        )

    user = (
        f"<repository>{index.repo_id} @ {index.commit[:12]}</repository>\n"
        f"<context>\n{context_text}\n</context>\n\n"
        f"<question>{question}</question>"
    )
    try:
        response = _client().beta.messages.create(
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.claude_effort},
            # If a safety classifier declines, retry server-side on Anthropic's
            # recommended fallback model instead of returning an empty refusal.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
    except TypeError as e:
        # With no key, token, or profile configured, the SDK raises TypeError
        # before sending anything.
        if "authentication method" in str(e):
            raise MissingCredentials(str(e)) from e
        raise

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        text = f"The model declined to answer this question (refusal category: {category})."
        status = "refused"
    else:
        text, status = split_status("".join(b.text for b in response.content if b.type == "text"))
        if response.stop_reason == "max_tokens":
            text += "\n\n[Answer truncated: hit the max_tokens limit.]"

    files = {cc.item.chunk.path for cc in context}
    code_ranges: dict[str, list[tuple[int, int]]] = {}
    for chunk in index.chunks:
        if chunk.path in files:
            code_ranges.setdefault(chunk.path, []).append((chunk.start_line, chunk.end_line))
    citations = check_citations(text, context, code_ranges)

    return Answer(
        text=flag_unverified(text, citations),
        context=context,
        citations=citations,
        model=response.model,
        stop_reason=response.stop_reason,
        usage={"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
        status=status,
    )
