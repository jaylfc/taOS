# tsk-ha5iau: LoRa Off-Grid Transport for taOS - Design Note

## Executive Summary
This design note addresses the requirement to add Meshtastic as a first-class platform in the taOS channel hub, routing messages to the correct agent via the existing `MessageRouter`. The solution adds a `meshtastic_connector.py` alongside the seven existing connectors and a Talk app surface, inheriting routing, archive and the existing app for free. The hard work is degradation policy for the 237-byte text-only link, not transport plumbing.

## 1. Enforced Security Model for the Bridge

### Core Principles
- **Zero Trust Extension**: The connector does NOT inherit channel hub trust. Messages must be authenticated at the connector ingress before reaching the router.
- **Per-Device Authentication**: Each Heltec module is registered with unique cryptographic keys.
- **Message Replay Protection**: All frames include a timestamp and sequence number to prevent replay attacks.
- **Payload Integrity**: AES-256-GCM encryption with per-frame nonces for message integrity.

### Security Architecture
```
┌─────────────────────────────────────────────────────────────┐
│  Source (LoRa)                                              │
│  • Signed payload (device key)                              │
│  • Sequence number + timestamp                             │
│  • AES-256-GCM encrypted                                        │
└─────────────────┬-------------------------------------------┘
                   │ Verify signature & decrypt
┌─────────────────▼-------------------------------------------─┐
│  meshtastic_connector.py                                    │
│  • Device key registry                                       │
│  • Sequence number validation                                 │
│  • Timestamp freshness check                                  │
│  • If verification fails: drop & log                        │
└─────────────────┬-------------------------------------------─┘
                   │ Emits IncomingMessage with platform="meshtastic"
                   │ Routes via MessageRouter.get_agent_for_channel
┌─────────────────▼-------------------------------------------─┐
│  channel_hub/router.py MessageRouter                        │
│  • assign_channel(platform="meshtastic", bot_id, agent_name) │
│  • get_agent_for_channel resolves the target agent           │
│  • Standard routing and archive pipeline                     │
└─────────────────────────────────────────────────────────────┘
```

### Security Handling Policies

**Unsigned Frames:**
- Dropped immediately at connector ingress
- Logged with device identifier and geolocation (if available)
- Alert generation: `"LoRa security breach: unsigned frame from <MAC>"

**Replayed Frames:**
- Detected via sequence number gap analysis
- Old frames (timestamp > 5 minutes or sequence number < last_seen) rejected
- Connector maintains sliding window per device
- Alert generation: `"LoRa replay detected from <MAC> (seq <n>)"

**Corrupted Frames:**
- MAC validation failure (AES-GCM tag mismatch)
- Decryption failure
- Dropped, logged, and alert generated

**Allowed Security Violations:**
- Connector may accept unencrypted messages during testing mode (configurable)
- Connector logs and forwards with testing flag for debugging
- All production deployments require encryption

## 2. Integration via channel_hub

### Existing Seam
`tinyagentos/channel_hub/` holds seven working connectors on a common envelope: discord, telegram, slack, matrix, email, webchat, webhook (4-5K each). `channel_hub/message.py` defines `IncomingMessage` with a `platform` field and `OutgoingMessage` for replies. `channel_hub/router.py` `MessageRouter` already provides `assign_channel(platform, bot_id, agent_name)` and `get_agent_for_channel(platform, bot_id)`.

### meshtastic_connector.py
The new connector sits alongside the existing seven. It emits `IncomingMessage(platform="meshtastic", ...)` and calls `self.router.route_message(self.agent_name, incoming)`, exactly like `telegram_connector.py` or `webchat_connector.py`. A Meshtastic channel (or node) maps to one agent via `assign_channel`. Addressing lives on the channel the message arrived on, not in the payload.

### Routing Keys on the Channel, Not the Payload
Map one Meshtastic channel (or node) to one agent and addressing costs ZERO bytes of the ~237 byte packet. The naive alternative is bad: real agent identities on this fleet are 25-30 chars (kilo-taos-20260711-000740, laguna-s-ora-20260721-191750, stepflash-taos-20260713-103907). Carrying sender + recipient in-payload would burn ~60 of 237 bytes, a quarter of the packet, before a single character of content. Meshtastic supports 8 channels, so channel-per-agent caps there and node-per-agent needs a board per agent; past that use a SHORT-CODE REGISTRY (2-4 bytes) mapped to canonical identity. Never put canonical ids on the radio.

