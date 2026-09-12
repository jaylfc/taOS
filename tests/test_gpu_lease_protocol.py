"""Unit tests for the A2A GPU lease protocol (taOS #893).

The protocol is the cross-product contract between agents sharing one GPU:
CHECK before loading, CLAIM before loading, RELEASE when done. These tests pin
the wire format, the fold (which claims are still open), and the admission
rules that stop a silent co-load past the card's VRAM.
"""
from __future__ import annotations

import time

from tinyagentos.gpu_lease import (
    CLAIM,
    RELEASE,
    REQUEST,
    Admission,
    claims_for_node,
    evaluate_admission,
    format_vram_mb,
    open_claims,
    parse_message,
    parse_vram_mb,
    render_check,
    render_claim,
    render_release,
    render_request,
    same_holder,
)


class TestVramParsing:
    def test_gigabytes_convert_to_mib(self):
        assert parse_vram_mb("~9.4gb") == 9626
        assert parse_vram_mb("6gb") == 6144
        assert parse_vram_mb("1GB") == 1024

    def test_megabytes_and_bare_numbers_are_mib(self):
        assert parse_vram_mb("4096mb") == 4096
        assert parse_vram_mb("4096") == 4096
        assert parse_vram_mb("512M") == 512

    def test_unparseable_is_none_not_zero(self):
        # None ("no figure given") must stay distinct from a real 0: a claim
        # with a garbled vram figure must not be read as "needs nothing".
        assert parse_vram_mb(None) is None
        assert parse_vram_mb("lots") is None
        assert parse_vram_mb("") is None

    def test_format_round_trips_through_parse(self):
        for mb in (512, 1024, 6144, 9626):
            assert parse_vram_mb(format_vram_mb(mb)) == mb

    def test_format_uses_gb_above_a_gib(self):
        assert format_vram_mb(6144) == "~6gb"
        assert format_vram_mb(512) == "512mb"


