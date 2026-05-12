# tests/test_errors.py
from fastapi.responses import JSONResponse
from tinyagentos.errors import error_response


def test_error_response_required_fields():
    resp = error_response(
        status_code=404,
        error="agent_not_found",
        detail="No agent named 'foo' exists.",
    )
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 404
    body = resp.body
    import json
    data = json.loads(body)
    assert data == {
        "error": "agent_not_found",
        "detail": "No agent named 'foo' exists.",
        "fix": None,
        "doc_url": None,
    }


def test_error_response_optional_fields():
    resp = error_response(
        status_code=403,
        error="forbidden",
        detail="Token scope 'agents.list' does not cover 'agents.deploy'.",
        fix="Reissue the token with `agents.*` scope, or have the operator widen permissions.",
        doc_url="/docs/agents/concepts/permissions",
    )
    import json
    data = json.loads(resp.body)
    assert data["fix"].startswith("Reissue")
    assert data["doc_url"] == "/docs/agents/concepts/permissions"