## 3. Degradation Policy

### The Problem
`OutgoingMessage` carries buttons, images and cards, and `message.py:parse_inline_hints` parses `[button:Label:action]` and `[image:path]` out of agent replies. NONE of that survives a text-only ~237 byte link. The connector needs an explicit policy for rich elements, long replies, and a hard per-message size budget enforced BEFORE transmit. An agent that answers in 2KB of prose must not silently become a 12-packet flood.

### Rich Element Policy
| Element | Policy |
|---------|--------|
| Buttons | Drop. Log a one-time notice per conversation: `"[button dropped: Meshtastic is text-only]"` |
| Images | Drop. Log a one-time notice per conversation: `"[image dropped: Meshtastic is text-only]"` |
| Cards | Drop. Log a one-time notice per conversation: `"[card dropped: Meshtastic is text-only]"` |

### Long Reply Policy
| Length | Policy |
|--------|--------|
| <= 237 bytes | Transmit as-is |
| > 237 bytes | Chunk into 237-byte segments with a `[part N/M]` prefix; reassemble on receive if needed |

### Hard Size Budget
Before transmit, every frame is verified to be <= 237 bytes on the wire. Long replies are chunked with a `[part N/M]` prefix whose denominator always equals the emitted part count; the connector guard raises (rather than shipping an over-budget frame) if any part still exceeds the limit after degradation.

### Example Degradation
```python
# In channel_hub/message.py
MAX_PAYLOAD = 237

def _degrade(response: OutgoingMessage) -> tuple[list[str], list[str]]:
    """Degrade rich elements and chunk long replies for the text-only link.

    - Reads response.buttons, response.images, response.cards; drops them
      and emits a notice per element kind (a fresh notice list per response).
    - Chunks on encoded bytes, never splitting a multibyte character: the
      chunk boundary is walked back to the last codepoint boundary before
      decoding, and the '[part N/M] ' prefix bytes always count against
      the 237-byte budget.
    - Derives total from the same chunking the parts use, so the label
      denominator always equals the number of emitted parts.
    """
    notices: list[str] = []

    # Drop buttons -- emit a notice per response
    if response.buttons:
        notices.append("[button dropped: Meshtastic is text-only]")
        response.buttons = []

    # Drop images -- emit a notice per response
    if response.images:
        notices.append("[image dropped: Meshtastic is text-only]")
        response.images = []

    # Drop cards -- emit a notice per response
    if response.cards:
        notices.append("[card dropped: Meshtastic is text-only]")
        response.cards = []

    # The text is already clean (parse_inline_hints stripped markup),
    # but we work with whatever content remains.
    encoded = response.content.encode("utf-8")
    if not encoded:
        return [], notices

    # Provisional total ignores the prefix length; refine until the label
    # denominator equals the actual number of emitted parts.
    total = max(1, (len(encoded) + MAX_PAYLOAD - 1) // MAX_PAYLOAD)
    while True:
        parts: list[str] = []
        idx = 1
        start = 0
        while start < len(encoded):
            prefix = f"[part {idx}/{total}] ".encode("utf-8")
            content_bytes = MAX_PAYLOAD - len(prefix)
            end = min(start + content_bytes, len(encoded))
            # Walk back to a codepoint boundary so we never slice a
            # multibyte character (which would corrupt it and inflate the
            # re-encoded size past the budget).
            while end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
                end -= 1
            chunk_text = encoded[start:end].decode("utf-8")
            parts.append(f"[part {idx}/{total}] {chunk_text}")
            start = end
            idx += 1
        if len(parts) == total:
            return parts, notices
        total = len(parts)
```

## 4. Sovereignty

