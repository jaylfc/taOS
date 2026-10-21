# RED PROOF - MobileChatList epoch-seconds sort/time labels

## Failing test (before fix)

<details>
<summary>Failing test output</summary>

```
FAIL src/apps/chat/__tests__/epoch-test.test.tsx > MobileChatList epoch-seconds sorting > sorts channels with epoch-seconds last_message_at in recency order (REAL API values)
AssertionError: expected [ 'alpha', 'beta', 'gamma' ] to deeply equal [ 'beta', 'gamma', 'alpha' ]

- Expected
+ Received

  [
+   "alpha",
    "beta",
    "gamma",
-   "alpha",
  ]
```
</details>

The test fails because the original `sortKey()` uses `Date.parse(c.last_message_at)` which returns `NaN` for epoch-second values (e.g., 1727788920), causing the sort to fall back to `created_at` and stay in creation order instead of most-recent-first.

After fix: all three acceptance criteria pass:
1. Epoch-seconds numbers sort in recency order (3000, 2000, 1000)
2. ISO strings still sort correctly  
3. null last_message_at falls back to created_at
