import logging

import tinyagentos.routes.cast  # noqa: F401


def test_cast_media_token_redacted_in_uvicorn_access_log():
    logger = logging.getLogger("uvicorn.access")
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=('192.0.2.5:5000', 'GET', '/cast/media/canvas/prj-x/still.png?token=tcast_SECRET', '1.1', 200),
        exc_info=None,
    )
    # Apply all filters
    for f in logger.filters:
        f.filter(record)
    message = record.getMessage()
    assert 'tcast_SECRET' not in message
    assert 'REDACTED' in message


def test_non_cast_path_token_left_unchanged():
    logger = logging.getLogger("uvicorn.access")
    original = '/api/x?token=abc'
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=('192.0.2.5:5000', 'GET', original, '1.1', 200),
        exc_info=None,
    )
    for f in logger.filters:
        f.filter(record)
    message = record.getMessage()
    assert 'token=abc' in message
    assert 'REDACTED' not in message
