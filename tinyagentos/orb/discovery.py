"""USB and LAN discovery for taOS Orbs (ESP32-S3)."""

from .frames import TransportUnavailable

ESPRESSIF_VID = 0x303A

ORB_SERVICE_TYPE = "_taos-orb._tcp.local."

# Agent Buddy UDP discovery
AGENT_BUDDY_PORT = 4666
AGENT_BUDDY_PROBE = b"ESP32_AGENT_COMPANION_DISCOVER_V1"


def _parse_service_info(info) -> dict:
    """Parse a zeroconf ServiceInfo-like object into a dict."""
    name = info.name
    if name.endswith(ORB_SERVICE_TYPE):
        name = name[:-len(ORB_SERVICE_TYPE)]
    if name.endswith("."):
        name = name[:-1]
    
    addresses = sorted(info.parsed_addresses())
    port = info.port
    
    props = info.properties or {}
    board = props.get(b"board")
    app = props.get(b"app")
    ver = props.get(b"ver")
    
    def decode(val):
        if val is None:
            return None
        return val.decode("utf-8", errors="replace")
    
    return {
        "name": name,
        "addresses": addresses,
        "port": port,
        "board": decode(board),
        "app": decode(app),
        "version": decode(ver),
    }


def list_usb_orbs() -> list[dict]:
    """List attached taOS Orbs by USB VID."""
    try:
        import serial.tools.list_ports
    except ImportError as e:
        raise TransportUnavailable("pyserial not installed: pip install 'tinyagentos[orb]'") from e
    
    result = []
    for port in serial.tools.list_ports.comports():
        if port.vid == ESPRESSIF_VID:
            result.append({
                "port": port.device,
                "vid": port.vid,
                "pid": port.pid if port.pid is not None else 0,
                "serial_number": port.serial_number,
                "description": port.description,
            })
    result.sort(key=lambda x: x["port"])
    return result


def browse_lan_orbs(timeout: float = 2.0) -> list[dict]:
    """Browse the LAN for taOS Orbs via mDNS. Browses for `timeout` seconds, then adds up to 1 second per discovered name for service lookups; async callers must use asyncio.to_thread."""
    from zeroconf import ServiceBrowser, Zeroconf
    import time
    
    zc = Zeroconf()
    collected_names = []
    
    class _Listener:
        def add_service(self, zc, type_, name):
            collected_names.append(name)
        
        def remove_service(self, zc, type_, name):
            pass
        
        def update_service(self, zc, type_, name):
            pass
    
    listener = _Listener()
    try:
        browser = ServiceBrowser(zc, ORB_SERVICE_TYPE, listener)
        time.sleep(timeout)
        
        # Deduplicate names
        unique_names = list(dict.fromkeys(collected_names))
        
        results = []
        for name in unique_names:
            info = zc.get_service_info(ORB_SERVICE_TYPE, name, timeout=1000)
            if info is None:
                continue
            results.append(_parse_service_info(info))
        
        results.sort(key=lambda x: x["name"])
        return results
    finally:
        zc.close()


def ipv4_broadcast_addresses() -> list[str]:
    """Get per-interface IPv4 broadcast addresses (default for probe_agent_buddy)."""
    import psutil
    import socket
    
    broadcast_set = set()
    
    try:
        if_addrs = psutil.net_if_addrs()
    except OSError:
        return ["255.255.255.255"]

    for interface_addrs in if_addrs.values():
        for addr_info in interface_addrs:
            # Only consider IPv4 addresses
            if addr_info.family == socket.AF_INET:
                # Skip localhost addresses
                if addr_info.address.startswith("127."):
                    continue
                # Skip if broadcast is None or empty
                if not addr_info.broadcast:
                    continue
                broadcast_set.add(addr_info.broadcast)
    
    if not broadcast_set:
        return ["255.255.255.255"]
    
    # Return sorted list
    return sorted(broadcast_set)


def parse_agent_buddy_reply(data: bytes, address: str) -> dict | None:
    """Parse an Agent Buddy discovery reply.
    
    Returns a dict with device_id, hostname, address, port, boot on success.
    Returns None on any decode/parse error or if validation fails.
    """
    import json
    import re
    
    try:
        text = data.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    
    if not isinstance(parsed, dict):
        return None
    
    # Validate required fields
    if parsed.get("service") != "esp32-agent-companion":
        return None
    
    protocol = parsed.get("protocol")
    if not isinstance(protocol, int) or isinstance(protocol, bool) or protocol != 1:
        return None
    
    device_id = parsed.get("deviceId")
    if not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", device_id):
        return None
    
    hostname = parsed.get("hostname")
    if not isinstance(hostname, str):
        return None
    
    port = parsed.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        return None
    
    boot = parsed.get("boot")
    if not isinstance(boot, int) or isinstance(boot, bool) or boot < 0:
        return None
    
    return {
        "device_id": device_id.lower(),
        "hostname": hostname,
        "address": address,
        "port": port,
        "boot": boot,
    }


def probe_agent_buddy(timeout: float = 1.2, addresses=None, port: int = AGENT_BUDDY_PORT) -> list[dict]:
    """Probe for Agent Buddy devices via UDP broadcast.
    
    Sends a discovery probe to each address and collects replies for `timeout` seconds.
    Returns a list of device dicts sorted by (hostname, device_id).
    
    Async callers must use asyncio.to_thread.
    """
    import socket
    import time
    
    if addresses is None:
        addresses = ipv4_broadcast_addresses()
    
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
    except OSError as e:
        if sock is not None:
            sock.close()
        raise TransportUnavailable("Failed to create or bind UDP socket") from e
    
    try:
        # Send probe to each address
        for addr in addresses:
            try:
                sock.sendto(AGENT_BUDDY_PROBE, (addr, port))
            except OSError:
                # Skip failed sends, continue with other addresses
                pass
        
        # Receive replies until timeout
        deadline = time.monotonic() + timeout
        results = {}
        
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, (remote_ip, _) = sock.recvfrom(2048)
            except socket.timeout:
                break
            except OSError:
                break
            
            parsed = parse_agent_buddy_reply(data, remote_ip)
            if parsed is not None:
                results[parsed["device_id"]] = parsed
        
        # Sort by (hostname, device_id)
        sorted_results = sorted(results.values(), key=lambda x: (x["hostname"], x["device_id"]))
        return sorted_results
    finally:
        if sock is not None:
            sock.close()