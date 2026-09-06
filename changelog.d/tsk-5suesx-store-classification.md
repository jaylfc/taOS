### Fixed

- The Postgres-before-cloud store assessment no longer ships two
  contradictory per-store classifications that disagreed on 37 of 78 stores,
  plus six raw grep dumps and a root-level duplicate. It is consolidated into a
  single verified reference at `docs/design/store-classification-reference.md`
  enumerating all 79 `BaseStore` subclasses (including the previously-omitted
  `ProjectListEntriesStore`), with the rule each of the three columns encodes,
  credential-bearing stores called out first, and the total derived from the
  table row count rather than asserted.
