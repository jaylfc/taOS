R2-33: Prove RED-first for mkdocs native exclude_docs replacement

RED test (origin/dev): FAILED tests/test_mkdocs_exclude.py::TestMkdocsExcludeReplacement::test_build_succeeds_without_mkdocs_exclude_plugin

```
FAILED tests/test_mkdocs_exclude.py::TestMkdocsExcludeReplacement::test_build_succeeds_without_mkdocs_exclude_plugin - AssertionError
```

**Test output from origin/dev scratch checkout:**

============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0.0/bin/python3
cachedir: .pytest/cache
Using --randomly-seed=2895861711
rootdir: /tmp/red-mrsk2p
configfile: pyproject.toml
plugins: anyio-4.14.2, asyncio-1.4.0.0, split-0.11.0.0, randomly-4.1.0.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_test_loop_scope=function
collecting > collected 1 item

tests/test_mkdocs_exclude.py::TestMkdocsExcludeReplacement::test_build_succeeds_without_mkdocs_exclude_plugin FAILED [100%]

=================================== FAILURES ====================================
_ TestMkdocsExcludeReplacement.test_build_succeeds_without_mkdocs_exclude_plugin _
self = <test_mkdocs_exclude.TestMkdocsExcludeReplacement object at 0x760fda82d6d0>

    def test_build_succeeds_without_mkdocs_exclude_plugin(self):
        raw = MKdocs_YML.read_text()
        config = _massage_config_for_test(raw, docs_dir="docs")
    
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docs_root = tmp_path / "docs"
            docs_root.mkdir()
            _write_minimal_docs(docs_root)
    
            config_path = tmp_path / "mkdocs.yml"
            config_path.write_text(config)
    
            site_dir = tmp_path / "site"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mkdocs",
                    "build",
                    "--config-file",
                    str(config_path),
                    "--site-dir",
                    str(site_dir),
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
            )
    
>           assert result.returncode == 0, (
                f"mkdocs build failed:\n{result.stdout}\n{result.stderr}"
            )
E           AssertionError: mkdocs build failed:
E            
E            Aborted with a configuration error!
E            
E            
E            \x1b[31m │  ⚠  Warning from the Material for MkDocs team\x1b[0m
E            \x1b[31m │\x1b[0m
E            \x1b[31m │\x1b[0m  MkDocs 2.0, the underlying framework of Material for MkDocs,
E            \x1b[31m │\x1b[0m  will introduce backward-incompatible changes, including:
E            \x1b[31m │\x1b[0m
E            \x1b[31m │  × \x1b[0mAll plugins will stop working – the plugin system has been removed
E            \x1b[31m │  × \x1b[0mAll theme overrides will break – the theming system has been rewritten
E            \x1b[31m │  × \x1b[0mNo migration path exists – existing projects cannot be upgraded
E            \x1b[31m │  × \x1b[0mClosed contribution model – community members can't report bugs
E            \x1b[31m │  × \x1b[0mCurrently unlicensed – unsuitable for production use
E            \x1b[31m │\x1b[0m
E            \x1b[31m │\x1b[0m  Our full analysis:
E            \x1b[31m │\x1b[0m
E            \x1b[31m │\x1b[0m  \x1b[4mhttps://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/\x1b[0m
E            \x1b[0m
E            ERROR   -  Config value 'plugins': The "exclude" plugin is not installed
            
E           assert 1 == 0
E            +  where 1 = CompletedProcess(args=['/usr/bin/python3', '-m', 'mkdocs', 'build', '--config-file', '/tmp/exec-tsk-mrsk2p.tmp/tmpy8tghr3y/mkdocs.yml', '--site-dir', '/tmp/exec-tsk-mrsk2p.tmp/tmpy8tghr3y/site'], returncode=1, stdout='\\nAborted with a configuration error!\\n', stderr='\\n\\x1b[31m │  ⚠  Warning from the Material for MkDocs team\\x1b[0m\\n\\x1b[31m │\\x1b[0m\\n\\x1b[31m │\\x1b[0m  MkDocs 2.0, the underlying framework of Material for MkDocs,\\n\\x1b[31m │\\x1b[0m  will introduce backward-incompatible changes, including:\\n\\x1b[31m │\\x1b[0m\\n\\x1b[31m │  × \\x1b[0mAll plugins will stop working – the plugin system has been removed\\n\\x1b[31m │  × \\x1b[0mAll theme overrides will break – the theming system has been rewritten\\n\\x1b[31m │  × \\x1b[0mNo migration path exists – existing projects cannot be upgraded\\n\\x1b[31m │  × \\x1b[0mClosed contribution model – community members can\\'t report bugs\\n\\x1b[31m │  × \\x1b[0mCurrently unlicensed – unsuitable for production use\\n\\x1b[31m │\\x1b[0m\\n\\x1b[31m │\\x1b[0m  Our full analysis:\\n\\x1b[31m │\\x1b[0m\\n\\x1b[31m │\\x1b[0m  \\x1b[4mhttps://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/\\x1b[0m\\n\\x1b[0m\\nERROR   -  Config value \\\'plugins\\\': The "exclude" plugin is not installed\\n\').returncode

tests/test_mkdocs_exclude.py:108: AssertionError
===========================================

=========================================
SHORT TEST SUMMARY:
FAILED tests/test_mkdocs_exclude.py::TestMkdocsExcludeReplacement::test_build_succeeds_without_mkdocs_exclude_plugin

------------------------------------------

=== GREEN TEST OUTPUT (after fix on BASE) ===

PASSED tests/test_mkdocs_exclude.py::TestMkdocsExcludeReplacement::test_build_succeeds_without_mkdocs_exclude_plugin

=== DOC FRAGMENT ===
### Fixed
R2-33: Replaced mkdocs-exclude plugin with native exclude_docs key in mkdocs.yml

This change replaces the deprecated mkdocs-exclude plugin with the native
exclude_docs key (available since MkDocs 1.5). The test verifies that the
docs site builds successfully without the external plugin.

### CODESENSE
- Changed site/docs/mkdocs.yml:98-102 from "exclude:" plugin to "exclude_docs:" key
