"""Temporary: proves the aggregate gate reports FAILURE when a shard fails.

Deleted immediately after the check. If this file is ever seen on dev, the
verification branch leaked and it should be removed.
"""


def test_deliberately_fails():
    assert False, "deliberate failure to verify the CI aggregate gate goes red"
