### Changed

- Setting a project's lead (`set_lead`) now moves all three lead fields together:
  `project_members.role = 'lead'`, `project_members.is_lead = 1`, and
  `projects.lead_member_id` are set in one place on promote, the previous lead's
  `is_lead` flag is cleared, and clearing the lead resets the flags so the pointer,
  the flag, and the role label can no longer disagree.