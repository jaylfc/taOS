"""OpenAI-compatible image generation server backed by stable-diffusion.cpp.

Wraps stable-diffusion-cpp-python to serve GGUF Stable Diffusion models
(LCM-Dreamshaper, SD 1.5, SDXL Turbo, Flux, etc.) over a POST /v1/images/generations
endpoint that the TinyAgentOS Images app can call directly.

CPU-only today — works on RK3588, x86 servers, anything with enough RAM.

Environment:
  SDCPP_MODEL_PATH   path to the .gguf model (required)
  SDCPP_HOST         bind host (default 0.0.0.0)
  SDCPP_PORT         bind port (default 7864)
  SDCPP_THREADS      inference threads (default 8)
  SDCPP_MODEL_NAME   model id reported to /v1/models (default dreamshaper-8-lcm)

Run:
  SDCPP_MODEL_PATH=data/models/dreamshaper-8-lcm-iq4_nl.gguf \
  python -m tinyagentos.services.sdcpp_server
"""
from __future__ import annotations

import base64
import io
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("sdcpp_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODEL_PATH = Path(os.environ.get("SDCPP_MODEL_PATH", "data/models/dreamshaper-8-lcm-iq4_nl.gguf"))
HOST = os.environ.get("SDCPP_HOST", "0.0.0.0")
PORT = int(os.environ.get("SDCPP_PORT", "7864"))
THREADS = int(os.environ.get("SDCPP_THREADS", "8"))
MODEL_NAME = os.environ.get("SDCPP_MODEL_NAME", "dreamshaper-8-lcm")


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    model: Optional[str] = None
    size: str = "512x512"
    n: int = 1
    response_format: str = Field("b64_json", pattern="^(b64_json|url)$")
    seed: Optional[int] = None
    steps: int = Field(4, ge=1, le=50)
    guidance_scale: float = Field(1.0, ge=0.0, le=20.0)


app = FastAPI(title="stable-diffusion.cpp server", version="0.1.0")
_sd = None
_load_error: Optional[str] = None


@app.on_event("startup")
async def _startup():
    global _sd, _load_error
    if not MODEL_PATH.exists():
        _load_error = f"Model not found: {MODEL_PATH}"
        logger.error(_load_error)
        return
    try:
        from stable_diffusion_cpp import StableDiffusion

        logger.info(f"Loading {MODEL_PATH} (threads={THREADS})")
        start = time.time()
        _sd = StableDiffusion(
            model_path=str(MODEL_PATH),
            n_threads=THREADS,
            verbose=False,
        )
        logger.info(f"Model loaded in {time.time() - start:.1f}s")
    except Exception as exc:
        _load_error = str(exc)
        logger.exception("Failed to load stable-diffusion.cpp model")


@app.get("/health")
async def health():
    if _sd is None:
        return JSONResponse(
            {"status": "error", "error": _load_error or "model not loaded"},
            status_code=503,
        )
    return {"status": "ok", "model": MODEL_NAME, "backend": "stable-diffusion.cpp"}


@app.get("/v1/models")
async def list_models():
    return {
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "tinyagentos",
            }
        ],
        "object": "list",
    }


@app.post("/v1/images/generations")
async def generate(body: GenerateRequest):
    if _sd is None:
        raise HTTPException(503, _load_error or "model not loaded")
    if body.n != 1:
        raise HTTPException(400, "n > 1 not supported")

    try:
        width_s, height_s = body.size.split("x")
        width, height = int(width_s), int(height_s)
    except ValueError:
        raise HTTPException(400, f"invalid size: {body.size}")

    seed = body.seed if body.seed is not None else random.randint(0, 2**31 - 1)

    logger.info(
        f"generate prompt={body.prompt!r} size={width}x{height} steps={body.steps} seed={seed}"
    )
    start = time.time()
    try:
        images = _sd.txt_to_img(
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            width=width,
            height=height,
            sample_steps=body.steps,
            cfg_scale=body.guidance_scale,
            seed=seed,
            sample_method="euler_a",
        )
    except Exception as exc:
        logger.exception("sd.cpp inference failed")
        raise HTTPException(500, f"inference failed: {exc}")
    elapsed = time.time() - start
    logger.info(f"generation complete in {elapsed:.1f}s")

    if not images:
        raise HTTPException(500, "sd.cpp returned no images")
    image = images[0]
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "created": int(time.time()),
        "data": [{"b64_json": b64, "revised_prompt": body.prompt}],
        "model": MODEL_NAME,
        "usage": {"elapsed_seconds": round(elapsed, 2), "seed": seed},
    }


def main():
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
