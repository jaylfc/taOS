from __future__ import annotations

from tinyagentos.push.apns import ApnsSender, NullApnsSender
from tinyagentos.push.unifiedpush import UnifiedPushSender, NullUnifiedPushSender

from tinyagentos.push.apns import ApnsSender as ApnsSender
from tinyagentos.push.apns import NullApnsSender as NullApnsSender
from tinyagentos.push.unifiedpush import UnifiedPushSender as UnifiedPushSender
from tinyagentos.push.unifiedpush import NullUnifiedPushSender as NullUnifiedPushSender
from tinyagentos.push.unifiedpush import build_unifiedpush_payload

__all__ = [
    "ApnsSender",
    "NullApnsSender",
    "UnifiedPushSender",
    "NullUnifiedPushSender",
    "build_unifiedpush_payload",
]


async def send_device_push(
    device: dict,
    payload: dict,
    *,
    apns_sender: "ApnsSender",
    up_sender: "UnifiedPushSender",
) -> bool:
    """Send one payload to one device. True when the push service accepted it.

    Propagates ApnsUnregistered: a 410 means the token is permanently dead, and
    a single-device caller has to prune it rather than read it as a plain False.
    The fan-out in notifications_push.send_device_push handles that pruning.
    """
    platform = device.get("platform", "")
    push_token = device.get("push_token", "")
    if not push_token:
        return False
    if platform in ("ios", "watchos"):
        return await apns_sender.send(push_token, payload)
    if platform == "android":
        return await up_sender.send(push_token, payload)
    return False
