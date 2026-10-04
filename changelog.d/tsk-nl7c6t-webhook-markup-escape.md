### Fixed

- Escape Telegram legacy-Markdown and Slack mrkdwn special characters in `WebhookNotifier` notification text to prevent link injection via user-controlled fields such as project names or agent handles.
