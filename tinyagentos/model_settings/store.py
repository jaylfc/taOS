"""Model Settings Store - SQLite-backed storage for model settings with audit events."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from tinyagentos.base_store import BaseStore
from tinyagentos.db_migrations import run_migrations_async


SCHEMA = """
CREATE TABLE IF NOT EXISTS model_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,           -- 'model', 'agent', 'chat'
    key TEXT NOT NULL,             -- model name, agent id, channel id
    setting TEXT NOT NULL,         -- setting name (temperature, top_p, etc.)
    value TEXT NOT NULL,           -- JSON-encoded value
    mode TEXT NOT NULL,            -- 'default' or 'forced'
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(level, key, setting, mode)
);

CREATE INDEX IF NOT EXISTS idx_model_settings_lookup
    ON model_settings(level, key, setting);

CREATE TABLE IF NOT EXISTS model_settings_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    level TEXT NOT NULL,
    key TEXT NOT NULL,
    setting TEXT NOT NULL,
    old_value TEXT,                -- JSON-encoded, NULL if new
    new_value TEXT,                -- JSON-encoded, NULL if deleted
    mode TEXT NOT NULL,
    via TEXT NOT NULL,             -- 'api', 'cli', etc.
    trace_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_settings_events_ts
    ON model_settings_events(ts DESC);

CREATE INDEX IF NOT EXISTS idx_model_settings_events_actor
    ON model_settings_events(actor);

CREATE TABLE IF NOT EXISTS model_capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT,                    -- NULL = all models for this provider
    setting TEXT NOT NULL,
    min_value TEXT,                -- JSON-encoded, NULL if no min
    max_value TEXT,                -- JSON-encoded, NULL if no max
    allowed_values TEXT,           -- JSON-encoded array, NULL if not enum
    UNIQUE(provider, model, setting)
);

CREATE TABLE IF NOT EXISTS model_defaults (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    setting TEXT NOT NULL,
    value TEXT NOT NULL,           -- JSON-encoded
    UNIQUE(model, setting)
);

