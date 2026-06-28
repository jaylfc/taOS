"""Unit tests for tinyagentos/channel_hub/message.py."""
from __future__ import annotations

import time

import pytest

from tinyagentos.channel_hub.message import (
    IncomingMessage,
    OutgoingMessage,
    parse_inline_hints,
)


# ---------------------------------------------------------------------------
# IncomingMessage
# ---------------------------------------------------------------------------

class TestIncomingMessage:
    def test_defaults_for_optional_fields(self):
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="hi",
        )
        assert msg.attachments == []
        assert msg.reply_to is None
        assert msg.raw == {}
        assert isinstance(msg.timestamp, float)

    def test_timestamp_is_auto_set(self):
        before = time.time()
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="hi",
        )
        after = time.time()
        assert before <= msg.timestamp <= after

    def test_explicit_timestamp(self):
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="hi",
            timestamp=1000.5,
        )
        assert msg.timestamp == 1000.5

    def test_all_fields_provided(self):
        raw = {"message_id": 42, "chat": {"id": 99}}
        msg = IncomingMessage(
            id="msg-1",
            from_id="user-42",
            from_name="Bob",
            platform="discord",
            channel_id="chan-7",
            channel_name="#dev",
            text="hello world",
            attachments=[{"type": "file", "url": "https://example.com/doc.pdf"}],
            reply_to="msg-0",
            timestamp=1234567890.0,
            raw=raw,
        )
        assert msg.id == "msg-1"
        assert msg.from_id == "user-42"
        assert msg.from_name == "Bob"
        assert msg.platform == "discord"
        assert msg.channel_id == "chan-7"
        assert msg.channel_name == "#dev"
        assert msg.text == "hello world"
        assert len(msg.attachments) == 1
        assert msg.attachments[0]["type"] == "file"
        assert msg.reply_to == "msg-0"
        assert msg.timestamp == 1234567890.0
        assert msg.raw is raw

    def test_empty_text(self):
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="",
        )
        assert msg.text == ""

    def test_empty_attachments(self):
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="hi",
            attachments=[],
        )
        assert msg.attachments == []

    def test_multiple_attachments(self):
        attachments = [
            {"type": "image", "url": "https://example.com/a.png"},
            {"type": "image", "url": "https://example.com/b.png"},
            {"type": "file", "url": "https://example.com/c.pdf"},
        ]
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="hi",
            attachments=attachments,
        )
        assert len(msg.attachments) == 3

    def test_all_platforms(self):
        for platform in ("telegram", "discord", "slack", "email", "web", "webhook"):
            msg = IncomingMessage(
                id="1",
                from_id="u1",
                from_name="Alice",
                platform=platform,
                channel_id="ch1",
                channel_name="General",
                text="hi",
            )
            assert msg.platform == platform

    def test_raw_dict_preserved(self):
        raw = {"nested": {"deep": True}, "list": [1, 2, 3]}
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Alice",
            platform="telegram",
            channel_id="ch1",
            channel_name="General",
            text="hi",
            raw=raw,
        )
        assert msg.raw["nested"]["deep"] is True
        assert msg.raw["list"] == [1, 2, 3]

    def test_default_raw_is_independent(self):
        msg1 = IncomingMessage(
            id="1", from_id="u1", from_name="A", platform="telegram",
            channel_id="ch1", channel_name="G", text="hi",
        )
        msg2 = IncomingMessage(
            id="2", from_id="u2", from_name="B", platform="telegram",
            channel_id="ch2", channel_name="G", text="hi",
        )
        msg1.raw["key"] = "val"
        assert "key" not in msg2.raw

    def test_default_attachments_are_independent(self):
        msg1 = IncomingMessage(
            id="1", from_id="u1", from_name="A", platform="telegram",
            channel_id="ch1", channel_name="G", text="hi",
        )
        msg2 = IncomingMessage(
            id="2", from_id="u2", from_name="B", platform="telegram",
            channel_id="ch2", channel_name="G", text="hi",
        )
        msg1.attachments.append({"type": "image"})
        assert len(msg2.attachments) == 0


# ---------------------------------------------------------------------------
# OutgoingMessage
# ---------------------------------------------------------------------------

