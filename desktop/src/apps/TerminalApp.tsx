import { useEffect, useRef, useState, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import {
  Button,
  Card,
  CardContent,
  Input,
  Label,
  Toolbar,
  ToolbarGroup,
  ToolbarSpacer,
} from "@/components/ui";

type Mode = "local" | "ssh";

interface SshHost {
  host: string;
  port: number;
  username: string;
}

interface Session {
  mode: Mode;
  host?: string;
  port?: number;
  username?: string;
  password?: string;
}

const RECENT_KEY = "tinyagentos.terminal.recentSsh";

function loadRecent(): SshHost[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, 8) : [];
  } catch {
    return [];
  }
}

function saveRecent(entry: SshHost) {
  try {
    const current = loadRecent().filter(
      (h) =>
        !(
          h.host === entry.host &&
          h.port === entry.port &&
          h.username === entry.username
        ),
    );
    current.unshift(entry);
    localStorage.setItem(RECENT_KEY, JSON.stringify(current.slice(0, 8)));
  } catch {
    // ignore
  }
}

interface ShortcutProp {
  wsUrl: string;
  ticket: string;
}

export function TerminalApp({ windowId: _windowId, shortcut }: { windowId?: string; shortcut?: ShortcutProp }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  const [session, setSession] = useState<Session | null>(null);
  const [view, setView] = useState<"picker" | "ssh-form" | "terminal">(
    shortcut ? "terminal" : "picker",
  );
  const [recent, setRecent] = useState<SshHost[]>(() => loadRecent());

  // SSH form state
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    }
    if (termRef.current) {
      termRef.current.dispose();
      termRef.current = null;
    }
    fitRef.current = null;
    setSession(null);
    setView("picker");
  }, []);

  const startLocal = () => {
    setSession({ mode: "local" });
    setView("terminal");
  };

  const openSshForm = (prefill?: SshHost) => {
    if (prefill) {
      setHost(prefill.host);
      setPort(String(prefill.port));
      setUsername(prefill.username);
      setPassword("");
    } else {
      setHost("");
      setPort("22");
      setUsername("");
      setPassword("");
    }
    setView("ssh-form");
  };

  const submitSsh = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedHost = host.trim();
    const trimmedUser = username.trim();
    if (!trimmedHost || !trimmedUser) return;
    const p = parseInt(port, 10) || 22;
    saveRecent({ host: trimmedHost, port: p, username: trimmedUser });
    setRecent(loadRecent());
    setSession({
      mode: "ssh",
      host: trimmedHost,
      port: p,
      username: trimmedUser,
      password,
    });
    setView("terminal");
  };

  // Shortcut mode: connect immediately without a session
  useEffect(() => {
    if (!shortcut) return;
    if (termRef.current) return;

    const wsUrl = shortcut.wsUrl;
    const ticket = shortcut.ticket;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "ticket", ticket }));
    };

    // If the container is available and ResizeObserver is supported, set up xterm too
    if (containerRef.current && typeof ResizeObserver !== "undefined") {
      const term = new Terminal({
        theme: {
          background: "#151625",
          foreground: "rgba(255, 255, 255, 0.85)",
          cursor: "#8b92a3",
          cursorAccent: "#151625",
          selectionBackground: "rgba(139, 146, 163, 0.3)",
          black: "#1a1b2e",
          red: "#ff5f57",
          green: "#28c840",
          yellow: "#febc2e",
          blue: "#8b92a3",
          magenta: "#f093fb",
          cyan: "#4facfe",
          white: "rgba(255,255,255,0.85)",
          brightBlack: "#555",
          brightRed: "#ff6b6b",
          brightGreen: "#51cf66",
          brightYellow: "#ffd43b",
          brightBlue: "#748ffc",
          brightMagenta: "#e599f7",
          brightCyan: "#66d9e8",
          brightWhite: "#ffffff",
        },
        fontFamily:
          "'JetBrains Mono', 'Fira Code', 'MesloLGS NF', 'Hack Nerd Font', 'Cascadia Code', 'SF Mono', monospace",
        fontSize: 14,
        lineHeight: 1.2,
        cursorBlink: true,
        cursorStyle: "bar",
        allowProposedApi: true,
      });
      const fit = new FitAddon();
      const webLinks = new WebLinksAddon();
      term.loadAddon(fit);
      term.loadAddon(webLinks);
      term.open(containerRef.current);
      fit.fit();
      fitRef.current = fit;
      termRef.current = term;

      ws.onmessage = (event) => { term.write(event.data); };
      ws.onerror = () => { term.writeln("\r\n\x1b[31mWebSocket connection error\x1b[0m"); };
      ws.onclose = () => { term.writeln("\r\n\x1b[33mConnection closed\x1b[0m"); };

      const inputDisposable = term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(data);
      });
      const resizeObserver = new ResizeObserver(() => {
        try { fit.fit(); } catch { /* ignore */ }
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
        }
      });
      resizeObserver.observe(containerRef.current);

      return () => {
        resizeObserver.disconnect();
        inputDisposable.dispose();
        try { ws.close(); } catch { /* ignore */ }
        term.dispose();
        termRef.current = null;
        wsRef.current = null;
        fitRef.current = null;
      };
    }

    return () => {
      try { ws.close(); } catch { /* ignore */ }
      wsRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (view !== "terminal" || !session) return;
    if (!containerRef.current || termRef.current) return;

    const term = new Terminal({
      theme: {
        background: "#151625",
        foreground: "rgba(255, 255, 255, 0.85)",
        cursor: "#8b92a3",
        cursorAccent: "#151625",
        selectionBackground: "rgba(139, 146, 163, 0.3)",
        black: "#1a1b2e",
        red: "#ff5f57",
        green: "#28c840",
        yellow: "#febc2e",
        blue: "#8b92a3",
        magenta: "#f093fb",
        cyan: "#4facfe",
        white: "rgba(255,255,255,0.85)",
        brightBlack: "#555",
        brightRed: "#ff6b6b",
        brightGreen: "#51cf66",
        brightYellow: "#ffd43b",
        brightBlue: "#748ffc",
        brightMagenta: "#e599f7",
        brightCyan: "#66d9e8",
        brightWhite: "#ffffff",
      },
      fontFamily:
        "'JetBrains Mono', 'Fira Code', 'MesloLGS NF', 'Hack Nerd Font', 'Cascadia Code', 'SF Mono', monospace",
      fontSize: 14,
      lineHeight: 1.2,
      cursorBlink: true,
      cursorStyle: "bar",
      allowProposedApi: true,
    });

    const fit = new FitAddon();
    const webLinks = new WebLinksAddon();
    term.loadAddon(fit);
    term.loadAddon(webLinks);

    term.open(containerRef.current);
    fit.fit();
    fitRef.current = fit;
    termRef.current = term;

    if (session.mode === "ssh") {
      term.writeln(
        `\x1b[36mConnecting to ${session.username}@${session.host}:${session.port}...\x1b[0m`,
      );
    }

    // Connect WebSocket to /ws/terminal
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/terminal`);
    wsRef.current = ws;

    ws.onopen = () => {
      // First message: connect config
      ws.send(
        JSON.stringify({
          type: "connect",
          mode: session.mode,
          host: session.host,
          port: session.port,
          username: session.username,
          password: session.password,
        }),
      );
      // Then send initial size
      ws.send(
        JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }),
      );
    };

    ws.onmessage = (event) => {
      term.write(event.data);
    };

    ws.onerror = () => {
      term.writeln("\r\n\x1b[31mWebSocket connection error\x1b[0m");
    };

    ws.onclose = () => {
      term.writeln("\r\n\x1b[33mConnection closed\x1b[0m");
    };

    // Forward terminal input to WebSocket
    const inputDisposable = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    // Handle resize
    const resizeObserver = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // ignore
      }
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }),
        );
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      inputDisposable.dispose();
      try {
        ws.close();
      } catch {
        // ignore
      }
      term.dispose();
      termRef.current = null;
      wsRef.current = null;
      fitRef.current = null;
    };
  }, [view, session]);

  // ---------- Picker UI ----------
  if (view === "picker") {
    return (
      <div
        className="h-full w-full overflow-auto p-6"
        style={{ backgroundColor: "#151625", color: "rgba(255,255,255,0.85)" }}
      >
        <div className="mx-auto max-w-xl">
          <h2 className="mb-1 text-xl font-semibold">Terminal</h2>
          <p className="mb-5 text-sm opacity-70">
            Choose a connection to start a new session.
          </p>

          <div className="mb-6 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Card
              role="button"
              tabIndex={0}
              onClick={startLocal}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  startLocal();
                }
              }}
              className="cursor-pointer transition hover:border-[#8b92a3]/60"
              aria-label="Local Shell"
            >
              <CardContent className="p-4">
                <div className="text-base font-medium">Local Shell</div>
                <div className="mt-1 text-xs opacity-60">
                  Spawn a shell on this machine
                </div>
              </CardContent>
            </Card>
            <Card
              role="button"
              tabIndex={0}
              onClick={() => openSshForm()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  openSshForm();
                }
              }}
              className="cursor-pointer transition hover:border-[#8b92a3]/60"
              aria-label="SSH Connection"
            >
              <CardContent className="p-4">
                <div className="text-base font-medium">SSH Connection</div>
                <div className="mt-1 text-xs opacity-60">
                  Connect to a remote host
                </div>
              </CardContent>
            </Card>
          </div>

          {recent.length > 0 && (
            <div>
              <div className="mb-2 text-xs uppercase tracking-wider opacity-60">
                Recent SSH hosts
              </div>
              <ul className="space-y-2">
                {recent.map((h) => (
                  <li key={`${h.username}@${h.host}:${h.port}`}>
                    <Card
                      role="button"
                      tabIndex={0}
                      onClick={() => openSshForm(h)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          openSshForm(h);
                        }
                      }}
                      className="cursor-pointer transition hover:border-[#8b92a3]/60"
                      aria-label={`Connect to ${h.username}@${h.host}:${h.port}`}
                    >
                      <CardContent className="px-3 py-2 text-sm">
                        <span className="font-mono">
                          {h.username}@{h.host}
                        </span>
                        <span className="ml-2 opacity-50">:{h.port}</span>
                      </CardContent>
                    </Card>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ---------- SSH form ----------
  if (view === "ssh-form") {
    return (
      <div
        className="h-full w-full overflow-auto p-6"
        style={{ backgroundColor: "#151625", color: "rgba(255,255,255,0.85)" }}
      >
        <form onSubmit={submitSsh} className="mx-auto max-w-md space-y-3">
          <h2 className="mb-4 text-xl font-semibold">SSH Connection</h2>

          <div>
            <Label htmlFor="ssh-host">Host</Label>
            <Input
              id="ssh-host"
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="192.168.1.100"
              autoFocus
              required
              className="font-mono"
            />
          </div>

          <div>
            <Label htmlFor="ssh-port">Port</Label>
            <Input
              id="ssh-port"
              type="number"
              value={port}
              onChange={(e) => setPort(e.target.value)}
              min={1}
              max={65535}
              className="font-mono"
            />
          </div>

          <div>
            <Label htmlFor="ssh-username">Username</Label>
            <Input
              id="ssh-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="root"
              required
              className="font-mono"
            />
          </div>

          <div>
            <Label htmlFor="ssh-password">
              Password{" "}
              <span className="opacity-60 font-normal normal-case">
                (optional — leave blank for key-based auth)
              </span>
            </Label>
            <Input
              id="ssh-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="font-mono"
            />
          </div>

          <div className="flex gap-2 pt-1">
            <Button type="submit" disabled={!host || !username}>
              Connect
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => setView("picker")}
            >
              Cancel
            </Button>
          </div>

          <p className="mt-4 text-xs opacity-50">
            Password auth requires <code className="font-mono">sshpass</code>{" "}
            on the host. Leave the password blank to use SSH keys.
          </p>
        </form>
      </div>
    );
  }

  // ---------- Terminal view ----------
  return (
    <div
      className="flex h-full w-full flex-col"
      style={{ backgroundColor: "#151625" }}
    >
      <Toolbar className="text-xs" style={{ color: "rgba(255,255,255,0.7)" }}>
        <ToolbarGroup>
          <div className="font-mono px-1">
            {shortcut ? (
              <span>Connecting to shortcut…</span>
            ) : session?.mode === "ssh" ? (
              <>
                <span className="opacity-60">ssh://</span>
                {session.username}@{session.host}
                <span className="opacity-60">:{session.port}</span>
              </>
            ) : (
              <span>Local shell</span>
            )}
          </div>
        </ToolbarGroup>
        <ToolbarSpacer />
        <ToolbarGroup>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={disconnect}
          >
            Disconnect
          </Button>
        </ToolbarGroup>
      </Toolbar>
      <div ref={containerRef} className="min-h-0 flex-1" />
    </div>
  );
}

export default TerminalApp;
