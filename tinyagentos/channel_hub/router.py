from __future__ import annotations
import logging
import httpx
from tinyagentos.adapters.retry_policy import ROUTER_TIMEOUT_SECONDS
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage, parse_inline_hints

logger = logging.getLogger(__name__)


class MessageRouter:
    def __init__(self):
        self._agent_ports: dict[str, int] = {}  # agent_name -> adapter port
        self._channel_assignments: dict[str, str] = {}  # "platform:bot_id" -> agent_name
        self._next_port = 9001

    def assign_channel(self, platform: str, bot_id: str, agent_name: str):
        key = f"{platform}:{bot_id}"
        self._channel_assignments[key] = agent_name

    def get_agent_for_channel(self, platform: str, bot_id: str) -> str | None:
        return self._channel_assignments.get(f"{platform}:{bot_id}")

    def register_adapter(self, agent_name: str, port: int):
        self._agent_ports[agent_name] = port

    def get_adapter_port(self, agent_name: str) -> int | None:
        return self._agent_ports.get(agent_name)

    def allocate_port(self, agent_name: str) -> int:
        port = self._next_port
        self._next_port += 1
        self._agent_ports[agent_name] = port
        return port

    def set_archive(self, archive) -> None:
        """Set the archive store for zero-loss message capture."""
        self._archive = archive

    async def route_message(self, agent_name: str, message: IncomingMessage) -> OutgoingMessage | None:
        port = self._agent_ports.get(agent_name)
        if not port:
            logger.warning(f"No adapter registered for agent '{agent_name}'")
            return None

        # Archive inbound message (zero-loss layer — never loses a message)
        await self._archive_message(agent_name, message, direction="inbound")

        try:
            # Same constant the adapters size their retry budget against, so
            # neither side can drift into retrying past this deadline.
            async with httpx.AsyncClient(timeout=ROUTER_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"http://localhost:{port}/message",
                    json={
                        "id": message.id,
                        "from_id": message.from_id,
                        "from_name": message.from_name,
                        "platform": message.platform,
                        "channel_id": message.channel_id,
                        "text": message.text,
                        "attachments": message.attachments,
                        "reply_to": message.reply_to,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                # Check if response is structured or plain text with hints
                if isinstance(data.get("content"), str) and not data.get("buttons"):
                    parsed = parse_inline_hints(data["content"])
                    if parsed.buttons or parsed.images:
                        await self._archive_message(agent_name, parsed, direction="outbound",
                                                     platform=message.platform, channel_id=message.channel_id)
                        return parsed

                response = OutgoingMessage(
                    content=data.get("content", ""),
                    buttons=data.get("buttons", []),
                    images=data.get("images", []),
                    cards=data.get("cards", []),
                    reply_to=data.get("reply_to"),
                    passthrough=data.get("passthrough", False),
                    passthrough_platform=data.get("platform", ""),
                    passthrough_payload=data.get("payload", {}),
                )

                # Archive outbound response
                await self._archive_message(agent_name, response, direction="outbound",
                                             platform=message.platform, channel_id=message.channel_id)
                return response
        except Exception as e:
            logger.error(f"Failed to route message to {agent_name}: {e}")
            return None

    async def _archive_message(self, agent_name: str, message, direction: str,
                                platform: str = "", channel_id: str = "") -> None:
        """Archive a channel message to the zero-loss layer. Never blocks routing."""
        archive = getattr(self, "_archive", None)
        if not archive:
            return
        try:
            if isinstance(message, IncomingMessage):
                await archive.record(
                    "conversation",
                    {
                        "content": message.text,
                        "from_id": message.from_id,
                        "from_name": message.from_name,
                        "platform": message.platform,
                        "channel_id": message.channel_id,
                        "direction": "inbound",
                        "message_id": message.id,
                    },
                    agent_name=agent_name,
                    summary=f"[{message.platform}] {message.from_name}: {message.text[:80]}",
                )
            elif isinstance(message, OutgoingMessage):
                await archive.record(
                    "conversation",
                    {
                        "content": message.content,
                        "platform": platform,
                        "channel_id": channel_id,
                        "direction": "outbound",
                    },
                    agent_name=agent_name,
                    summary=f"[{platform}→] {message.content[:80]}",
                )
        except Exception:
            pass  # Never block message routing for archive failures
