from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from typing import Any

NODE_ID_RE = re.compile(r"^[0-9]{1,6}$")


def parse_get_volume(text: str) -> dict[str, Any] | None:
    """Parse output from `wpctl get-volume <id>`.

    Expected format:
      "Volume: 0.62"
      "Volume: 0.62 [MUTED]"
    """
    lines = text.strip().splitlines()
    if not lines:
        return None
    first_line = lines[0]
    parts = first_line.split()
    volume = None
    muted = False
    for index, token in enumerate(parts):
        if token.rstrip(":").lower() == "volume" and index + 1 < len(parts):
            try:
                volume = float(parts[index + 1])
            except ValueError:
                volume = None
            break
    if volume is None:
        return None
    if "[MUTED]" in first_line.upper():
        muted = True
    return {"volume": volume, "muted": muted}


def parse_pw_dump(json_text: str) -> dict[str, Any]:
    """Parse output from `pw-dump --json`.

    Extracts:
      - sinks: List of nodes with media.class Audio/Sink
      - sources: List of nodes with media.class Audio/Source  
      - default_sink: Node name of default audio sink
      - default_source: Node name of default audio source

    Node structure:
      {
        "id": str,
        "name": str,  // from node.name
        "description": str,  // from node.description
      }
    """
    try:
        data = json.loads(json_text)
    except Exception:
        return {"sinks": [], "sources": [], "default_sink": None, "default_source": None}

    sinks = []
    sources = []
    default_sink = None
    default_source = None

    # Find metadata object for defaults
    metadata_obj = None
    for obj in data:
        if obj.get("type") == "PipeWire:Interface:Metadata":
            props = obj.get("props", {})
            if props.get("metadata.name") == "default":
                metadata_obj = obj
                break

    if metadata_obj:
        props = metadata_obj.get("props", {})
        default_sink_key = "default.audio.sink"
        default_source_key = "default.audio.source"
        if default_sink_key in props:
            sink_data = props[default_sink_key]
            if isinstance(sink_data, dict) and "name" in sink_data:
                default_sink = sink_data["name"]
        if default_source_key in props:
            source_data = props[default_source_key]
            if isinstance(source_data, dict) and "name" in source_data:
                default_source = source_data["name"]

    # Extract sinks and sources
    for obj in data:
        obj_type = obj.get("type")
        if obj_type != "PipeWire:Interface:Node":
            continue
        props = obj.get("info", {}).get("props", {})
        media_class = props.get("media.class", "")
        node_name = obj.get("name", "")
        node_description = obj.get("description", "")
        node_id = obj.get("id")

        if media_class == "Audio/Sink":
            sinks.append({"id": node_id, "name": node_name, "description": node_description})
        elif media_class == "Audio/Source":
            sources.append({"id": node_id, "name": node_name, "description": node_description})

    return {"sinks": sinks, "sources": sources, "default_sink": default_sink, "default_source": default_source}


def set_volume_argv(node: str, volume: float) -> list[str]:
    """Build argv for `wpctl set-volume <node> <volume>`.

    Volume is clamped to 0.0..1.5 and formatted as %.2f.
    """
    if not NODE_ID_RE.fullmatch(node) and node != "@DEFAULT_AUDIO_SINK@":
        raise ValueError(f"Invalid node: {node}")
    volume = max(0.0, min(1.5, volume))
    return ["wpctl", "set-volume", node, f"{volume:.2f}"]


def set_mute_argv(node: str, muted: bool) -> list[str]:
    """Build argv for `wpctl set-muted <node> <muted>`.
    """
    if not NODE_ID_RE.fullmatch(node) and node != "@DEFAULT_AUDIO_SINK@":
        raise ValueError(f"Invalid node: {node}")
    return ["wpctl", "set-muted", node, "true" if muted else "false"]


def set_default_argv(node: str) -> list[str]:
    """Build argv for `wpctl set-default <node>`.
    """
    if not NODE_ID_RE.fullmatch(node) and node != "@DEFAULT_AUDIO_SINK@":
        raise ValueError(f"Invalid node: {node}")
    return ["wpctl", "set-default", node]


def run_sync_with_process(argv: list[str], runtime_dir: str) -> tuple[int, str | None]:
    """Execute argv with XDG_RUNTIME_DIR set, 4s timeout (synchronous version using subprocess.run)."""
    env = dict(os.environ, XDG_RUNTIME_DIR=runtime_dir)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=4,
            env=env,
        )
        rc = proc.returncode
        return rc, proc.stdout if proc.stdout else None
    except FileNotFoundError:
        return 127, None
    except subprocess.TimeoutExpired:
        return 124, None
    except Exception:
        return 1, None


async def run(argv: list[str], runtime_dir: str) -> tuple[int, str | None]:
    """Execute argv with XDG_RUNTIME_DIR set, 4s timeout.

    Returns (rc, stdout) where rc is the subprocess return code and stdout is
    the captured output. Never raises, only returns error codes.
    """
    env = dict(os.environ, XDG_RUNTIME_DIR=runtime_dir)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=4)
        rc = proc.returncode if proc.returncode is not None else 1
        return rc, stdout.decode(errors="replace") if stdout else None
    except FileNotFoundError:
        return 127, None
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.communicate(), timeout=1)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
        return 124, None
    except asyncio.CancelledError:
        if "proc" in locals():
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=1)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
        return 130, None  # Process was cancelled
    except Exception:
        return 1, None