### Real, With One Caveat
Every other connector except webchat/webhook puts a company in the path. The sharper claim is not merely self-hosted but INFRASTRUCTURE INDEPENDENT: it keeps working with no ISP, no internet and no LAN. Caveat to design around rather than a blocker: encryption protects CONTENT, not PRESENCE. Broadcast RF means anyone in range observes that a transmission happened, roughly from where, how often and how large, and RF is direction-findable. Sovereignty over custody and content, not invisibility. Do not let the pitch drift into the latter.

## 5. Concrete First Milestone (Hardware Phase)

### Version 0.1.0 - "Point-to-Point Prototype"
**Target**: Q3 2026 (after hardware arrival)

#### Primary Objectives
1. **Hardware Setup**: Deploy two Heltec V4 modules in point-to-point configuration
2. **Connector Development**: Implement `meshtastic_connector.py` emitting `platform="meshtastic"`
3. **Authentication**: Complete per-device key registration and validation system
4. **Degradation Testing**: Verify rich elements are dropped, long replies are chunked, and the 237-byte budget is enforced

#### Technical Deliverables
- [ ] `meshtastic_connector.py` alongside the seven existing connectors
- [ ] Device key management system with secure storage
- [ ] `MessageRouter.assign_channel("meshtastic", <node_id>, <agent_name>)` wiring
- [ ] Configuration management for radio parameters (freq, SF, channel)
- [ ] Logging and monitoring for security events and degradation decisions
- [ ] Test suite for connector functionality, security properties, and degradation policy

#### Deployment Architecture
```
┌─────────────────────────────────────────────────────────┐
│  TAOS Controller                                        │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  channel_hub                                    │   │
│  │  • MessageRouter.get_agent_for_channel("meshtastic", node_id) │
│  │  • Archive and routing pipeline                  │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  meshtastic_connector.py                         │   │
│  │  • Ingest Meshtastic packets                     │   │
│  │  • Verify signature & decrypt                    │   │
│  │  • Degrade rich elements, chunk long replies     │   │
│  │  • Emit IncomingMessage(platform="meshtastic")   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Radio Links (2 Heltec V4 modules)                 │   │
│  │                                                 │   │
│  │  +----------------------+                    │   │
│  │  │  Module 1 (Radio 1) │                    │   │
│  │  │  - 919-923 MHz      │                    │   │
│  │  │  - 28 dBm TX        │                    │   │
│  │  +----------------------+                    │   │
│  │  │  Module 2 (Radio 2) │                    │   │
│  │  │  - 923-924 MHz      │                    │   │
│  │  │  - 28 dBm TX        │                    │   │
│  │  +----------------------+                    │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

#### MVP Testing Requirements
- [ ] Message round-trip testing: Radio -> connector -> router -> agent -> connector -> Radio
- [ ] Security validation: Verify unsigned frames are rejected
- [ ] Degradation validation: Verify buttons and images are dropped with logged notices
- [ ] Size budget validation: Verify long replies are chunked or truncated to 237 bytes
- [ ] Performance testing: 1kbps sustained throughput with <1s latency

#### Success Criteria
1. **Functional**: Both radio modules connect and exchange messages
2. **Security**: All unsigned/replayed messages are rejected
3. **Degradation**: Rich elements are dropped and long replies are chunked
4. **Performance**: Status beacons sent every 30 seconds
5. **Reliability**: Connector survives controller reboot without reconnection

### Notes
- This design focuses on the security model first as required
- Firmware implementation will follow after this design is approved
- The connector is a CONTROL channel, not a data tunnel
- All security decisions must be made before hardware deployment

## References
- [Heltec WiFi LoRa 32 V4 Datasheet](https://docs.heltec.org/en/latest/wifi_lora_32/tty/v4.html)
- [Meshtastic Documentation](https://meshtastic.org/)
- [taOS channel_hub Architecture](/tinyagentos/channel_hub/)
- [channel_hub/message.py](/tinyagentos/channel_hub/message.py)
- [channel_hub/router.py](/tinyagentos/channel_hub/router.py)
- [Jay's Note 2026-08-28](note-260828-f0fa88.md - reference implementation)

---
*Document created: 2026-08-28*
*Status: Draft design note*
*Author: taOS Lead*
*Tags: lora, meshtastic, channel_hub, security, radio*
