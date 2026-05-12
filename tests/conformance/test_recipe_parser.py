from pathlib import Path

from tests.conformance._recipe_parser import extract_blocks


def test_extract_bash_and_http(tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    md.write_text(
        "# Recipe\n"
        "\n"
        "First a bash:\n"
        "\n"
        "```bash\n"
        "curl http://localhost/api\n"
        "```\n"
        "\n"
        "Skipped (destructive):\n"
        "\n"
        "```bash-skip\n"
        "rm -rf /\n"
        "```\n"
        "\n"
        "And an http block:\n"
        "\n"
        "```http\n"
        "GET /api HTTP/1.1\n"
        "```\n"
    )
    blocks = extract_blocks(md)
    assert len(blocks) == 2
    assert blocks[0]["lang"] == "bash"
    assert "curl" in blocks[0]["code"]
    assert blocks[1]["lang"] == "http"
    assert "GET /api" in blocks[1]["code"]


def test_extract_skips_unknown_lang(tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    md.write_text("```json\n{}\n```\n```python\nprint(1)\n```\n")
    assert extract_blocks(md) == []


def test_extract_skips_http_skip(tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    md.write_text("```http-skip\nGET /\n```\n")
    assert extract_blocks(md) == []


def test_extract_empty_file(tmp_path: Path) -> None:
    md = tmp_path / "empty.md"
    md.write_text("# Empty\n")
    assert extract_blocks(md) == []


def test_extract_line_numbers_1_indexed(tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    md.write_text(
        "# heading\n"          # line 1
        "\n"                    # line 2
        "para\n"                # line 3
        "\n"                    # line 4
        "```bash\n"             # line 5  ← opening fence at 1-indexed 5
        "echo hi\n"             # line 6
        "```\n"                 # line 7
    )
    blocks = extract_blocks(md)
    assert blocks[0]["line_no"] == 5


def test_extract_two_consecutive_blocks(tmp_path: Path) -> None:
    md = tmp_path / "r.md"
    md.write_text(
        "```bash\n"
        "a\n"
        "```\n"
        "```http\n"
        "GET / HTTP/1.1\n"
        "```\n"
    )
    blocks = extract_blocks(md)
    assert len(blocks) == 2
    assert blocks[0]["lang"] == "bash"
    assert blocks[1]["lang"] == "http"
