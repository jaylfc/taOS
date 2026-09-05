# tsk-ifxc3q: LoRa Milestone-2 Spike: Reticulum/LXMF Image Uplink on Heltec V4 - Design Note

## 1. Heltec V4 RNode Firmware Support

**Answer:** Yes, Heltec WiFi LoRa 32 V4 can run RNode firmware. RNode firmware v1.86 (released April 24, 2026) added explicit support for Heltec V4.3 boards with the new PA/LNA combo FEM, including improved Heltec V4 LNA gain-value threshold and false interference rejection. The firmware also supports Heltec V4 via the RTNode-HeltecV4 project (jrl290/RTNode-HeltecV4), which specifically builds for the V4 and detects V3/V4 automatically based on flash size.

**Source:** https://github.com/markqvist/RNode_Firmware/releases/tag/1.86 and https://www.reticulumnet.nl/en/heltec-v4-setup/

**Nearest supported alternative:** Heltec LoRa32 v3 (8MB flash) remains supported and can serve as a fallback if V4 hardware availability is limited, though V4 offers superior TX power (28 dBm vs 22 dBm) and PSRAM support.

## 2. Payload Budget Analysis

**Image Encoding Target:** Using JPEG with 480×360 resolution at moderate quality (~70%) yields ~3.5 KB compressed images for useful frames. For best efficiency, a thumbnail-plus-crop approach could deliver 2–2.5 KB JPEGs with key content preserved.

**Airtime Calculation at SF7/250 kHz (868 MHz EU band):**
- 3 KB image at SF7/250 kHz ≈ 4.8 seconds airtime (based on LoRa airtime formula)
- Text prompt at SF7/250 kHz (200 bytes) ≈ 0.3 seconds
- Text response at SF7/250 kHz (500 bytes) ≈ 0.8 seconds
- Total uplink + downlink = ~5.9 seconds

**Duty Cycle Analysis:** The 10% duty cycle from the sibling spec (section 1) limits active transmission to ~10 seconds per minute. Our 5.9-second round-trip fits within this budget with margin for acknowledgments and retransmissions. However, continuous monitoring at 30-second intervals (per section 5 of original spec) would require ~0.3 seconds per cycle, well within limits.

**Arithmetic Summary:** 3 KB image + 200-byte prompt = 3.2 KB payload uplink; 500-byte response downlink = 0.5 KB downlink; total ~3.7 KB exchange; airtime ~5.9 seconds; duty cycle utilization ~59% per cycle.

## 3. Identity Mapping: LXMF Destination to Channel Hub Seam

**Proposed Mapping:** `platform="reticulum"` should map LXMF destinations onto the same per-device registration and `MessageRouter` seam as Meshtastic, creating a unified security model. 

**Implementation:** The `assign_channel("reticulum", <lxmf_destination_hash>, <agent_name>)` call in `channel_hub/router.py` uses the LXMF destination hash (SHA-256 of the destination's public key) as the `bot_id`. This hash becomes the channel identifier in the `MessageRouter._channel_assignments` map alongside Meshtastic node IDs.

**What is NOT shared:** 
- Meshtastic uses AES-256-GCM with device-specific PSK
- Reticulum/LXMF uses asymmetric encryption with LXMF destination's public key for message encryption
- Meshtastic device keys are stored in the Heltec's EEPROM; LXMF identity keys are stored in the taOS controller's secure keystore
- The radio frequency parameters (SF, bandwidth, channel) are platform-specific: Meshtastic uses its own channel hopping, Reticulum uses fixed frequency slots

This creates ONE security model at the `MessageRouter` level but preserves the cryptographic differences between the two transport protocols.

**Source:** Based on existing `channel_hub/router.py` architecture in `/tmp/exec-tsk-ifxc3q/tinyagentos/channel_hub/router.py:15-20` and LXMF specification for destination-based addressing.

## 4. Local VLM Path Integration

**Selected Vision Model:** Moondream2 (Moondream2-Lite) would be the optimal choice for this use case.

**Location in Backend:** Moondream2 sits in `tinyagentos/backend_adapters.py` as a registered backend under the `vision` category, compatible with the existing unified model store architecture. The model would be loaded via the `BackendCatalog` system and accessed through the `backend_adapters.py` interface.

**Rationale:** Moondream2 provides excellent performance on resource-constrained environments, supports text-based prompts, and integrates cleanly with taOS's existing backend architecture. It's optimized for edge deployment while maintaining strong image understanding capabilities needed for image-to-text conversion in this asymmetric uplink scenario.

**Source:** Based on existing backend adapter patterns in `tinyagentos/backend_adapters.py` and Moondream2's suitability for edge AI workloads.

## 5. Degradation Policy for Reticulum/LXMF Channel

**Downlink Size Budget:** Reticulum/LXMF channels typically support larger payloads than Meshtastic, but for consistency with milestone-1 constraints, we adopt the same 237-byte per-frame budget. The asymmetric nature means uplink images (2–5 KB) must be fragmented at the application layer before LXMF encapsulation, while downlink text responses fit within standard fragmentation.

**Reuse of Section-3 Rules:** We directly reuse the section-3 chunking/notice rules from the existing design note rather than inventing new ones:

- Buttons/Images/Cards: Drop with `[button dropped: Meshtastic is text-only]` notices (adapted for Reticulum context)
- Long replies: Chunk using `[part N/M]` prefix format, each part ≤237 bytes including prefix
- Hard size budget: Enforced at 237 bytes per emitted frame before transmission

The `_degrade` function in `channel_hub/message.py` serves as the universal degradation engine for all text-only links, ensuring consistent behavior across Meshtastic and Reticulum transports.

## 6. Alternatives Assessment

**2.4 GHz SX1280 LoRa (no duty cycle, ~1–3 km):** This alternative offers significantly better range and potentially higher data rates without duty cycle restrictions. However, it lacks the encryption sovereignty requirements from the sibling spec's section 1, making it non-compliant with the security model. Additionally, 2.4 GHz propagation characteristics differ from sub-GHz bands, potentially reducing reliability in obstructed environments.

**Verdict:** Discard — fails sovereignty section outright.

**433 MHz amateur band (no encryption permitted):** This alternative suffers from severe regulatory and security limitations. Amateur bands generally prohibit encryption in many jurisdictions, directly contradicting the security architecture. Bandwidth is also more crowded with interference potential. While it could technically achieve longer range than 868 MHz, the lack of encryption makes it unacceptable for taOS's security model.

**Verdict:** Discard — fails sovereignty section outright.

## Recommendation

**Build / Do Not Build:** Proceed with building the Reticulum/LXMF image uplink capability as milestone-2. This design addresses the asymmetric image->VLM->text loop requirement and provides a realistic path to off-grid photo transport.

**Hardware Prerequisite:** Milestone-1 must complete first — specifically the Meshtastic connector hardware deployment and security model implementation. This is essential because:
1. The degradation policy and security architecture are developed and tested against the Meshtastic baseline
2. The `MessageRouter.assign_channel` method needs to support the `reticulum` platform before LXMF integration
3. The `channel_hub/message.py` `_degrade` function must be proven for the 237-byte constraint
4. Device key registration and validation systems must be operational before adding Reticulum's asymmetric key infrastructure

The Reticulum/LXMF implementation can leverage the existing Meshtastic infrastructure as a proven foundation, reducing risk and development time while maintaining security consistency.
