### Security
- UnifiedPush push-token endpoints now validate URLs against SSRF guards before storing or sending. Loopback, link-local, multicast, reserved, and unspecified addresses are refused at registration and again at send time. Decimal-encoded and IPv4-mapped IPv6 loopback forms are also blocked. RFC1918 and CGNAT private ranges remain allowed for self-hosted LAN use cases. (#tsk-kqryuo)
