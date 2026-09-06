### Changed
- taOStalk (Messages) structural parity with the Store app: the
  2834-line `desktop/src/apps/MessagesApp.tsx` monolith now lives at
  `desktop/src/apps/MessagesApp/index.tsx`, and the mobile-aware
  toolbar strip is its own `MessagesApp/MobileMessages.tsx` component
  alongside a `MobileMessages.test.tsx` that covers desktop, mobile,
  selected-channel, and standalone-title cases. `MessagesApp.tsx` is
  kept as a re-export shim so every existing `@/apps/MessagesApp`
  import path keeps resolving unchanged. Behaviour, copy and
  styling are identical to before; this is a move plus a focused
  extraction, not a rewrite (#tsk-iahrh5).