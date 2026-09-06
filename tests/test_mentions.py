from tinyagentos.chat.mentions import MentionSet, parse_mentions


def test_empty_members_returns_empty_explicit():
    m = parse_mentions("@tom help", [])
    assert m.explicit == ()
    assert m.all is False
    assert m.humans is False


def test_no_mentions_in_text():
    m = parse_mentions("hello world", ["tom", "don"])
    assert m.explicit == ()
    assert m.all is False
    assert m.humans is False


def test_none_text_returns_defaults():
    m = parse_mentions(None, ["tom"])
    assert m.explicit == ()
    assert m.all is False
    assert m.humans is False


def test_members_case_insensitive_lookup():
    m = parse_mentions("@Tom", ["TOM", "Don"])
    assert m.explicit == ("tom",)


def test_mention_with_hyphen_in_name():
    m = parse_mentions("@ai-agent report", ["ai-agent", "tom"])
    assert m.explicit == ("ai-agent",)


def test_mention_with_underscore_in_name():
    m = parse_mentions("@my_agent status", ["my_agent", "tom"])
    assert m.explicit == ("my_agent",)


def test_duplicate_mentions_deduped():
    m = parse_mentions("@tom @tom @tom", ["tom"])
    assert m.explicit == ("tom",)


def test_all_and_humans_together():
    m = parse_mentions("@all @humans listen up", ["tom"])
    assert m.all is True
    assert m.humans is True
    assert m.explicit == ()


def test_explicit_plus_special_mentions():
    m = parse_mentions("@tom @all @don please", ["tom", "don"])
    assert m.explicit == ("don", "tom")
    assert m.all is True
    assert m.humans is False


def test_mention_at_start_of_text():
    m = parse_mentions("@tom hello", ["tom"])
    assert m.explicit == ("tom",)


def test_mention_at_end_of_text():
    m = parse_mentions("hello @tom", ["tom"])
    assert m.explicit == ("tom",)


def test_mention_with_no_space_around():
    m = parse_mentions("hey@tom", ["tom"])
    assert m.explicit == ()


def test_partial_member_name_no_match():
    m = parse_mentions("@to help", ["tom"])
    assert m.explicit == ()


def test_mention_substring_of_member_no_match():
    m = parse_mentions("@tommy help", ["tom"])
    assert m.explicit == ()


def test_email_address_not_mention():
    m = parse_mentions("user@example.com", ["example"])
    assert m.explicit == ()


def test_mention_followed_by_punctuation():
    m = parse_mentions("@tom, are you there?", ["tom"])
    assert m.explicit == ("tom",)


def test_multiple_special_all_deduped():
    m = parse_mentions("@all @all @all", ["tom"])
    assert m.all is True
    assert m.explicit == ()


def test_mention_set_equality():
    a = parse_mentions("@tom", ["tom"])
    b = parse_mentions("@tom", ["tom"])
    assert a == b
    assert a == MentionSet(explicit=("tom",), all=False, humans=False)


def test_mention_set_inequality():
    a = parse_mentions("@tom", ["tom"])
    b = parse_mentions("@don", ["tom", "don"])
    assert a != b


def test_mention_set_with_all_true_inequality():
    a = parse_mentions("@all", ["tom"])
    b = parse_mentions("", ["tom"])
    assert a != b


def test_empty_text_empty_members():
    m = parse_mentions("", [])
    assert m.explicit == ()
    assert m.all is False
    assert m.humans is False


def test_whitespace_only_text():
    m = parse_mentions("   ", ["tom"])
    assert m.explicit == ()
    assert m.all is False


def test_mention_with_trailing_underscore_not_matched():
    m = parse_mentions("@tom_ help", ["tom"])
    assert m.explicit == ()


def test_mention_with_trailing_dash_not_matched():
    m = parse_mentions("@tom- help", ["tom"])
    assert m.explicit == ()
