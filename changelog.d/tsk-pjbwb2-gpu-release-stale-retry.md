### Fixed
- Prevent stale GPU release retry from clearing newer claim when re-releasing an expired lease ID that names no active lease but the same actor has since reclaimed the same node.