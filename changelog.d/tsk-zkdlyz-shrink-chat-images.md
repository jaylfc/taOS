### Changed

- LLM gateway now shrinks oversized chat images once, in a shared provider-agnostic step before routing, so providers receive images within their limits (#tsk-zkdlyz).
- Oversized-pixel and animated images are passed through unchanged.
