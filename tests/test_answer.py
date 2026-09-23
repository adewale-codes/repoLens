from services.answer import build_context, check_citations
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
