### Fixed

- Browser proxy now rewrites `@import` URLs in `<style>` blocks and `text/css` responses through the proxy, replacing the regex-based CSS rewriter with tinycss2 to handle `url()` and `@import` correctly including data-URIs with parentheses.
