### Fixed
- Extract shared `_unpublished_first_party_pins` helper so both first-party tag-pairing tests call the same parser, preventing a regression from passing green when the real-repo test never reaches the tag branch
