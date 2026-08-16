### Fixed
- Updated `DecisionBlock` tests to match the new interactive contract: pending decisions now render enabled controls (option buttons and free-text textarea) that submit answers, while non-pending decisions render disabled controls or no input at all. Added a first-answer-wins regression test ensuring a second answer is rejected after the first is accepted.