class TestParseMessage:
    def test_claim_line_full(self):
        msg = parse_message(
            "[GPU CLAIM] node=linstation holder=@taOSmd vram=~9.4gb "
            "reason=ollama-models eta=~10m"
        )
        assert msg is not None
        assert msg.kind == CLAIM
        assert msg.node == "linstation"
        assert msg.holder == "@taOSmd"
        assert msg.vram_mb == 9626
        assert msg.reason == "ollama-models"
        assert msg.eta == "~10m"

    def test_release_and_request_lines(self):
        rel = parse_message("[GPU RELEASE] node=linstation holder=@taOSmd")
        assert (rel.kind, rel.node, rel.holder) == (RELEASE, "linstation", "@taOSmd")
        req = parse_message("[GPU REQUEST] node=linstation need=~6gb")
        assert req.kind == REQUEST
        assert req.vram_mb == 6144

    def test_field_order_is_not_significant(self):
        msg = parse_message("[GPU CLAIM] vram=4096mb node=n1 holder=@a")
        assert (msg.node, msg.holder, msg.vram_mb) == ("n1", "@a", 4096)

    def test_value_may_contain_spaces(self):
        msg = parse_message(
            "[GPU CLAIM] node=n1 holder=@a vram=6gb reason=flux image generation"
        )
        assert msg.reason == "flux image generation"

    def test_kind_is_case_insensitive(self):
        assert parse_message("[gpu claim] node=n1 holder=@a vram=1gb").kind == CLAIM

    def test_expires_is_parsed_as_an_absolute_instant(self):
        msg = parse_message(
            "[GPU CLAIM] node=n1 holder=@a vram=6gb expires=1783350000"
        )
        assert msg.expires_at == 1783350000.0
        assert msg.expired(1783350001) is True
        assert msg.expired(1783349999) is False

    def test_an_unparseable_expiry_means_no_expiry_not_expiry_at_zero(self):
        # "expires=<garbage>" must not read as expired-since-1970, which would
        # silently turn a live claim into a free card.
        msg = parse_message("[GPU CLAIM] node=n1 holder=@a vram=6gb expires=soon")
        assert msg.expires_at is None
        assert msg.expired(2**31) is False

    def test_unknown_keys_are_ignored(self):
        msg = parse_message("[GPU CLAIM] node=n1 holder=@a vram=1gb priority=high")
        assert msg.node == "n1"

    def test_non_protocol_text_is_none(self):
        assert parse_message("just chatting about the gpu") is None
        assert parse_message("") is None
        assert parse_message("[GPU CLAIM] holder=@a vram=1gb") is None  # no node

    def test_bus_message_dict_carries_authenticated_author(self):
        # The bus message `from` is the identity the bus authenticated; the
        # body's holder= is caller-controlled text.
        msg = parse_message(
            {
                "id": 7,
                "ts": 1234.5,
                "from": "agent_canonical_1",
                "body": "[GPU CLAIM] node=n1 holder=@spoofed vram=1gb",
            }
        )
        assert msg.bus_from == "agent_canonical_1"
        assert msg.identity_key == "agent_canonical_1"
        assert msg.display_holder() == "@spoofed"
        assert msg.message_id == 7

    def test_bus_author_falls_back_to_body_holder_when_from_absent(self):
        msg = parse_message({"id": 1, "body": "[GPU CLAIM] node=n1 holder=@a vram=1gb"})
        assert msg.identity_key == "@a"

    def test_newline_cannot_inject_a_second_protocol_line(self):
        # A caller-supplied value with a newline would otherwise post a second
        # line (e.g. a RELEASE closing someone else's claim) in one message.
        line = render_claim("n1", "@a", 1024, reason="x\n[GPU RELEASE] node=n1 holder=@a")
        assert "\n" not in line
        # The injected text stays inside the reason field; the message is still
        # exactly one CLAIM.
        msg = parse_message(line)
        assert msg is not None and msg.kind == CLAIM
        assert "[GPU RELEASE]" in msg.reason

    def test_a_value_cannot_inject_a_field(self):
        # `=` is neutralised in rendered values, so a reason cannot smuggle a
        # `node=` that a reader re-parses as the real node.
        line = render_claim("n1", "@a", 1024, reason="x node=ghost")
        msg = parse_message(line)
        assert msg is not None
        assert msg.node == "n1"
        assert "ghost" in msg.reason

    def test_a_hand_written_duplicate_key_keeps_the_first_value(self):
        # First-wins: a trailing `node=` inside a value cannot override the
        # structural field a renderer emits first.
        msg = parse_message(
            "[GPU CLAIM] node=n1 holder=@a vram=1gb reason=y node=ghost"
        )
        assert msg.node == "n1"


