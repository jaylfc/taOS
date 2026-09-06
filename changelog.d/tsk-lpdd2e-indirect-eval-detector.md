### Security

- App Studio's publish/install source scan now flags indirect eval (`window.eval(...)`, `globalThis.eval(...)`, `self.eval(...)`), unquoted dangerous URL schemes in HTML attributes (`<a href=javascript:...>`), and sandbox-escape references reached through a global-object prefix (`globalThis.top`, `self.parent`, `self.opener`). All three slipped past the previous patterns.