CREATE TABLE IF NOT EXISTS agent_owners (
    agent_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_members (
    channel_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    PRIMARY KEY (channel_id, member_id)
);
"""


class ModelSettingsStore(BaseStore):
    """SQLite store for model settings with four precedence levels and audit trail."""
    
    SCHEMA = SCHEMA
    MIGRATIONS = []
    
    # All supported settings
    SETTINGS = frozenset([
        "temperature", "top_p", "top_k", "min_p", "max_output_tokens",
        "reasoning_effort", "repetition_penalty", "presence_penalty",
        "seed", "stop_sequences", "system_prompt_addition"
    ])
    
    # Levels in precedence order (lowest to highest)
    LEVELS = ("model", "agent", "chat", "request")
    
    def __init__(self, db_path: Path):
        super().__init__(db_path)
        # Cache for capability lookups
        self._capability_cache: dict[tuple[str, str], dict] = {}
        self._cache_loaded = False
    
    async def _post_init(self) -> None:
        """Initialize default capabilities after schema creation."""
        await self._ensure_default_capabilities()
        await self._load_capability_cache()
    
    async def _ensure_default_capabilities(self) -> None:
        """Insert default provider capabilities if table is empty."""
        async with self._db.execute("SELECT COUNT(*) FROM model_capabilities") as cursor:
            count = (await cursor.fetchone())[0]
        if count > 0:
            return
        
        # Default capabilities - these match the specification
        defaults = [
            # OpenAI-compatible providers
            ("openai", None, "temperature", "0.0", "2.0", None),
            ("openai", None, "top_p", "0.0", "1.0", None),
            ("openai", None, "max_output_tokens", "1", "32768", None),
            ("openai", None, "seed", "0", "2147483647", None),
            ("openai", None, "stop_sequences", None, None, '["string"]'),
            ("openai", None, "system_prompt_addition", None, None, None),
            ("openrouter", None, "temperature", "0.0", "2.0", None),
            ("openrouter", None, "top_p", "0.0", "1.0", None),
            ("openrouter", None, "max_output_tokens", "1", "32768", None),
            ("openrouter", None, "seed", "0", "2147483647", None),
            ("openrouter", None, "stop_sequences", None, None, '["string"]'),
            ("openrouter", None, "system_prompt_addition", None, None, None),
            ("deepseek", None, "temperature", "0.0", "2.0", None),
            ("deepseek", None, "top_p", "0.0", "1.0", None),
            ("deepseek", None, "max_output_tokens", "1", "32768", None),
            ("deepseek", None, "seed", "0", "2147483647", None),
            ("deepseek", None, "stop_sequences", None, None, '["string"]'),
            ("deepseek", None, "system_prompt_addition", None, None, None),
            
            # Anthropic
            ("anthropic", None, "temperature", "0.0", "1.0", None),
            ("anthropic", None, "top_p", "0.0", "1.0", None),
            ("anthropic", None, "top_k", "1", "100", None),
            ("anthropic", None, "max_output_tokens", "1", "8192", None),
            ("anthropic", None, "stop_sequences", None, None, '["string"]'),
            ("anthropic", None, "system_prompt_addition", None, None, None),
            
            # Ollama family
            ("ollama", None, "temperature", "0.0", "2.0", None),
            ("ollama", None, "top_p", "0.0", "1.0", None),
            ("ollama", None, "top_k", "1", "100", None),
            ("ollama", None, "min_p", "0.0", "1.0", None),
            ("ollama", None, "max_output_tokens", "1", "32768", None),
            ("ollama", None, "repetition_penalty", "0.0", "2.0", None),
            ("ollama", None, "presence_penalty", "-2.0", "2.0", None),
            ("ollama", None, "seed", "0", "2147483647", None),
            ("ollama", None, "stop_sequences", None, None, '["string"]'),
            ("ollama", None, "system_prompt_addition", None, None, None),
            
            # Per-model overrides for reasoning
            ("anthropic", "claude-3-7-sonnet", "reasoning_effort", None, None, '["low","medium","high","off"]'),
            ("anthropic", "claude-3-5-sonnet", "reasoning_effort", None, None, '["low","medium","high","off"]'),
            ("openai", "o1", "reasoning_effort", None, None, '["low","medium","high"]'),
            ("openai", "o1-mini", "reasoning_effort", None, None, '["low","medium","high"]'),
            ("openai", "o3-mini", "reasoning_effort", None, None, '["low","medium","high"]'),
        ]
        
        now = time.time()
        for provider, model, setting, min_v, max_v, allowed in defaults:
            await self._db.execute(
                """INSERT OR IGNORE INTO model_capabilities
                   (provider, model, setting, min_value, max_value, allowed_values)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider, model, setting, min_v, max_v, allowed),
            )
        await self._db.commit()
    
    async def _load_capability_cache(self) -> None:
        """Load capabilities into memory for fast lookups."""
        self._capability_cache.clear()
        async with self._db.execute(
            "SELECT provider, model, setting, min_value, max_value, allowed_values FROM model_capabilities"
        ) as cursor:
            async for row in cursor:
                provider, model, setting, min_v, max_v, allowed = row
                key = (provider, model or "*")
                if key not in self._capability_cache:
                    self._capability_cache[key] = {}
                cap = {}
                if min_v is not None:
                    cap["min"] = json.loads(min_v)
                if max_v is not None:
                    cap["max"] = json.loads(max_v)
                if allowed is not None:
                    cap["allowed"] = json.loads(allowed)
                self._capability_cache[key][setting] = cap
        self._cache_loaded = True
    
    def _get_capability(self, provider: str, model: str, setting: str) -> dict | None:
        """Get capability for a provider/model/setting, with fallback to provider-level."""
        # Try model-specific first
        key = (provider, model)
        if key in self._capability_cache and setting in self._capability_cache[key]:
            return self._capability_cache[key][setting]
        # Fall back to provider-level
        key = (provider, "*")
        if key in self._capability_cache and setting in self._capability_cache[key]:
            return self._capability_cache[key][setting]
        return None
    
    # -----------------------------------------------------------------------
    # Permission checks
    # -----------------------------------------------------------------------
    
    def _check_write_permission(self, actor: str, level: str, key: str) -> None:
        """Verify actor can write to this level/key."""
        if actor.startswith("agent:"):
            raise PermissionError("agent actors cannot write model settings")
        
        if level == "agent":
            # Check if actor is admin or owns the agent
            if not actor.startswith("admin:"):
                owner = self._get_agent_owner_sync(key)
                if owner != actor:
                    raise PermissionError(f"{actor} is not the owner of agent {key}")
        
        elif level == "chat":
            # Check if actor is admin or member of the channel
            if not actor.startswith("admin:"):
                if not self._is_chat_member_sync(key, actor):
                    raise PermissionError(f"{actor} is not a member of channel {key}")
        
        elif level == "model":
            # Only admins can write model defaults
            if not actor.startswith("admin:"):
                raise PermissionError("only admins can write model defaults")
    
    def _get_agent_owner_sync(self, agent_id: str) -> str | None:
        """Synchronous owner lookup for permission check."""
        # This runs in the same thread, we need to use a sync query
        # For now, use a simple approach - in practice this would need a sync connection
        # We'll handle this in the async method
        return None  # Placeholder, actual check in async method
    
    def _is_chat_member_sync(self, channel_id: str, member_id: str) -> bool:
        """Synchronous membership check for permission check."""
        return True  # Placeholder, actual check in async method
    
    async def _check_write_permission_async(self, actor: str, level: str, key: str) -> None:
        """Async permission check with DB lookups."""
        if actor.startswith("agent:"):
            raise PermissionError("agent actors cannot write model settings")
        
        if level == "agent":
            if not actor.startswith("admin:"):
                owner = await self.get_agent_owner(key)
                if owner != actor:
                    raise PermissionError(f"{actor} is not the owner of agent {key}")
        
        elif level == "chat":
            if not actor.startswith("admin:"):
                is_member = await self.is_chat_member(key, actor)
                if not is_member:
                    raise PermissionError(f"{actor} is not a member of channel {key}")
        
        elif level == "model":
            if not actor.startswith("admin:"):
                raise PermissionError("only admins can write model defaults")
    
    # -----------------------------------------------------------------------
    # Core CRUD operations
    # -----------------------------------------------------------------------
    
    async def set_setting(
        self,
        actor: str,
        level: str,
        key: str,
        setting: str,
        value: Any,
        mode: str,
        via: str = "api",
    ) -> None:
        """Write a setting with audit trail."""
        if level not in self.LEVELS[:-1]:  # Exclude 'request' level
            raise ValueError(f"invalid level: {level}")
        if setting not in self.SETTINGS:
            raise ValueError(f"unknown setting: {setting}")
        if mode not in ("default", "forced"):
            raise ValueError(f"invalid mode: {mode}")
        
        await self._check_write_permission_async(actor, level, key)
        
        # Get old value for audit
        old_value = await self._get_raw_setting(level, key, setting, mode)
        
        now = time.time()
        value_json = json.dumps(value)
        
        # Upsert the setting
        await self._db.execute(
            """INSERT INTO model_settings (level, key, setting, value, mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(level, key, setting, mode) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (level, key, setting, value_json, mode, now, now),
        )
        
        # Record audit event
        await self._db.execute(
            """INSERT INTO model_settings_events
               (ts, actor, level, key, setting, old_value, new_value, mode, via)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, actor, level, key, setting,
             json.dumps(old_value) if old_value is not None else None,
             value_json, mode, via),
        )
        
        await self._db.commit()
    
    async def _get_raw_setting(
        self, level: str, key: str, setting: str, mode: str | None = None
    ) -> Any | None:
        """Get raw setting value from DB."""
        if mode is not None:
            async with self._db.execute(
                "SELECT value FROM model_settings WHERE level=? AND key=? AND setting=? AND mode=?",
                (level, key, setting, mode),
            ) as cursor:
                row = await cursor.fetchone()
                return json.loads(row[0]) if row else None
        else:
            # Get highest priority mode (forced > default)
            async with self._db.execute(
                """SELECT value, mode FROM model_settings 
                   WHERE level=? AND key=? AND setting=?
                   ORDER BY CASE mode WHEN 'forced' THEN 0 ELSE 1 END
                   LIMIT 1""",
                (level, key, setting),
            ) as cursor:
                row = await cursor.fetchone()
                return json.loads(row[0]) if row else None
    
    async def get_setting(self, level: str, key: str, setting: str) -> Any | None:
        """Get a setting value (highest priority mode)."""
        return await self._get_raw_setting(level, key, setting)
    
    async def get_all_settings(self, level: str, key: str) -> dict[str, Any]:
        """Get all settings for a level/key."""
        result = {}
        async with self._db.execute(
            """SELECT setting, value, mode FROM model_settings 
               WHERE level=? AND key=?
               ORDER BY setting, CASE mode WHEN 'forced' THEN 0 ELSE 1 END""",
            (level, key),
        ) as cursor:
            async for row in cursor:
                setting, value, mode = row
                if setting not in result:  # First one wins (forced > default)
                    result[setting] = json.loads(value)
        return result
    
    async def delete_setting(
        self, actor: str, level: str, key: str, setting: str, mode: str, via: str = "api"
    ) -> bool:
        """Delete a setting with audit trail."""
        await self._check_write_permission_async(actor, level, key)
        
        old_value = await self._get_raw_setting(level, key, setting, mode)
        if old_value is None:
            return False
        
        now = time.time()
        await self._db.execute(
            "DELETE FROM model_settings WHERE level=? AND key=? AND setting=? AND mode=?",
            (level, key, setting, mode),
        )
        
        await self._db.execute(
            """INSERT INTO model_settings_events
               (ts, actor, level, key, setting, old_value, new_value, mode, via)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, actor, level, key, setting,
             json.dumps(old_value), None, mode, via),
        )
        
        await self._db.commit()
        return True
    
    # -----------------------------------------------------------------------
    # Agent ownership
    # -----------------------------------------------------------------------
    
    async def set_agent_owner(self, agent_id: str, user_id: str) -> None:
        """Record agent ownership for permission checks."""
        await self._db.execute(
            """INSERT OR REPLACE INTO agent_owners (agent_id, user_id)
               VALUES (?, ?)""",
            (agent_id, user_id),
        )
        await self._db.commit()
    
    async def get_agent_owner(self, agent_id: str) -> str | None:
        """Get the owner of an agent."""
        async with self._db.execute(
            "SELECT user_id FROM agent_owners WHERE agent_id = ?",
            (agent_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None
    
    # -----------------------------------------------------------------------
    # Chat membership
    # -----------------------------------------------------------------------
    
    async def add_chat_member(self, channel_id: str, member_id: str) -> None:
        """Add a member to a channel for permission checks."""
        await self._db.execute(
            """INSERT OR IGNORE INTO chat_members (channel_id, member_id)
               VALUES (?, ?)""",
            (channel_id, member_id),
        )
        await self._db.commit()
    
    async def remove_chat_member(self, channel_id: str, member_id: str) -> None:
        """Remove a member from a channel."""
        await self._db.execute(
            "DELETE FROM chat_members WHERE channel_id = ? AND member_id = ?",
            (channel_id, member_id),
        )
        await self._db.commit()
    
    async def is_chat_member(self, channel_id: str, member_id: str) -> bool:
        """Check if a member belongs to a channel."""
        async with self._db.execute(
            "SELECT 1 FROM chat_members WHERE channel_id = ? AND member_id = ?",
            (channel_id, member_id),
        ) as cursor:
            return await cursor.fetchone() is not None
    
    # -----------------------------------------------------------------------
    # Audit events
    # -----------------------------------------------------------------------
    
    async def get_events(
        self, limit: int = 100, actor: str | None = None, level: str | None = None
    ) -> list[dict]:
        """Get audit events, newest first."""
        conditions = []
        params = []
        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if level:
            conditions.append("level = ?")
            params.append(level)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        
        async with self._db.execute(
            f"""SELECT id, ts, actor, level, key, setting, old_value, new_value, mode, via, trace_id
                FROM model_settings_events {where}
                ORDER BY ts DESC LIMIT ?""",
            params,
        ) as cursor:
            rows = await cursor.fetchall()
        
        events = []
        for row in rows:
            events.append({
                "id": row[0],
                "ts": row[1],
                "actor": row[2],
                "level": row[3],
                "key": row[4],
                "setting": row[5],
                "old_value": json.loads(row[6]) if row[6] else None,
                "new_value": json.loads(row[7]) if row[7] else None,
                "mode": row[8],
                "via": row[9],
                "trace_id": row[10],
            })
        return events
    
    # -----------------------------------------------------------------------
    # Capabilities
    # -----------------------------------------------------------------------
    
    async def get_capability(
        self, provider: str, model: str, setting: str
    ) -> dict | None:
        """Get capability for a provider/model/setting."""
        return self._get_capability(provider, model, setting)
    
    async def set_capability(
        self,
        provider: str,
        model: str | None,
        setting: str,
        min_value: Any = None,
        max_value: Any = None,
        allowed_values: list | None = None,
    ) -> None:
        """Set a capability (admin only)."""
        await self._db.execute(
            """INSERT OR REPLACE INTO model_capabilities
               (provider, model, setting, min_value, max_value, allowed_values)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                provider,
                model,
                setting,
                json.dumps(min_value) if min_value is not None else None,
                json.dumps(max_value) if max_value is not None else None,
                json.dumps(allowed_values) if allowed_values is not None else None,
            ),
        )
        await self._db.commit()
        await self._load_capability_cache()
    
    # -----------------------------------------------------------------------
    # Model defaults (admin-editable)
    # -----------------------------------------------------------------------
    
    async def set_model_default(self, model: str, setting: str, value: Any) -> None:
        """Set a model default value (admin only)."""
        await self._db.execute(
            """INSERT OR REPLACE INTO model_defaults (model, setting, value)
               VALUES (?, ?, ?)""",
            (model, setting, json.dumps(value)),
        )
        await self._db.commit()
    
    async def get_model_default(self, model: str, setting: str) -> Any | None:
        """Get a model default value."""
        async with self._db.execute(
            "SELECT value FROM model_defaults WHERE model = ? AND setting = ?",
            (model, setting),
        ) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else None
    
    async def get_all_model_defaults(self, model: str) -> dict[str, Any]:
        """Get all model defaults for a model."""
        result = {}
        async with self._db.execute(
            "SELECT setting, value FROM model_defaults WHERE model = ?",
            (model,),
        ) as cursor:
            async for row in cursor:
                result[row[0]] = json.loads(row[1])
        return result