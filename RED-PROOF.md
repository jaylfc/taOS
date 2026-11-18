# RED-PROOF: tsk-tq2znp — workspace root delete via empty/dot path

Before the fix, `DELETE /api/workspace/files/` (and the agent + project
mirrors) moves the whole workspace root into the trash because
`_resolve_safe` accepts the base itself.

Failing run on dev (tests written, fix not applied):

```text
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_delete_root_is_refused[]
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_delete_root_is_refused[.]
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_delete_root_is_refused[%2e]
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_delete_root_is_refused[sub%2F..]
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_delete_root_is_refused[sub/..]
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_rename_root_refused[.]
FAILED tests/test_routes_user_workspace.py::TestRootDeleteRefused::test_rename_root_refused[sub/..]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_delete_root_is_refused[]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_delete_root_is_refused[.]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_delete_root_is_refused[%2e]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_delete_root_is_refused[sub%2F..]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_delete_root_is_refused[sub/..]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_rename_root_refused[]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_rename_root_refused[.]
FAILED tests/test_routes_agent_workspace.py::TestAgentRootDeleteRefused::test_rename_root_refused[sub/..]
FAILED tests/test_routes_project_files.py::TestProjectRootDeleteRefused::test_delete_root_is_refused[]
FAILED tests/test_routes_project_files.py::TestProjectRootDeleteRefused::test_delete_root_is_refused[.]
FAILED tests/test_routes_project_files.py::TestProjectRootDeleteRefused::test_delete_root_is_refused[%2e]
FAILED tests/test_routes_project_files.py::TestProjectRootDeleteRefused::test_delete_root_is_refused[sub%2F..]
FAILED tests/test_routes_project_files.py::TestProjectRootDeleteRefused::test_delete_root_is_refused[sub/..]
20 failed, 4 passed in 43.66s
```

After the fix (`_resolve_safe_child` refuses the base itself, used by every
delete route):

```text
118 passed in 187.79s
```