### Fixed

- Re-pairing a taOSusb board under the same name with the LLM gateway off no longer leaves an orphan model key behind. `revoke_for_node` is now called unconditionally on every successful re-pair and in the rollback path, regardless of whether the gateway is enabled.
