"""OMP adapter — drives OMP (oh-my-pi) via the Agent Client Protocol.

OMP speaks the Agent Client Protocol (ACP): ``omp acp`` is a JSON-RPC 2.0
stdio server. This adapter is a thin wrapper around :class:`ACPAdapter`
pre-configured with the OMP command.

Usage (callers drive the adapter):
    from tinyagentos.adapters.omp_adapter import OMPAdapter, OMPConfig

    cfg = OMPConfig(session_key="agent:main:main")
    adapter = OMPAdapter(cfg, sink=my_sink)
    await adapter.spawn()
    await adapter.initialize()
    sid = await adapter.new_session()
    stop_reason = await adapter.prompt(sid, "hello", trace_id="t1")
    await adapter.close()
"""
from __future__ import annotations

from tinyagentos.adapters.acp_adapter import ACPAdapter, ACPConfig


class OMPAdapter(ACPAdapter):
    """Thin wrapper around ACPAdapter pre-configured for OMP."""

    def __init__(self, config=None, sink=None):
        if config is None:
            config = ACPConfig(command=["omp", "acp"])
        super().__init__(config, sink)
