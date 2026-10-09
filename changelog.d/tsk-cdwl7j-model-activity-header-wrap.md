### Fixed

- Fixed Model Activity header overflow on mobile widths (tsk-cdwl7j)

**Change:** Applied `flex-wrap` and width constraints to the Model Activity header components to prevent filters from clipping past the card edge at widths ~390px. The title now stays on one line with `whitespace-nowrap`, and filter selects wrap to a second row when space is limited.