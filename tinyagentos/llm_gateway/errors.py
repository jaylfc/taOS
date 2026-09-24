"""OpenAI-shaped errors, so a stock OpenAI client surfaces them as it would the real API."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


def openai_error_body(message: str, *, type: str, code: str | None = None) -> dict:
    return {"error": {"message": message, "type": type, "param": None, "code": code}}


class GatewayError(Exception):
    """Raised anywhere under the gateway routes; rendered by ``handle_gateway_error``."""

    def __init__(self, status: int, message: str, *, type: str = "invalid_request_error",
                 code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.type = type
        self.code = code

    def response(self) -> JSONResponse:
        return JSONResponse(
            openai_error_body(self.message, type=self.type, code=self.code),
            status_code=self.status,
        )


async def handle_gateway_error(_request: Request, exc: GatewayError) -> JSONResponse:
    return exc.response()


def unauthorized() -> GatewayError:
    return GatewayError(401, "missing or invalid credentials", code="invalid_api_key")


def bad_request(message: str) -> GatewayError:
    return GatewayError(400, message, code="invalid_request")


def model_not_found(message: str) -> GatewayError:
    return GatewayError(404, message, code="model_not_found")


def model_not_permitted(model: str) -> GatewayError:
    return GatewayError(403, f"model {model!r} is not permitted for this caller",
                        code="model_not_permitted")


def upstream_error(message: str) -> GatewayError:
    return GatewayError(502, message, type="api_error", code="upstream_error")
