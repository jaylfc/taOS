### Fixed
- taOS Talk mobile: member count wording, sendMessage guard for failed attachments, hook order, and missing tests
- taOS Talk mobile composer: pass the archived-agent roster into describeChannelParty so archived DMs resolve identically in composer and header
- taOS Talk mobile channel-party: match channel.settings.archived_agent_id against the archived roster id before the slug/name fallback
- taOS Talk mobile: clear sendError after a successful attachment send so a stale failure does not remain on screen
- taOS Talk mobile header: render mobileParty.subtitle for agent parties so labels such as "Archived agent" show
- taOS Talk mobile: send WebSocket leave frame when deselecting a channel so the server releases the socket join
- taOS Talk mobile: clear backedOut guard on sidebar channel select and consider archived channels in default selection
- taOS Talk pinned messages popover: cap max-height with min(400px, 60dvh) so Jump to buttons stay visible on short screens