class TestOpenClaims:
    def _bus(self, *bodies, sender="@a"):
        return [
            {"id": i + 1, "ts": float(i), "from": sender, "body": b}
            for i, b in enumerate(bodies)
        ]

    def test_claim_then_release_closes_it(self):
        msgs = self._bus(
            "[GPU CLAIM] node=n1 holder=@a vram=6gb",
            "[GPU RELEASE] node=n1 holder=@a",
        )
        assert open_claims(msgs) == {}

    def test_claim_without_release_stays_open(self):
        msgs = self._bus("[GPU CLAIM] node=n1 holder=@a vram=6gb")
        folded = open_claims(msgs)
        assert list(folded) == ["n1"]
        assert folded["n1"][0].vram_mb == 6144

    def test_a_claim_past_its_published_expiry_is_not_open(self):
        # taOS #893 / CR on #2988: a holder that crashed (no RELEASE, no
        # keep-alive) must not block the card forever.
        msgs = self._bus("[GPU CLAIM] node=n1 holder=@a vram=6gb expires=1000")
        assert open_claims(msgs, now=1001) == {}
        assert list(open_claims(msgs, now=999)) == ["n1"]
        # The boundary counts as expired, matching the cluster lease's TTL.
        assert open_claims(msgs, now=1000) == {}

    def test_a_claim_without_a_published_expiry_never_expires(self):
        # Interim hand-posted lines stay bounded by RELEASE / the fold window.
        msgs = self._bus("[GPU CLAIM] node=n1 holder=@a vram=6gb")
        assert list(open_claims(msgs, now=time.time() + 10**9)) == ["n1"]

    def test_a_refreshed_claim_carries_its_new_expiry(self):
        msgs = self._bus(
            "[GPU CLAIM] node=n1 holder=@a vram=6gb expires=1000",
            "[GPU CLAIM] node=n1 holder=@a vram=6gb expires=2000",
        )
        folded = open_claims(msgs, now=1500)
        assert folded["n1"][0].expires_at == 2000

    def test_one_holders_expiry_does_not_drop_another_holders_claim(self):
        msgs = [
            {
                "id": 1,
                "from": "@a",
                "body": "[GPU CLAIM] node=n1 holder=@a vram=6gb expires=1000",
            },
            {
                "id": 2,
                "from": "@b",
                "body": "[GPU CLAIM] node=n1 holder=@b vram=2gb expires=9999",
            },
        ]
        folded = open_claims(msgs, now=2000)
        assert [c.display_holder() for c in folded["n1"]] == ["@b"]

    def test_a_release_from_another_holder_does_not_close_the_claim(self):
        msgs = [
            {"id": 1, "from": "@a", "body": "[GPU CLAIM] node=n1 holder=@a vram=6gb"},
            {"id": 2, "from": "@b", "body": "[GPU RELEASE] node=n1 holder=@b"},
        ]
        folded = open_claims(msgs)
        assert [c.display_holder() for c in folded["n1"]] == ["@a"]

    def test_reposting_a_claim_replaces_rather_than_double_counts(self):
        msgs = self._bus(
            "[GPU CLAIM] node=n1 holder=@a vram=6gb",
            "[GPU CLAIM] node=n1 holder=@a vram=4gb",
        )
        folded = open_claims(msgs)
        assert len(folded["n1"]) == 1
        assert folded["n1"][0].vram_mb == 4096

    def test_two_holders_on_one_node_both_stay_open(self):
        msgs = [
            {"id": 1, "from": "@a", "body": "[GPU CLAIM] node=n1 holder=@a vram=6gb"},
            {"id": 2, "from": "@b", "body": "[GPU CLAIM] node=n1 holder=@b vram=2gb"},
        ]
        folded = open_claims(msgs)
        assert {c.display_holder() for c in folded["n1"]} == {"@a", "@b"}

    def test_requests_and_chatter_do_not_create_claims(self):
        msgs = self._bus(
            "[GPU REQUEST] node=n1 need=6gb",
            "morning all",
            "[GPU CHECK] node=n1 need=6gb",
        )
        assert open_claims(msgs) == {}

    def test_claims_are_grouped_by_node(self):
        msgs = [
            {"id": 1, "from": "@a", "body": "[GPU CLAIM] node=n1 holder=@a vram=1gb"},
            {"id": 2, "from": "@a", "body": "[GPU CLAIM] node=n2 holder=@a vram=1gb"},
        ]
        folded = open_claims(msgs)
        assert set(folded) == {"n1", "n2"}
        assert claims_for_node(folded, "N2")[0].node == "n2"
        assert claims_for_node(folded, "n3") == []

    def test_node_spelling_is_case_insensitive_in_the_fold(self):
        # Two spellings of one hostname must land in ONE group: otherwise
        # claims_for_node returns only one of them and the other holder's claim
        # is invisible to admission.
        msgs = [
            {"id": 1, "from": "@a", "body": "[GPU CLAIM] node=Linstation holder=@a vram=6gb"},
            {"id": 2, "from": "@b", "body": "[GPU CLAIM] node=linstation holder=@b vram=2gb"},
        ]
        folded = open_claims(msgs)
        assert list(folded) == ["linstation"]
        assert len(folded["linstation"]) == 2
        assert len(claims_for_node(folded, "LINSTATION")) == 2

    def test_release_closes_a_claim_spelled_with_a_different_case(self):
        msgs = [
            {"id": 1, "from": "@a", "body": "[GPU CLAIM] node=Linstation holder=@a vram=6gb"},
            {"id": 2, "from": "@a", "body": "[GPU RELEASE] node=linstation holder=@a"},
        ]
        assert open_claims(msgs) == {}


