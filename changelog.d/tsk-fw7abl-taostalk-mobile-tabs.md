### Added

- taOStalk mobile PWA (chat-pwa) now ships a bottom tab bar with Chats,
  Projects, Decisions, and Agents. The Chats tab keeps the user on the
  in-place MessagesApp; the other three deep-link into the desktop shell
  via `/desktop?app=<id>` so the chat PWA stays a focused comms surface
  without re-implementing sibling platform apps. The bar is hidden on
  desktop viewports and never overflows horizontally on a phone-size
  viewport, so the new tabs do not regress the existing single-column
  message view or the `MobileSplitView` back-nav.
