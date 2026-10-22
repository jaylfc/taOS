"""USB and LAN discovery for taOS Orbs (ESP32-S3)."""

from .frames import TransportUnavailable

ESPRESSIF_VID = 0x303A

ORB_SERVICE_TYPE = "_taos-orb._tcp.local."


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
    """Browse LAN for taOS Orbs via mDNS. Blocks for `timeout` seconds; async callers must use asyncio.to_thread."""
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
    browser = ServiceBrowser(zc, ORB_SERVICE_TYPE, listener)
    try:
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