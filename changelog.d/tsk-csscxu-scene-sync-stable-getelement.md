### Fixed
- Flush pending SceneSync writes before dispose to prevent edits from being dropped when closing the board or switching project within the debounce window.