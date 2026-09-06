import pytest


@pytest.mark.asyncio
async def test_app_state_has_project_stores(client):
    transport = client._transport
    app = transport.app
    assert app.state.project_store is not None
    assert app.state.project_task_store is not None
    assert app.state.projects_root.exists()
