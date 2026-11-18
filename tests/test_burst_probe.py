import asyncio, pytest
from tests.test_auth_pin import *  # fixtures

@pytest.mark.asyncio
async def test_burst(app, console):
    app.state.auth.setup_user("owner", "Owner", "", "passw0rd")
    app.state.auth.set_pin("owner", "4913")
    rs = await asyncio.gather(*[console.post("/auth/pin-login", json={"pin": f"{i:04d}"}) for i in range(40)])
    from collections import Counter
    print("STATUSES", Counter(r.status_code for r in rs))
