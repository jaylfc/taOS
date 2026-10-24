"""USB serial transport. pyserial is imported lazily so installs without it still boot."""
import glob

from .frames import OrbError


# ------------------------------------------------------------------ device ----
def open_port(port, baud=115200, timeout=0.2):
    """Open without resetting the chip: DTR and RTS are set low BEFORE the open and again after."""
    import serial
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.dtr = False
    ser.rts = False
    return ser


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*"))
    if not ports:
        raise OrbError("no serial port found (pass -p PORT)")
    return ports[0]


