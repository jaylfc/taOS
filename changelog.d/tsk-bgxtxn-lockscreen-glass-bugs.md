### Fixed

- Phone lock screen: swiping up from an agent island no longer opens the PIN
  keypad. Measured at the device's real viewport with a full screen of agents,
  `#ls-feed` does not overflow -- it is content-sized and the islands fit -- so
  the unlock veto, which asked only how far the feed could still travel, was
  never arming. A feed that cannot scroll at all is now treated as the cards
  they are rather than as a feed already read to its end. Swiping up at the end
  of a feed that CAN scroll still unlocks, unchanged.

- Phone lock screen: the agent islands no longer flicker every fifteen seconds.
  The activity poll wiped the list and rebuilt every island, so each one was a
  new element replaying its 520ms entrance animation on every tick, whether or
  not anything had changed. The list is now reconciled by agent name and updated
  in place: an island that persists keeps its element, its animation and its
  keyboard focus.
