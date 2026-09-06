from tinyagentos.chat.mentions import parse_mentions, MentionSet


def test_single_slug():
    assert parse_mentions("hey @tom what's up", ["tom", "don"]) == MentionSet(
        explicit=("tom",), all=False, humans=False
    )


def test_multiple_slugs_sorted_and_deduped():
    m = parse_mentions("@tom @don @tom please", ["tom", "don"])
    assert m.explicit == ("don", "tom")


def test_at_all():
    m = parse_mentions("@all please respond", ["tom"])
    assert m.all is True
    assert m.explicit == ()


def test_at_humans():
    m = parse_mentions("@humans heads up", ["tom"])
    assert m.humans is True


def test_non_member_slug_ignored():
    m = parse_mentions("@unknown help", ["tom"])
    assert m.explicit == ()


def test_word_boundary_email_not_mention():
    m = parse_mentions("email@tom.com send", ["tom"])
    assert m.explicit == ()


def test_case_insensitive():
    m = parse_mentions("@TOM stand up", ["tom"])
    assert m.explicit == ("tom",)


def test_empty_text():
    m = parse_mentions("", ["tom"])
    assert m.explicit == ()
    assert m.all is False
    assert m.humans is False
