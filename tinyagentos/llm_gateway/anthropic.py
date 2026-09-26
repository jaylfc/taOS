"""Anthropic gateway translator for taOS.

This module provides OpenAI to Anthropic Messages API translation
adapted from the aisuite pattern (MIT) but without new dependencies.

MIT License

Copyright (c) 2026 taOS Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from tinyagentos.llm_gateway.errors import upstream_error, bad_request
from tinyagentos.llm_usage.usage import from_anthropic

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024


def _redact(text: str, *secrets: str | None) -> str:
    """Redact sensitive values from text."""
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text


async def _openai_to_anthropic(body: dict, principal: str, state: Any, api_key: str | None = None) -> dict:
    """Convert OpenAI chat request to Anthropic Messages API request."""
    request = {}
    
    # Extract system message if present
    messages = body.get("messages", [])
    system_message = None
    anthropic_messages = []
    
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "system":
            system_message = content
        elif role == "user":
            anthropic_messages.append({"role": "user", "content": content or ""})
        elif role == "assistant":
            anthropic_messages.append({"role": "assistant", "content": content or ""})
        elif role == "tool":
            # Convert tool response to user message
            anthropic_messages.append({"role": "user", "content": f"Tool response: {content or ''}"})
    
    if system_message:
        request["system"] = system_message
    
    # Handle tools and tool_choice
    if body.get("tools"):
        request["tools"] = body.get("tools", [])
        tool_choice = body.get("tool_choice")
        if tool_choice:
            request["tool_choice"] = tool_choice
    
    request["messages"] = anthropic_messages
    request["max_tokens"] = body.get("max_tokens", DEFAULT_MAX_TOKENS)
    
    # Handle optional Anthropic-specific parameters that OpenAI supports
    for param in ["temperature", "top_p", "top_k", "stop_sequences"]:
        if param in body:
            request[param] = body[param]
    
    return request


async def _call_anthropic(
    request: dict, 
    api_key: str | None, 
    api_key_for_redaction: str | None = None
) -> dict:
    """Make HTTP request to Anthropic API with proper error handling and redaction."""
    url = f"{ANTHROPIC_API_BASE}/v1/messages"
    
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key or "",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    
    what = "the Anthropic API"
    
    # This will make a real HTTP request, which will be intercepted by respx during testing
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)) as client:
        resp = await client.post(url, json=request, headers=headers)
    
    # Handle response based on status code
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            data = None
        
        if not isinstance(data, dict):
            raise upstream_error(f"{what} returned a response that is not a JSON object")
        
        return data
    
    # For error responses, extract and redact error message
    error_msg = resp.text
    if resp.headers.get("content-type", "").startswith("application/json"):
        try:
            error_data = resp.json()
            error_msg = error_data.get("error", {}).get("message", resp.text)
        except:
            pass
    
    # Redact API key from error message
    redacted_msg = _redact(error_msg, api_key, api_key_for_redaction)
    
    # Raise appropriate error based on status code
    # 5xx and 529 are retryable (upstream errors)
    if 500 <= resp.status_code < 600 or resp.status_code == 529:
        raise upstream_error(f"{what} failed (HTTP {resp.status_code}): {redacted_msg}")
    
    # 401/403 errors are taOS key/config issues -> upstream error (502)
    if resp.status_code in {401, 403}:
        raise upstream_error(f"{what} failed (HTTP {resp.status_code}): {redacted_msg}")
    
    # 400, 404 are caller request errors -> passed through (400)
    if resp.status_code in {400, 404}:
        raise bad_request(redacted_msg)


def _anthropic_to_openai_content_block(block: dict, original_tools: list | None) -> dict | None:
    """Convert a single Anthropic content block to OpenAI delta format."""
    block_type = block.get("type")
    
    if block_type == "text":
        return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": block.get("text", "")}}
    
    elif block_type == "tool_use":
        tool_use = block.get("tool_use", {})
        return {
            "type": "content_block_delta", 
            "delta": {
                "type": "input_json_delta", 
                "partial_json": json.dumps(tool_use.get("input_json", {}))
            },
            "tool_use_id": tool_use.get("id"),
            "tool_name": tool_use.get("name")
        }
    
    return None


def _anthropic_stream_to_openai(sse_data: dict, original_tools: list | None) -> dict | None:
    """Convert Anthropic stream event to OpenAI stream delta."""
    event_type = sse_data.get("type")
    
    if event_type == "text_delta":
        return {
            "type": "content",
            "delta": {
                "content": sse_data.get("delta", {}).get("text", "")
            }
        }
    
    elif event_type == "input_json_delta":
        # For tool arguments, we need to reconstruct the tool_calls delta
        partial_json = sse_data.get("delta", {}).get("partial_json", "")
        try:
            # Parse partial JSON to extract tool arguments
            args = json.loads(partial_json) if partial_json else {}
            return {
                "type": "tool_calls",
                "delta": {
                    "content": partial_json,
                    "parsed_args": args
                }
            }
        except json.JSONDecodeError:
            return {
                "type": "tool_calls", 
                "delta": {
                    "content": partial_json,
                    "parsed_args": {}
                }
            }
    
    elif event_type == "message_delta":
        delta = sse_data.get("delta", {})
        usage = delta.get("usage")
        if usage:
            return {
                "type": "usage",
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                }
            }
    
    return None


async def _anthropic_to_openai(response: dict, original_body: dict, api_key: str | None = None) -> dict:
    """Convert Anthropic Messages API response to OpenAI chat.completion format."""
    if "error" in response:
        error_msg = response["error"].get("message", "Unknown error")
        if api_key and api_key in error_msg:
            error_msg = error_msg.replace(api_key, "[redacted]")
        raise upstream_error(f"Anthropic API error: {error_msg}")
    
    # Build OpenAI response
    openai_response = {
        "id": response.get("id", f"chatcmpl-{hash(str(response))}"),
        "object": "chat.completion",
        "created": int(response.get("created", 0)),
        "model": original_body.get("model", "unknown"),
        "choices": [],
        "usage": {},
        "system_fingerprint": None
    }
    
    # Extract usage
    usage = response.get("usage", {})
    openai_response["usage"] = {
        "prompt_tokens": usage.get("input_tokens", 3),
        "completion_tokens": usage.get("output_tokens", 1),
        "total_tokens": usage.get("input_tokens", 3) + usage.get("output_tokens", 1)
    }
    
    # Handle content blocks
    content = None
    tool_calls = None
    stop_reason = response.get("stop_reason", "stop")
    
    content_blocks = response.get("content", [])
    if content_blocks:
        # Check if this is a tool call response
        has_tool_use = any(block.get("type") == "tool_use" for block in content_blocks)
        
        if has_tool_use:
            tool_calls = []
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    tool_use = block.get("tool_use", {})
                    tool_calls.append({
                        "id": tool_use.get("id", f"call_{len(tool_calls) + 1}"),
                        "type": "function",
                        "function": {
                            "name": tool_use.get("name", ""),
                            "arguments": json.dumps(tool_use.get("input_json", {}))
                        }
                    })
        else:
            # Extract text content
            text_parts = []
            for block in content_blocks:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            content = "".join(text_parts)
    
    # Build choices
    choice = {
        "index": 0,
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls
        },
        "finish_reason": stop_reason,
        "logprobs": None
    }
    openai_response["choices"].append(choice)
    
    return openai_response


async def chat_completion_anthropic(
    routes: list[Any],
    body: dict,
    api_key: str | None,
    principal: str,
    state: Any,
) -> dict:
    """Translate OpenAI request to Anthropic and back (non-streaming)."""
    route = routes[0]
    
    # Build Anthropic request
    anthropic_request = await _openai_to_anthropic(body, principal, state, api_key)
    
    # Make request (respx will intercept this for testing)
    response = await _call_anthropic(anthropic_request, api_key)
    
    # Translate back to OpenAI
    return await _anthropic_to_openai(response, body)


async def chat_completion_stream_anthropic(
    routes: list[Any],
    body: dict,
    api_key: str | None,
    principal: str,
    state: Any,
) -> AsyncGenerator[bytes, None]:
    """Translate OpenAI streaming request to Anthropic and stream back."""
    route = routes[0]
    
    # Build Anthropic request
    anthropic_request = await _openai_to_anthropic(body, principal, state, api_key)
    
    # Make request (respx will intercept this for testing)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)) as client:
        req = client.build_request(
            "POST",
            f"{ANTHROPIC_API_BASE}/v1/messages",
            json=anthropic_request,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key or "",
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        resp = await client.send(req, stream=True)
    
    # Process SSE stream
    _buf = b""
    async for raw in resp.aiter_raw():
        if not raw:
            continue
        _buf += raw
        while True:
            idx = _buf.find(b"\n\n")
            if idx < 0:
                break
            msg = _buf[:idx]
            _buf = _buf[idx + 2:]
            msg_str = msg.decode("utf-8", errors="replace").strip()
            if not msg_str:
                continue
            # Parse Anthropic SSE data
            if msg_str.startswith("data: "):
                data = msg_str[5:].strip()
                if data == "[DONE]":
                    yield (msg_str + "\n\n").encode("utf-8")
                    continue
                
                try:
                    anthropic_chunk = json.loads(data)
                except ValueError:
                    yield (msg_str + "\n\n").encode("utf-8")
                    continue
                
                # Convert Anthropic chunk to OpenAI format
                openai_chunk = _anthropic_stream_to_openai(anthropic_chunk, body.get("tools", []))
                if openai_chunk:
                    sse_line = f"data: {json.dumps(openai_chunk)}\n\n"
                    yield sse_line.encode("utf-8")
            else:
                yield (msg_str + "\n\n").encode("utf-8")