### Fixed
- Browser proxy now rewrites `@import` preludes and `text/css` response bodies through the proxy, closing isolation leaks where CSS URLs bypassed cookie isolation and the SSRF choke point. Data-URI `url()` values with nested parentheses are also preserved correctly.
