"""Tests for reading.frontmatter."""

from __future__ import annotations

from auto_lorebook.reading.frontmatter import join_frontmatter, split_frontmatter

# ── split_frontmatter ─────────────────────────────────────────────────────────


def test_split_no_frontmatter() -> None:
    """Content without fence returns ({}, original)."""
    content = "# Just a heading\nsome body"
    fm, body = split_frontmatter(content)
    assert fm == {}
    assert body == content


def test_split_basic() -> None:
    """Valid frontmatter split correctly."""
    content = "---\nkey: value\n---\nbody text\n"
    fm, body = split_frontmatter(content)
    assert fm == {"key": "value"}
    assert body == "body text\n"


def test_split_no_closing_fence() -> None:
    """Missing closing fence returns ({}, original)."""
    content = "---\nkey: value\n"
    fm, body = split_frontmatter(content)
    assert fm == {}
    assert body == content


def test_split_preserves_body() -> None:
    """Body content preserved exactly after split."""
    content = "---\nschema_version: 1\n---\n# Header\n\nParagraph.\n"
    fm, body = split_frontmatter(content)
    assert fm["schema_version"] == 1
    assert body == "# Header\n\nParagraph.\n"


def test_split_multiple_keys() -> None:
    """Multiple frontmatter keys all parsed."""
    content = (
        "---\nschema_version: 1\nsource_id: srt-abc\nreading_status: draft\n---\nbody\n"
    )
    fm, _ = split_frontmatter(content)
    assert fm["source_id"] == "srt-abc"
    assert fm["reading_status"] == "draft"


def test_split_empty_frontmatter() -> None:
    """Empty frontmatter block returns {} and body."""
    content = "---\n---\nbody\n"
    fm, body = split_frontmatter(content)
    assert fm == {}
    assert body == "body\n"


# ── join_frontmatter ──────────────────────────────────────────────────────────


def test_join_produces_fence() -> None:
    """Joined content starts and ends with --- fence."""
    result = join_frontmatter({"key": "val"}, "body\n")
    assert result.startswith("---\n")
    assert "---\n" in result[4:]


def test_join_roundtrip() -> None:
    """split(join(fm, body)) == (fm, body)."""
    original_fm: dict[str, object] = {
        "schema_version": 1,
        "source_id": "srt-abc123",
        "reading_status": "draft",
    }
    body = "# Segment 1\n\n- Bullet one\n"
    content = join_frontmatter(original_fm, body)
    recovered_fm, recovered_body = split_frontmatter(content)
    assert recovered_fm == original_fm
    assert recovered_body == body


def test_join_empty_body() -> None:
    """Empty body produces valid content."""
    result = join_frontmatter({"x": 1}, "")
    fm, body = split_frontmatter(result)
    assert fm == {"x": 1}
    assert not body
