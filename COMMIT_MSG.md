fix: declare dompurify + @codemirror/commands + @codemirror/language as desktop dependencies

FENCED BLOCK - RED (test written, fix not yet applied):
FAIL  src/__tests__/deps.test.ts > every directly-imported package is declared as a dependency
AssertionError: expected [ 'dompurify', …(2) ] to deeply equal []

- []
+ [
+   "dompurify",
+   "@codemirror/language",
+   "@codemirror/commands",
+ ]

FENCED BLOCK - GREEN (after fix):
 Test Files  1 passed (1)
   Tests  1 passed (1)