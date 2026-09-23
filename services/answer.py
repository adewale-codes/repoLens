"""Retrieved chunks -> a grounded, cited answer from Claude.

The context sent to Claude is bounded by `context_char_budget` (roughly 15k
tokens of code). Every line of code carries its real line number, so
Claude's citations are copied, not guessed. After the call, we check each
`file:line` citation in the answer against what Claude was actually shown and
flag any that point outside it.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

import anthropic

from config import settings

from .retrieve import Retrieval, Retrieved

SYSTEM_PROMPT = """\
You answer questions about a code repository using only the code excerpts \
provided in <context>. The excerpts were selected by a search system and may be \
incomplete or irrelevant. You have no other knowledge of this repository.

Ground every claim in the excerpts, citing the file path and line numbers \
shown in the left margin, in the form path/to/file.py:12-30 (or \
path/to/file.py:12 for a single line). Don't cite anything that isn't in the \
excerpts, and don't fill gaps with what the code "probably" does elsewhere.

If the excerpts don't contain the answer, say so plainly: say what you looked \
at and what's missing, rather than guessing. A partial answer that's clearly \
marked as partial is better than a complete-sounding one that isn't supported.

End with a line starting "Files consulted:" listing the files you actually \
relied on."""

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
    valid: bool  # the cited lines were shown to the model


@dataclass
class Answer:
    text: str
    context: list[ContextChunk]
    citations: list[Citation]
    model: str | None
    stop_reason: str | None
    usage: dict | None


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


def check_citations(text: str, context: list[ContextChunk]) -> list[Citation]:
    shown: dict[str, list[tuple[int, int]]] = {}
    for cc in context:
        shown.setdefault(cc.item.chunk.path, []).append((cc.shown_start, cc.shown_end))
    citations, seen = [], set()
    for m in _CITATION.finditer(text):
        file, start = m["file"].lstrip("./"), int(m["start"])
        end = int(m["end"]) if m["end"] else start
        if (file, start, end) in seen:
            continue
        seen.add((file, start, end))
        valid = any(s <= start and end <= e for s, e in shown.get(file, ()))
        citations.append(Citation(file, start, end, valid))
    return citations


def answer_question(repo_id: str, commit: str, question: str, retrieval: Retrieval) -> Answer:
    context_text, context = build_context(retrieval, settings.context_char_budget)
    if not context:
        return Answer(
            "This repository has no indexed code to search, so there is nothing to answer from.",
            [], [], None, None, None,
        )

    user = (
        f"<repository>{repo_id} @ {commit[:12]}</repository>\n"
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
    else:
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "max_tokens":
            text += "\n\n[Answer truncated: hit the max_tokens limit.]"

    return Answer(
        text=text,
        context=context,
        citations=check_citations(text, context),
        model=response.model,
        stop_reason=response.stop_reason,
        usage={"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
    )
