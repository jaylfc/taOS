# RED-PROOF — tsk-vecegs device-bearer share writes

## Failing run (pre-fix)

```
tests/test_share_device_writes.py::TestDeviceBearerProjectFiles::test_device_can_upload_when_member_with_write FAILED
  assert 401 == 200
  +  where 401 = <Response [401 Unauthorized]>.status_code

tests/test_share_device_writes.py::TestDeviceBearerProjectFiles::test_device_cannot_upload_to_other_project FAILED
  assert 401 in (403, 404)
  +  where 401 = <Response [401 Unauthorized]>.status_code

tests/test_share_device_writes.py::TestDeviceBearerProjectFiles::test_device_can_upload_to_owned_project FAILED
  assert 401 == 200
  +  where 401 = <Response [401 Unauthorized]>.status_code

tests/test_share_device_writes.py::TestDeviceBearerLibraryIngest::test_device_can_ingest_url FAILED
  assert 401 == 202
  +  where 401 = <Response [401 Unauthorized]>.status_code

tests/test_share_device_writes.py::TestDeviceBearerLibraryIngest::test_device_cannot_ingest_without_token FAILED
  assert 202 == 401
  +  where 202 = <Response [202 Accepted]>.status_code

tests/test_share_device_writes.py::TestDeviceBearerChatMessages::test_device_can_post_to_member_channel FAILED
  assert 401 == 200
  +  where 401 = <Response [401 Unauthorized]>.status_code

tests/test_share_device_writes.py::TestDeviceBearerChatMessages::test_device_cannot_post_to_non_member_channel FAILED
  assert 401 == 403
  +  where 401 = <Response [401 Unauthorized]>.status_code
```

## Root cause

The three ingest routes (`library/ingest`, `projects/{slug}/files/upload`, `chat/messages`) and the share destination list contain dead device-bearer branches that read `request.state._device`. The auth layer never populates `_device` for these routes because they do not declare `Depends(current_user_or_device)`. A device bearer therefore hits the 401/empty-auth fallback even when presenting a valid `taosdev_` token.

In addition:
- `share.py` uses the loose literal `"user"` for chat membership checks.
- `project_files.py` restricts device bearers to project owners only; members with `can_edit_canvas=1` are rejected.
- `share.py` lists only `list_for_user` (owner-only) projects, not writable-member projects.

## Green run (post-fix)

```
tests/test_share_device_writes.py::TestDeviceBearerChatMessages::test_device_cannot_post_to_non_member_channel PASSED
tests/test_share_device_writes.py::TestDeviceBearerChatMessages::test_device_can_post_to_member_channel PASSED
tests/test_share_device_writes.py::TestDeviceBearerLibraryIngest::test_device_can_ingest_url PASSED
tests/test_share_device_writes.py::TestDeviceBearerLibraryIngest::test_device_cannot_ingest_without_token PASSED
tests/test_share_device_writes.py::TestShareDestinations::test_destinations_include_library PASSED
tests/test_share_device_writes.py::TestShareDestinations::test_destinations_exclude_other_user_projects PASSED
tests/test_share_device_writes.py::TestShareDestinations::test_destinations_include_owned_projects PASSED
tests/test_share_device_writes.py::TestDeviceBearerProjectFiles::test_device_can_upload_to_owned_project PASSED
tests/test_share_device_writes.py::TestDeviceBearerProjectFiles::test_device_cannot_upload_to_other_project PASSED
tests/test_share_device_writes.py::TestDeviceBearerProjectFiles::test_device_can_upload_when_member_with_write PASSED
tests/test_routes_project_files_agent_scope.py::TestSessionFilesUnchanged::test_owner_session_list_allowed PASSED
tests/test_routes_project_files_agent_scope.py::TestSessionFilesUnchanged::test_owner_session_upload_allowed PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesWrite::test_mkdir_allowed_with_files_write PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesWrite::test_upload_without_write_scope_is_403 PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesWrite::test_upload_allowed_with_files_write PASSED
tests/test_routes_project_files_agent_scope.py::TestGrantRevocationCutsAccess::test_revoke_route_stops_the_next_files_read PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesRead::test_stats_allowed_with_files_read PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesRead::test_list_allowed_with_files_read PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesRead::test_list_without_read_scope_is_403 PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesProjectBinding::test_unknown_slug_is_404 PASSED
tests/test_routes_project_files_agent_scope.py::TestAgentFilesProjectBinding::test_token_for_other_project_is_404 PASSED
==================== 21 passed in 53.40s =====================
```
