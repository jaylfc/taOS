### Fixed

- Browser proxy now rewrites `@import` preludes in CSS and rewrites `text/css` response bodies through the proxy, closing isolation leaks where stylesheet URLs bypassed cookie isolation and the SSRF choke point.
