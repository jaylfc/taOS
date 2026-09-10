### Fixed
- Expression indexes are now properly parsed using sqlglot instead of regex
- Quoted identifiers in ALTER TABLE statements are now detected
- Unterminated CREATE TABLE statements are now caught as violations
- Both schema-migration and retrofit-migration guards use a real SQL parser for robust parsing