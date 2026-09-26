### Security

- Bluetooth board pairing now refuses (409) a board whose advertised name matches an existing worker, or a device that has not been revoked, so a board can no longer take over another node's key, clear its block or revoke state, or have a failed provision unregister the real node. A rolled-back pairing only undoes what it wrote itself.
- A paired device (kind=device) can no longer promote itself to a worker by registering or heartbeating with its own key: the kind is recorded with the key at pairing and kept on every registration, so the device never becomes a candidate for chat, embed, image or browser jobs.
