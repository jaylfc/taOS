/**
 * LiveBrowserView — thin presentational component for a live Neko browser session.
 *
 * Token convention: the stream token is appended as a URL fragment:
 *   <nekoUrl>#token=<streamToken>
 * The Neko room page reads window.location.hash on load and uses the token to
 * authenticate the WebRTC/WebSocket connection. Fragment params never reach the
 * server in HTTP logs, which keeps the token out of access logs.
 */

interface LiveBrowserViewProps {
  nekoUrl: string;
  streamToken: string;
}

export function LiveBrowserView({ nekoUrl, streamToken }: LiveBrowserViewProps) {
  // Append the token as a fragment so the Neko room can read it client-side.
  const src = `${nekoUrl}#token=${streamToken}`;

  return (
    <iframe
      title="Full browser"
      src={src}
      sandbox="allow-scripts allow-same-origin allow-forms"
      style={{ width: "100%", height: "100%", borderStyle: "none", borderWidth: 0, display: "block" }}
    />
  );
}