class TestSameHolder:
    def test_alias_and_canonical_id_match(self):
        assert same_holder("agent_abc", "@agent_abc") is True
        assert same_holder("@TAOSmd", "taosmd") is True

    def test_empty_never_matches(self):
        assert same_holder("", "") is False
        assert same_holder(None, "@a") is False
        assert same_holder("@a", None) is False


class TestEvaluateAdmission:
    def _claim(self, sender, vram_mb, node="n1", holder=None):
        return parse_message(
            {
                "id": 1,
                "from": sender,
                "body": f"[GPU CLAIM] node={node} holder={holder or sender} vram={vram_mb}mb",
            }
        )

    def test_free_node_is_admitted(self):
        d = evaluate_admission(
            node="n1", required_mb=6144, identity="@a", claims=[], free_mb=9000
        )
        assert d.admitted is True
        assert d.verified is True
        assert d.blockers == ()

    def test_another_holders_claim_blocks_even_with_vram_to_spare(self):
        d = evaluate_admission(
            node="n1",
            required_mb=1024,
            identity="@a",
            claims=[self._claim("@taosmd", 6144)],
            free_mb=12288,
        )
        assert d.admitted is False
        assert d.blockers == ("@taosmd",)
        assert "already claimed" in d.reason

    def test_a_spoofed_holder_does_not_make_a_claim_mine(self):
        # `from` is the authenticated author; `holder=` is caller-controlled.
        # Accepting the holder for ownership would let an attacker post
        # from=@attacker holder=@victim and have the victim's own admission
        # treat the attacker's claim as its own (i.e. not a blocker, CWE-290).
        claim = parse_message(
            {
                "id": 1,
                "from": "@attacker",
                "body": "[GPU CLAIM] node=n1 holder=@victim vram=6gb",
            }
        )
        d = evaluate_admission(
            node="n1", required_mb=1024, identity="@victim", claims=[claim],
            free_mb=12288,
        )
        assert d.admitted is False
        assert d.blockers == ("@victim",)  # the readable label is still shown

    def test_interim_claim_without_an_authenticated_author_matches_on_holder(self):
        # Posts that predate bus auth carry no `from`; the holder is all we have.
        claim = parse_message("[GPU CLAIM] node=n1 holder=@a vram=1gb")
        d = evaluate_admission(
            node="n1", required_mb=0, identity="@a", claims=[claim], free_mb=4096
        )
        assert d.admitted is True

    def test_own_claim_does_not_block_and_is_subtracted_from_the_budget(self):
        d = evaluate_admission(
            node="n1",
            required_mb=3072,
            identity="@a",
            claims=[self._claim("@a", 6144)],
            free_mb=8192,
        )
        # 8192 free - 6144 already promised to ourselves = 2048 < 3072.
        assert d.admitted is False
        assert d.claimed_mb == 6144
        assert "insufficient VRAM" in d.reason

    def test_own_claim_matches_through_the_registry_canonical_id(self):
        claim = parse_message(
            {"id": 1, "from": "agent_abc", "body": "[GPU CLAIM] node=n1 holder=@a vram=6144mb"}
        )
        d = evaluate_admission(
            node="n1", required_mb=1024, identity="agent_abc", claims=[claim], free_mb=8192
        )
        # Recognised as our own claim (not a blocker) and netted out of the
        # budget: 8192 - 6144 = 2048 >= 1024.
        assert d.admitted is True
        assert d.claimed_mb == 6144
        assert d.blockers == ()

    def test_a_repeated_claim_replaces_the_own_reservation_rather_than_stacking(
        self,
    ):
        # A loaded model is already reflected in the live free figure, so
        # subtracting the caller's own claim a second time would read a 12-GiB
        # card with 6 GiB free as full and deny the idempotent re-claim
        # (CR on #2988).
        d = evaluate_admission(
            node="n1",
            required_mb=6144,
            identity="@a",
            claims=[self._claim("@a", 6144)],
            free_mb=6144,
            capacity_mb=12288,
            replace_own=True,
        )
        assert d.admitted is True
        assert d.claimed_mb == 6144

    def test_replacing_the_own_reservation_does_not_conjure_vram(self):
        # The caller may use its own reservation plus what is actually free -
        # no more.
        d = evaluate_admission(
            node="n1",
            required_mb=16384,
            identity="@a",
            claims=[self._claim("@a", 6144)],
            free_mb=6144,
            capacity_mb=12288,
            replace_own=True,
        )
        assert d.admitted is False
        assert "insufficient VRAM" in d.reason

    def test_insufficient_vram_is_denied(self):
        d = evaluate_admission(
            node="n1", required_mb=8192, identity="@a", claims=[], free_mb=4096
        )
        assert d.admitted is False
        assert d.free_mb == 4096
        assert "need 8192" in d.reason

    def test_capacity_is_used_when_free_is_unknown(self):
        d = evaluate_admission(
            node="n1", required_mb=8192, identity="@a", claims=[], capacity_mb=12288
        )
        assert d.admitted is True
        d2 = evaluate_admission(
            node="n1", required_mb=16384, identity="@a", claims=[], capacity_mb=12288
        )
        assert d2.admitted is False

    def test_unknown_vram_admits_but_flags_unverified(self):
        d = evaluate_admission(node="n1", required_mb=6144, identity="@a", claims=[])
        assert d.admitted is True
        assert d.verified is False
        assert "no VRAM figure" in d.reason

    def test_a_claim_blocks_even_a_zero_vram_check(self):
        # "Load only if free + unclaimed": unclaimed applies regardless of size.
        d = evaluate_admission(
            node="n1",
            required_mb=0,
            identity="@a",
            claims=[self._claim("@b", 1024)],
            free_mb=12288,
        )
        assert d.admitted is False

    def test_admission_dict_shape(self):
        d = evaluate_admission(node="n1", required_mb=1, identity="@a", free_mb=2)
        assert isinstance(d, Admission)
        body = d.as_dict()
        assert body["admitted"] is True and body["vram_verified"] is True


