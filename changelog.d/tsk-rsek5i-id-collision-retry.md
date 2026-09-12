### Fixed
- new_id collisions are now retried up to 5 times at every store insert site, instead of surfacing as a raw sqlite3.IntegrityError and rolling back the transaction
