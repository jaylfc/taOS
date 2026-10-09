```
=================================== FAILURES ===================================
______________ TestReadPinFormat.test_semver_re_matches_the_spec _______________

self = <test_audit_forks.TestReadPinFormat object at 0x7799e23349d0>

    def test_semver_re_matches_the_spec(self):
        # Should match valid SemVer versions
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?", "1.2.3") is not None
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?", "2.8.3-taos.2") is not None
>       assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?", "1.2.3-alpha-beta") is not None
E       AssertionError: assert None is not None
E        +  where None = <function fullmatch at 0x7799e6b7ea30>('\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?', '1.2.3-alpha-beta')
E        +    where <function fullmatch at 0x7799e6b7ea30> = re.fullmatch

tests/test_audit_forks.py:215: AssertionError
=========================== short test summary info ============================
FAILED tests/test_audit_forks.py::TestReadPinFormat::test_semver_re_matches_the_spec
============================== 1 failed in 0.25s ===============================
```
tests/test_audit_forks.py::TestReadPinFormat::test_semver_re_matches_the_spec PASSED