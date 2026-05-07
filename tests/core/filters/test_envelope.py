"""Tests for core.filters.envelope — T2.0a envelope stripping."""

from __future__ import annotations

import pytest

from itsme.core.filters.envelope import has_envelopes, strip_envelopes


# ---------------------------------------------------------------- basic cases


class TestStripEnvelopes:
    def test_no_envelopes_unchanged(self) -> None:
        text = "I decided to use Postgres for the project."
        assert strip_envelopes(text) == text

    def test_empty_string(self) -> None:
        assert strip_envelopes("") == ""

    def test_single_command_name(self) -> None:
        text = "before\n<command-name>exit</command-name>\nafter"
        assert strip_envelopes(text) == "before\n\nafter"

    def test_single_command_args(self) -> None:
        text = "before\n<command-args>{}</command-args>\nafter"
        assert strip_envelopes(text) == "before\n\nafter"

    def test_command_message_multiline(self) -> None:
        text = (
            "user said hi\n"
            "<command-message>\n"
            "This is a multi-line\n"
            "command message block.\n"
            "</command-message>\n"
            "user said bye"
        )
        result = strip_envelopes(text)
        assert "command-message" not in result
        assert "user said hi" in result
        assert "user said bye" in result

    def test_local_command_caveat(self) -> None:
        text = "<local-command-caveat>Some caveat text</local-command-caveat> real content"
        result = strip_envelopes(text)
        assert result == "real content"

    def test_local_command_stdout(self) -> None:
        text = "prefix\n<local-command-stdout>\nstdout line 1\nline 2\n</local-command-stdout>\nsuffix"
        result = strip_envelopes(text)
        assert "stdout" not in result
        assert "prefix" in result
        assert "suffix" in result


class TestMultipleEnvelopes:
    def test_all_five_tags(self) -> None:
        text = (
            "real content 1\n"
            "<local-command-caveat>caveat</local-command-caveat>\n"
            "<command-name>exit</command-name>\n"
            "<command-message>msg</command-message>\n"
            "<command-args>{\"key\": \"val\"}</command-args>\n"
            "<local-command-stdout>out</local-command-stdout>\n"
            "real content 2"
        )
        result = strip_envelopes(text)
        assert result == "real content 1\n\nreal content 2"

    def test_repeated_same_tag(self) -> None:
        text = (
            "<command-name>exit</command-name>"
            " middle "
            "<command-name>clear</command-name>"
        )
        result = strip_envelopes(text)
        assert result == "middle"

    def test_interleaved_with_content(self) -> None:
        text = (
            "## Decision\n"
            "We chose Postgres.\n"
            "<command-name>exit</command-name>\n"
            "<command-args>{}</command-args>\n"
            "\n"
            "## Rationale\n"
            "Better concurrent writes."
        )
        result = strip_envelopes(text)
        assert "## Decision" in result
        assert "Postgres" in result
        assert "## Rationale" in result
        assert "command-name" not in result


class TestEdgeCases:
    def test_nested_angle_brackets_in_content(self) -> None:
        """Tags in regular text (like HTML/XML examples) should NOT be stripped."""
        text = "Use <div> tags in your HTML. Also <span>inline</span>."
        assert strip_envelopes(text) == text

    def test_partial_tag_not_stripped(self) -> None:
        """An unclosed envelope tag should NOT cause infinite matching."""
        text = "<command-name>exit without closing"
        assert strip_envelopes(text) == text

    def test_idempotent(self) -> None:
        text = "before <command-name>exit</command-name> after"
        once = strip_envelopes(text)
        twice = strip_envelopes(once)
        assert once == twice

    def test_blank_line_collapse(self) -> None:
        text = "a\n\n\n<command-name>x</command-name>\n\n\n\nb"
        result = strip_envelopes(text)
        # Should not have more than one blank line in a row
        assert "\n\n\n" not in result
        assert "a" in result
        assert "b" in result

    def test_only_envelopes_returns_empty(self) -> None:
        text = "<command-name>exit</command-name>\n<command-args>{}</command-args>"
        assert strip_envelopes(text) == ""

    def test_cjk_content_preserved(self) -> None:
        text = "用户决定使用 Postgres\n<command-name>exit</command-name>\n数据库选型完成"
        result = strip_envelopes(text)
        assert "用户决定使用 Postgres" in result
        assert "数据库选型完成" in result
        assert "command-name" not in result


# ---------------------------------------------------------------- has_envelopes


class TestHasEnvelopes:
    def test_has_envelope_true(self) -> None:
        assert has_envelopes("<command-name>exit</command-name>")

    def test_has_envelope_false(self) -> None:
        assert not has_envelopes("regular text")

    def test_has_envelope_partial_false(self) -> None:
        assert not has_envelopes("<command-name>unclosed")


# ----------------------------------------- integration: hook transcript parsing


class TestHookIntegration:
    """Verify envelope stripping is wired into the hook transcript parser."""

    def test_extract_message_text_strips_envelopes(self) -> None:
        from itsme.hooks._common import _extract_message_text

        entry = {
            "type": "assistant",
            "message": {
                "content": (
                    "Here is my answer.\n"
                    "<command-name>exit</command-name>\n"
                    "<command-args>{}</command-args>\n"
                    "Some trailing text."
                ),
            },
        }
        text = _extract_message_text(entry)
        assert "command-name" not in text
        assert "command-args" not in text
        assert "Here is my answer." in text
        assert "Some trailing text." in text

    def test_extract_message_text_content_blocks_strips(self) -> None:
        from itsme.hooks._common import _extract_message_text

        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Good point."},
                    {
                        "type": "text",
                        "text": "<local-command-stdout>foo</local-command-stdout>",
                    },
                ],
            },
        }
        text = _extract_message_text(entry)
        assert "Good point." in text
        assert "local-command-stdout" not in text