class TestOutgoingMessage:
    def test_all_defaults(self):
        msg = OutgoingMessage()
        assert msg.content == ""
        assert msg.buttons == []
        assert msg.images == []
        assert msg.cards == []
        assert msg.reply_to is None
        assert msg.passthrough is False
        assert msg.passthrough_platform == ""
        assert msg.passthrough_payload == {}

    def test_content_only(self):
        msg = OutgoingMessage(content="Hello!")
        assert msg.content == "Hello!"
        assert msg.buttons == []
        assert msg.images == []
        assert msg.cards == []

    def test_with_buttons(self):
        buttons = [
            {"label": "Yes", "action": "yes"},
            {"label": "No", "action": "no"},
        ]
        msg = OutgoingMessage(content="Choose", buttons=buttons)
        assert len(msg.buttons) == 2
        assert msg.buttons[0] == {"label": "Yes", "action": "yes"}
        assert msg.buttons[1] == {"label": "No", "action": "no"}

    def test_with_images(self):
        images = ["/tmp/a.png", "https://example.com/b.jpg"]
        msg = OutgoingMessage(images=images)
        assert msg.images == ["/tmp/a.png", "https://example.com/b.jpg"]

    def test_with_cards(self):
        cards = [{"title": "Card 1", "body": "content"}]
        msg = OutgoingMessage(cards=cards)
        assert len(msg.cards) == 1
        assert msg.cards[0]["title"] == "Card 1"

    def test_reply_to(self):
        msg = OutgoingMessage(content="reply", reply_to="msg-42")
        assert msg.reply_to == "msg-42"

    def test_passthrough_fields(self):
        msg = OutgoingMessage(
            passthrough=True,
            passthrough_platform="telegram",
            passthrough_payload={"custom_key": "custom_val"},
        )
        assert msg.passthrough is True
        assert msg.passthrough_platform == "telegram"
        assert msg.passthrough_payload == {"custom_key": "custom_val"}

    def test_all_fields_provided(self):
        msg = OutgoingMessage(
            content="full test",
            buttons=[{"label": "OK", "action": "ok"}],
            images=["img.png"],
            cards=[{"title": "T"}],
            reply_to="ref-1",
            passthrough=True,
            passthrough_platform="discord",
            passthrough_payload={"k": "v"},
        )
        assert msg.content == "full test"
        assert len(msg.buttons) == 1
        assert len(msg.images) == 1
        assert len(msg.cards) == 1
        assert msg.reply_to == "ref-1"
        assert msg.passthrough is True
        assert msg.passthrough_platform == "discord"
        assert msg.passthrough_payload == {"k": "v"}

    def test_default_buttons_are_independent(self):
        msg1 = OutgoingMessage()
        msg2 = OutgoingMessage()
        msg1.buttons.append({"label": "X", "action": "x"})
        assert len(msg2.buttons) == 0

    def test_default_images_are_independent(self):
        msg1 = OutgoingMessage()
        msg2 = OutgoingMessage()
        msg1.images.append("a.png")
        assert len(msg2.images) == 0

    def test_default_cards_are_independent(self):
        msg1 = OutgoingMessage()
        msg2 = OutgoingMessage()
        msg1.cards.append({"title": "T"})
        assert len(msg2.cards) == 0

    def test_default_passthrough_payload_is_independent(self):
        msg1 = OutgoingMessage()
        msg2 = OutgoingMessage()
        msg1.passthrough_payload["key"] = "val"
        assert "key" not in msg2.passthrough_payload

    def test_empty_content(self):
        msg = OutgoingMessage(content="")
        assert msg.content == ""


# ---------------------------------------------------------------------------
# parse_inline_hints
# ---------------------------------------------------------------------------

