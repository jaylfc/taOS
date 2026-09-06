import pytest
import yaml
from httpx import AsyncClient, ASGITransport


def _make_app(tmp_path):
    cfg = {"server": {"host": "0.0.0.0", "port": 6969}, "backends": [],
           "qmd": {"url": "http://localhost:7832"}, "agents": [],
           "metrics": {"poll_interval": 30, "retention_days": 30}}
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_chat_guide_returns_markdown(tmp_path):
    app = _make_app(tmp_path)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    ) as client:
        r = await client.get("/api/docs/chat-guide")
        assert r.status_code == 200
        data = r.json()
        assert "markdown" in data
        assert "taOS Chat" in data["markdown"] or "chat-guide" in data["markdown"].lower()
