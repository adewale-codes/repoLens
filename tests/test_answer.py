from services.answer import Citation, build_context, check_citations, flag_unverified
from services.parsers import Chunk
from services.retrieve import Retrieval, Retrieved


def make_retrieval():
    body = "\n".join(f"    line {i}" for i in range(2, 41))
    chunk = Chunk("src/app.py", "python", "function", "run", 10, 49, "def run():\n" + body)
    return Retrieval([Retrieved(chunk, 0.9, "vector", "similarity 0.900")], 0.9)


def test_context_carries_real_line_numbers():
    text, used = build_context(make_retrieval(), budget=100_000)
    assert '<excerpt ref="1" file="src/app.py" lines="10-49"' in text
    assert "   10| def run():" in text
    assert "   49|     line 40" in text
    assert (used[0].shown_start, used[0].shown_end) == (10, 49)


def test_context_truncates_to_budget_and_says_so():
    text, used = build_context(make_retrieval(), budget=800)
    assert used[0].shown_end < 49
    assert "truncated for space" in text


def test_citations_are_checked_against_shown_lines():
    _, used = build_context(make_retrieval(), budget=100_000)
    answer = "Starts in src/app.py:10-12, loops at src/app.py:30. Also src/app.py:60-70 and lib/other.js:5."
    got = {(c.file, c.start, c.end): c.valid for c in check_citations(answer, used)}
    assert got == {
        ("src/app.py", 10, 12): True,
        ("src/app.py", 30, 30): True,
        ("src/app.py", 60, 70): False,  # outside what was shown
        ("lib/other.js", 5, 5): False,  # file never shown
    }


def _context_for(chunks):
    retrieval = Retrieval([Retrieved(c, 0.5, "vector", "") for c in chunks], 0.5)
    return build_context(retrieval, budget=100_000)[1]


def _chunk(start, end, symbol="f"):
    text = "\n".join(f"x{i}" for i in range(start, end + 1))
    return Chunk("examples/naval.py", "python", "function", symbol, start, end, text)


def test_range_spanning_excerpts_across_blank_lines_is_valid():
    # Real case: testing.py:317-380 cited from excerpts 317-358 and 360-380; line 359 is blank.
    shown = [_chunk(1, 1), _chunk(4, 12)]
    code = {"examples/naval.py": [(1, 1), (4, 12)]}  # lines 2-3 belong to no chunk
    [c] = check_citations("see examples/naval.py:1-12", _context_for(shown), code)
    assert c.valid


def test_range_spanning_unshown_code_is_invalid():
    # Real case: naval.py:1-24 cited from excerpts 1, 4-12, 20-24; lines 15-17 are an unshown function.
    shown = [_chunk(1, 1), _chunk(4, 12), _chunk(20, 24)]
    code = {"examples/naval.py": [(1, 1), (4, 12), (15, 17), (20, 24)]}
    [c] = check_citations("see examples/naval.py:1-24", _context_for(shown), code)
    assert not c.valid


def test_without_code_ranges_every_cited_line_must_be_shown():
    [c] = check_citations("see examples/naval.py:1-12", _context_for([_chunk(1, 1), _chunk(4, 12)]))
    assert not c.valid


def test_invalid_citations_are_flagged_inline_with_a_caveat():
    text = "An example CLI (`examples/naval.py:1-24`) and examples/naval.py:4-12 again at examples/naval.py:1-24."
    citations = [Citation("examples/naval.py", 1, 24, False), Citation("examples/naval.py", 4, 12, True)]
    flagged = flag_unverified(text, citations)
    assert flagged.startswith(
        "An example CLI (`examples/naval.py:1-24` [unverified]) and examples/naval.py:4-12 again at "
        "examples/naval.py:1-24 [unverified]."
    )
    assert "Note: 1 citation marked [unverified]" in flagged
    assert flag_unverified("clean examples/naval.py:4-12", citations[1:]) == "clean examples/naval.py:4-12"


def test_exact_member_range_from_class_index_is_a_real_location():
    # Real case: the answer cited make_context (core.py:1355-1390) from Command's
    # class-summary note, saying outright that its body wasn't shown.
    header = Chunk("src/core.py", "python", "class_summary", "Command", 985, 990, "class Command:\n" * 6,
                   note="Class Command spans L985-1681. Members: make_context (L1355-1390), invoke (L1428-1442)")
    context = _context_for([header])
    code = {"src/core.py": [(985, 990), (1355, 1390), (1428, 1442)]}
    got = {(c.start, c.end): (c.valid, c.basis) for c in check_citations(
        "header src/core.py:985-990; make_context src/core.py:1355-1390; part of it src/core.py:1360-1370",
        context, code)}
    assert got == {
        (985, 990): (True, "excerpt"),
        (1355, 1390): (True, "class_index"),
        (1360, 1370): (False, None),  # not an exact listed range, and never shown
    }


def test_answer_question_end_to_end_with_stubbed_client(monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from services import answer
    from services.store import RepoIndex

    shown, unshown = _chunk(4, 12, "cli"), _chunk(15, 17, "ship")
    index = RepoIndex("o/r", "abc123def4567", "m", [shown, unshown], np.zeros((2, 1), np.float32), [])
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        text = "The CLI group is at examples/naval.py:4-12, see also examples/naval.py:4-17."
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", stop_details=None,
            model="claude-opus-5", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(answer, "_client", lambda: client)

    result = answer.answer_question(index, "what is the cli?", Retrieval([Retrieved(shown, 0.9, "vector", "")], 0.9))
    assert "<repository>o/r @ abc123def456</repository>" in requests[0]["messages"][0]["content"]
    assert [(c.start, c.end, c.basis) for c in result.citations] == [(4, 12, "excerpt"), (4, 17, None)]
    assert "examples/naval.py:4-17 [unverified]" in result.text
    assert "examples/naval.py:4-12 [unverified]" not in result.text
