### Fixed
- Lock screen: swiping up to unlock works again once you have read to the end of
  the notification feed. The veto added for tsk-6bjsvg asked only whether the
  feed OVERFLOWS, never where it was SCROLLED -- and swipe-up is both the unlock
  gesture and the gesture that scrolls the feed toward its end. At the bottom of
  a long feed the drag could no longer scroll and was vetoed anyway, so nothing
  happened at all over most of the glass. The veto now asks how far the feed can
  still travel, measured at `touchstart` with a pixel tolerance rather than an
  equality (scroll geometry is fractional under a non-integer DPR). A feed with
  nothing to scroll reads as already at its end, so unlock still works across the
  whole screen on a device with one agent and no notifications.