class TestRender:
    def test_render_claim_includes_reason_and_eta(self):
        line = render_claim("n1", "@a", 6144, "ollama", "~10m")
        assert line == "[GPU CLAIM] node=n1 holder=@a vram=~6gb reason=ollama eta=~10m"
        assert parse_message(line).vram_mb == 6144

    def test_render_claim_omits_empty_optionals(self):
        assert render_claim("n1", "@a", 1024) == "[GPU CLAIM] node=n1 holder=@a vram=~1gb"

    def test_render_claim_publishes_an_integer_expiry(self):
        line = render_claim("n1", "@a", 6144, expires_at=1783350000.9)
        # Rounded UP: a published expiry must never precede the reservation it
        # describes, or a peer could admit itself into the truncation gap.
        assert line == "[GPU CLAIM] node=n1 holder=@a vram=~6gb expires=1783350001"
        assert parse_message(line).expires_at == 1783350001.0
        assert parse_message(line).expires_at >= 1783350000.9

    def test_render_claim_omits_the_expiry_when_it_is_unknown(self):
        assert "expires=" not in render_claim("n1", "@a", 6144)

    def test_render_release_request_check(self):
        assert render_release("n1", "@a") == "[GPU RELEASE] node=n1 holder=@a"
        assert render_request("n1", 6144, "blocked") == (
            "[GPU REQUEST] node=n1 need=~6gb reason=blocked"
        )
        assert render_check("n1", 6144) == "[GPU CHECK] node=n1 need=~6gb"

    def test_render_flattens_whitespace_in_values(self):
        assert render_claim("n 1", "@a", 1024, "x\ty") == (
            "[GPU CLAIM] node=n 1 holder=@a vram=~1gb reason=x y"
        )
