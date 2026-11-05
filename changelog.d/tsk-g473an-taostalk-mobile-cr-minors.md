### Fixed
- taOS Talk mobile: member count wording, sendMessage guard for failed attachments, hook order, and missing tests
- taOS Talk mobile composer: pass the archived-agent roster into describeChannelParty so archived DMs resolve identically in composer and header
- taOS Talk mobile channel-party: match channel.settings.archived_agent_id against the archived roster id before the slug/name fallback
- taOS Talk mobile: clear sendError after a successful attachment send so a stale failure does not remain on screen
- taOS Talk mobile header: render mobileParty.subtitle for agent parties so labels such as "Archived agent" show
