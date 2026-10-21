"""USB discovery for taOS Orbs (ESP32-S3)."""

from .frames import TransportUnavailable

ESPRESSIF_VID = 0x303A


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