"""One licence gate for every install path.

Every route or background job that installs a catalog manifest calls
``enforce`` before it installs anything: the store routes (v1, used by
``taosctl store install``, and v2), the model download route, taOSmd setup, the
shared legacy installer (setup wizard backends and v2 services) and the
dependency backend a v2 chain pulls in. The rules live in
``tinyagentos/license_policy.py``; this module applies them to a request.

A caller with an agent, CLI or service token gets the 412 with the text the
person must accept, so it can ask them. Only a person's own session can send
``accepted: true``; nothing else can accept on their behalf.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from tinyagentos import license_policy


def request_via(request: Request) -> str | None:
    """Which credential authenticated this request (set by the auth middleware)."""
    via = getattr(request.state, "via", None)
    return via if isinstance(via, str) else None


def _session_user(request: Request) -> dict | None:
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        return None
    token = request.cookies.get("taos_session", "")
    if not token:
        return None
    return auth.session_user(token, user_agent=request.headers.get("user-agent", ""))


def _finish(progress, install_id, error: str) -> None:
    if progress is not None and install_id:
        progress.finish(install_id, success=False, error=error)


async def enforce(request: Request, manifest, body: dict | None, progress=None, install_id: str = "") -> JSONResponse | None:
    """Return a refusal response when ``manifest`` may not install yet, else None.

    ``body`` carries the person's ``accepted`` and ``country`` answers. A
    successful acceptance is recorded here before None is returned.
    """
    body = body if isinstance(body, dict) else {}
    restriction = license_policy.evaluate(manifest, manifest.id)
    if not restriction.restrictive:
        return None
    license_store = getattr(request.app.state, "license_acceptances", None)
    via = request_via(request)
    person_session = license_policy.via_may_accept(via)
    session_user = _session_user(request) if person_session else None
    acting = session_user
    if acting is None:
        uid = getattr(request.state, "user_id", None)
        auth_mgr = getattr(request.app.state, "auth", None)
        if isinstance(uid, str) and uid and auth_mgr is not None:
            acting = auth_mgr.get_user_by_id(uid)
    user_id = (acting or {}).get("id") or "anonymous"
    username = (acting or {}).get("username") or ""
    accepted_now = bool(body.get("accepted", False))
    country_in = body.get("country")

    def _refuse(error: str, detail: str, status: int, **extra):
        _finish(progress, install_id, error)
        return JSONResponse(
            {"error": error, "detail": detail, "license_class": restriction.cls,
             "license_id": restriction.acceptance_key, "install_id": install_id, **extra},
            status_code=status,
        )

    if accepted_now and (not person_session or session_user is None):
        return _refuse(
            "agent_cannot_accept_license",
            "Only a person signed in to their own session can accept a licence. "
            "Ask the person to accept it in the Store app.",
            403,
        )
    if country_in is not None:
        if not person_session or session_user is None:
            return _refuse(
                "agent_cannot_set_country",
                "Only a person signed in to their own session can set their country.",
                403,
            )
        code = license_policy.valid_country(country_in)
        if code is None:
            return _refuse(
                "invalid_country",
                "Country must be a two-letter ISO 3166-1 code such as US or GB.",
                400,
            )
        request.app.state.auth.set_country(session_user["username"], code)
        acting = {**(acting or {}), "country": code}

    # Territory-limited licences: the person's own country decides. Unset
    # fails closed here, and only here; acceptance never overrides an
    # excluded country.
    country = (acting or {}).get("country") or ""
    territory = license_policy.territory_check(restriction, country)
    if territory == "excluded":
        return _refuse(
            "territory_excluded",
            f"{manifest.name} is under {restriction.name}, which does not apply in your "
            f"country ({country.upper()}). Accepting the licence does not change that.",
            403, user_country=country.upper(),
        )
    if territory == "needs_country":
        _finish(progress, install_id, "needs_country")
        return JSONResponse(
            {
                "needs_country": True,
                "detail": (
                    f"{manifest.name} is under {restriction.name}, which is limited by "
                    "country, and your country is not set. Set it in Settings, Account, "
                    "Country (on desktop or phone), or enter it here when asked, then "
                    "install again."
                ),
                "license_class": restriction.cls,
                "license_id": restriction.acceptance_key,
                "name": manifest.name,
                "install_id": install_id,
            },
            status_code=412,
        )

    license_key = restriction.acceptance_key
    already_accepted = (
        await license_store.has_accepted(user_id, manifest.id, license_key)
        if license_store is not None else False
    )
    if not already_accepted and not accepted_now:
        _finish(progress, install_id, "needs_license_acceptance")
        return JSONResponse(
            {
                "needs_license_acceptance": True,
                "license_id": license_key,
                "license_name": restriction.name,
                "license_version": restriction.version,
                "license_url": restriction.url,
                "license_class": restriction.cls,
                "weights_license": restriction.name,
                "name": manifest.name,
                "text": license_policy.acceptance_text(manifest.name, restriction),
            },
            status_code=412,
        )
    if accepted_now and not already_accepted:
        if license_store is None:
            return _refuse(
                "license_store_unavailable",
                "The licence record could not be saved, so the install was not started.",
                503,
            )
        await license_store.record_acceptance(
            user_id, manifest.id, license_key,
            license_name=restriction.name, license_version=restriction.version,
            license_url=restriction.url, username=username,
        )
    return None
