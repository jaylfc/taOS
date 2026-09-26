### Security
- Bluetooth board pairing now commits the board nonce before the controller reveals its own (protocol v2), so a man in the middle can no longer grind a nonce that makes the controller and the board show the same 6-digit code
- The pairing protocol version is now 2: an old board and a new controller refuse each other with a clear version error, and taOSusb boards need the matching proto.py