class TestParseInlineHints:
    def test_plain_text_unchanged(self):
        result = parse_inline_hints("Just plain text")
        assert result.content == "Just plain text"
        assert result.buttons == []
        assert result.images == []

    def test_empty_string(self):
        result = parse_inline_hints("")
        assert result.content == ""
        assert result.buttons == []
        assert result.images == []

    def test_single_button(self):
        result = parse_inline_hints("Click [button:Yes:confirm_yes]")
        assert result.content == "Click"
        assert len(result.buttons) == 1
        assert result.buttons[0] == {"label": "Yes", "action": "confirm_yes"}

    def test_multiple_buttons(self):
        result = parse_inline_hints("[button:A:act_a] and [button:B:act_b]")
        assert result.content == "and"
        assert len(result.buttons) == 2
        assert result.buttons[0] == {"label": "A", "action": "act_a"}
        assert result.buttons[1] == {"label": "B", "action": "act_b"}

    def test_single_image(self):
        result = parse_inline_hints("See [image:/tmp/photo.jpg]")
        assert result.content == "See"
        assert result.images == ["/tmp/photo.jpg"]

    def test_multiple_images(self):
        result = parse_inline_hints("[image:a.png] text [image:b.png]")
        assert result.content == "text"
        assert result.images == ["a.png", "b.png"]

    def test_mixed_buttons_and_images(self):
        result = parse_inline_hints(
            "Result: [button:View:view] [image:https://example.com/pic.png]"
        )
        assert result.content == "Result:"
        assert len(result.buttons) == 1
        assert len(result.images) == 1
        assert result.buttons[0] == {"label": "View", "action": "view"}
        assert result.images == ["https://example.com/pic.png"]

    def test_only_button_no_surrounding_text(self):
        result = parse_inline_hints("[button:Click:action]")
        assert result.content == ""
        assert len(result.buttons) == 1

    def test_only_image_no_surrounding_text(self):
        result = parse_inline_hints("[image:https://cdn.example.com/cat.jpg]")
        assert result.content == ""
        assert result.images == ["https://cdn.example.com/cat.jpg"]

    def test_button_with_colons_in_action(self):
        result = parse_inline_hints("[button:Go:path:to:resource]")
        assert len(result.buttons) == 1
        assert result.buttons[0]["label"] == "Go"
        assert result.buttons[0]["action"] == "path:to:resource"

    def test_image_with_url(self):
        result = parse_inline_hints("[image:https://cdn.example.com/cat.jpg]")
        assert result.images == ["https://cdn.example.com/cat.jpg"]

    def test_image_with_absolute_path(self):
        result = parse_inline_hints("[image:/home/user/photo.png]")
        assert result.images == ["/home/user/photo.png"]

    def test_image_with_relative_path(self):
        result = parse_inline_hints("[image:./assets/img.png]")
        assert result.images == ["./assets/img.png"]

    def test_whitespace_stripped_from_content(self):
        result = parse_inline_hints("  [button:X:x]  hello  [image:a.png]  ")
        assert result.content == "hello"

    def test_no_hints_only_special_chars(self):
        result = parse_inline_hints("!@#$%^&*()")
        assert result.content == "!@#$%^&*()"
        assert result.buttons == []
        assert result.images == []

    def test_malformed_button_missing_bracket_not_matched(self):
        result = parse_inline_hints("text [button:NoEnd bracket")
        assert result.content == "text [button:NoEnd bracket"
        assert result.buttons == []

    def test_malformed_image_missing_bracket_not_matched(self):
        result = parse_inline_hints("text [image:NoEnd bracket")
        assert result.content == "text [image:NoEnd bracket"
        assert result.images == []

    def test_button_missing_action_not_matched(self):
        result = parse_inline_hints("[button:LabelOnly]")
        assert result.buttons == []
        assert result.content == "[button:LabelOnly]"

    def test_empty_button_label_not_matched(self):
        result = parse_inline_hints("[button::]")
        assert result.buttons == []
        assert result.content == "[button::]"

    def test_empty_image_path_not_matched(self):
        result = parse_inline_hints("[image:]")
        assert result.images == []
        assert result.content == "[image:]"

    def test_return_type_is_outgoing_message(self):
        result = parse_inline_hints("test")
        assert isinstance(result, OutgoingMessage)

    def test_result_has_no_cards(self):
        result = parse_inline_hints("text [button:A:a] [image:b.png]")
        assert result.cards == []

    def test_result_reply_to_is_none(self):
        result = parse_inline_hints("text")
        assert result.reply_to is None

    def test_result_passthrough_is_false(self):
        result = parse_inline_hints("text")
        assert result.passthrough is False

    def test_adjacent_hints_no_gap(self):
        result = parse_inline_hints("[button:A:a][image:b.png]")
        assert result.content == ""
        assert len(result.buttons) == 1
        assert len(result.images) == 1

    def test_three_buttons(self):
        result = parse_inline_hints(
            "[button:X:x1] [button:Y:x2] [button:Z:x3]"
        )
        assert result.content == ""
        assert len(result.buttons) == 3
        assert result.buttons[2] == {"label": "Z", "action": "x3"}

    def test_text_before_and_after_hints(self):
        result = parse_inline_hints("before [button:Go:go] after")
        assert result.content == "before  after"
        assert len(result.buttons) == 1

    def test_unicode_content(self):
        result = parse_inline_hints("héllo wörld [button:OK:ok]")
        assert result.content == "héllo wörld"
        assert result.buttons[0]["label"] == "OK"

    def test_unicode_button_label_and_action(self):
        result = parse_inline_hints("[button:日本語:アクション]")
        assert result.buttons[0] == {"label": "日本語", "action": "アクション"}

    def test_unicode_image_path(self):
        result = parse_inline_hints("[image:/tmp/日本語.png]")
        assert result.images == ["/tmp/日本語.png"]

    def test_newlines_in_text(self):
        result = parse_inline_hints("line1\nline2 [button:Next:next]")
        assert result.content == "line1\nline2"
        assert len(result.buttons) == 1

    def test_only_whitespace_content_becomes_empty(self):
        result = parse_inline_hints("   ")
        assert result.content == ""

    def test_hint_with_spaces_in_label(self):
        result = parse_inline_hints("[button:Click Here:action]")
        assert result.buttons[0]["label"] == "Click Here"
        assert result.buttons[0]["action"] == "action"
