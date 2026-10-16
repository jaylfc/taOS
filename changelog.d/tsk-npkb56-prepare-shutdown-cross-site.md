### Fixed

- A web page open in a browser on the taOS host can no longer trigger the agent shutdown drain through the loopback exemption on `/api/system/prepare-shutdown`. The loopback exemption now excludes requests carrying an `Origin` header or a `Sec-Fetch-Site` header whose value is not `none`.