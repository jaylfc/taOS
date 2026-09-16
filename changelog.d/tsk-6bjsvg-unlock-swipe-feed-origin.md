### Fixed
- Lock screen: scrolling through the notification feed no longer opens the PIN
  keypad. The unlock swipe was bound to `document.body`, so the long upward drag
  that reads to the end of a full feed read as swipe-up-to-unlock. The gesture is
  now vetoed from where the touch BEGAN (latched at `touchstart`, since the finger
  usually leaves the feed before it lifts), and only while the feed actually has
  somewhere to scroll -- so unlock still works over the whole resting screen on a
  device with one agent and no notifications.
