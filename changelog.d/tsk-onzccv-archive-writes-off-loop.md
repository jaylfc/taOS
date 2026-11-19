### Changed

- Archive, restore and purge now await the local-token and gateway-key writes (``_revoke_gateway_keys``, ``mint_agent_local_token`` and ``unbind_local_token_agent``) via ``await asyncio.to_thread(...)`` so these blocking FileLock/atomic-write operations no longer stall the event loop; behaviour and error returns are unchanged.
