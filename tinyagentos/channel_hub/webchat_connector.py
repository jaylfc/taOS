from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class WebChatConnector:
    def __init__(self, agent_name: str, router):
        self.agent_name = agent_name
        self.router = router
        self._connections: dict[str, WebSocket] = {}

    async def stop(self):
        """No-op: WebChatConnector has no background task to stop."""

    async def handle_websocket(self, websocket: WebSocket, user_id: str | None = None):
        await websocket.accept()
        conn_id = str(uuid.uuid4())[:8]
        self._connections[conn_id] = websocket

        try:
            while True:
                data = await websocket.receive_text()
                msg_data = json.loads(data)

                incoming = IncomingMessage(
                    id=str(uuid.uuid4())[:8],
                    from_id=user_id or conn_id,
                    from_name=msg_data.get("name", "User"),
                    platform="web",
                    channel_id=f"webchat-{conn_id}",
                    channel_name="Web Chat",
                    text=msg_data.get("text", ""),
                )

                response = await self.router.route_message(self.agent_name, incoming)
                if response:
                    await websocket.send_text(json.dumps({
                        "content": response.content,
                        "buttons": response.buttons,
                        "images": response.images,
                        "timestamp": time.time(),
                    }))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WebChat error for connection {conn_id}: {e}")
        finally:
            self._connections.pop(conn_id, None)
