"""Offline tests for document helpers."""
from bot.documents import compare_url, normalize_pdf
from bot.inputs import RunInputs
from bot.util import without_query


def inputs(head_repo="me/repo", head_branch="feature/x"):
    return RunInputs("me/repo", "main", head_repo, head_branch, "t")


def test_compare_url_same_repo():
    assert compare_url(inputs()) == "https://github.com/me/repo/compare/main...feature/x"


def test_compare_url_other_repo():
    url = compare_url(inputs(head_repo="you/fork", head_branch="fix"))
    assert url.endswith("/compare/main...you:fork:fix")


def test_without_query():
    assert without_query("https://github.com/a/b/compare/x...y?expand=1#top") == \
        "https://github.com/a/b/compare/x...y"


def test_pdf_dates_are_zeroed_without_changing_size(tmp_path):
    pdf = tmp_path / "a.pdf"
    original = b"%PDF-1.4\n<< /CreationDate (D:20261024221500+05'30') /ModDate (D:20261024221501) >>\n%%EOF"
    pdf.write_bytes(original)
    normalize_pdf(pdf)
    fixed = pdf.read_bytes()
    assert len(fixed) == len(original)
    assert b"D:00000000000000+05'30'" in fixed and b"D:00000000000000)" in fixed
    other = tmp_path / "b.pdf"
    other.write_bytes(original.replace(b"221500", b"235959"))
    normalize_pdf(other)
    assert other.read_bytes() == fixed
