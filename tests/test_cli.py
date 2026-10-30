from evidentai.cli import main
from tests.conftest import md_path


def test_parse_tree(capsys):
    assert main(["parse", str(md_path("3.0", "auth-guide")), "--tree"]) == 0
    out = capsys.readouterr().out
    assert "parser=markdown-it" in out and "sections=14" in out
    assert "    Requesting Access Tokens" in out


def test_parse_chunks_on_pdf(pdf_dir, capsys):
    assert main(["parse", str(pdf_dir / "v3.0" / "auth-guide.pdf"), "--chunks"]) == 0
    out = capsys.readouterr().out
    assert "chunks (max_words=300)" in out and " p1-1 " in out


def test_parse_error_exit_code(tmp_path, capsys):
    bad = tmp_path / "notes.txt"
    bad.write_text("hi")
    assert main(["parse", str(bad)]) == 2
    assert "error[unsupported_file_type]" in capsys.readouterr().err
