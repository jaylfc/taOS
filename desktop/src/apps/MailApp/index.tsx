import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ChevronDown,
  CornerUpLeft,
  CornerUpRight,
  FileText,
  Forward,
  Inbox,
  Mail,
  MoreHorizontal,
  Paperclip,
  Plus,
  Search,
  Send,
  Share2,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import {
  addAccount,
  deleteAccount,
  fetchAccounts,
  fetchFolders,
  fetchMessage,
  fetchMessages,
  sendMessage,
  type MailAccount,
  type MailDetail,
  type MailEnvelope,
  type NewAccount,
} from "@/lib/mail";
import styles from "./MailApp.module.css";

/* ------------------------------------------------------------------ */
/*  Constants + helpers                                                */
/* ------------------------------------------------------------------ */

type Filter = "all" | "unread" | "flagged";

// Canonical folders shown for every account. The IMAP server's own folder list
// (from fetchFolders) is also surfaced, but these five are always present and
// map to the common special-use mailboxes.
const CANONICAL_FOLDERS: { id: string; label: string }[] = [
  { id: "INBOX", label: "Inbox" },
  { id: "Sent", label: "Sent" },
  { id: "Drafts", label: "Drafts" },
  { id: "Archive", label: "Archive" },
  { id: "Trash", label: "Trash" },
];

const FOLDER_ICON: Record<string, typeof Inbox> = {
  INBOX: Inbox,
  Sent: Send,
  Drafts: FileText,
  Archive: Archive,
  Trash: Trash2,
};

function initials(name: string, addr: string): string {
  const src = (name || addr || "?").trim();
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase();
  }
  return src.slice(0, 2).toUpperCase();
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function MailApp({ windowId: _windowId }: { windowId: string }) {
  const isMobile = useIsMobile();

  const [accounts, setAccounts] = useState<MailAccount[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [activeAccountId, setActiveAccountId] = useState<string | null>(null);
  const [acctMenuOpen, setAcctMenuOpen] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);

  const [serverFolders, setServerFolders] = useState<string[]>([]);
  const [activeFolder, setActiveFolder] = useState("INBOX");

  const [messages, setMessages] = useState<MailEnvelope[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [detail, setDetail] = useState<MailDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [composeOpen, setComposeOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [mobileReading, setMobileReading] = useState(false);

  const activeAccount = useMemo(
    () => accounts.find((a) => a.id === activeAccountId) ?? null,
    [accounts, activeAccountId],
  );

  /* ---- load accounts ---- */
  const loadAccounts = useCallback(async () => {
    try {
      const list = await fetchAccounts();
      setAccounts(list);
      setActiveAccountId((prev) => prev ?? (list[0]?.id ?? null));
      if (list.length === 0) setShowAddForm(true);
    } catch {
      setAccounts([]);
    } finally {
      setAccountsLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  /* ---- load folders + messages for the active account/folder ---- */
  useEffect(() => {
    if (!activeAccountId) return;
    let cancelled = false;
    void fetchFolders(activeAccountId)
      .then((f) => {
        if (!cancelled) setServerFolders(f);
      })
      .catch(() => {
        if (!cancelled) setServerFolders([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeAccountId]);

  const loadMessages = useCallback(async () => {
    if (!activeAccountId) return;
    setMessagesLoading(true);
    setListError(null);
    try {
      const list = await fetchMessages(activeAccountId, activeFolder);
      setMessages(list);
    } catch (e) {
      setMessages([]);
      setListError(e instanceof Error ? e.message : "Failed to load mail");
    } finally {
      setMessagesLoading(false);
    }
  }, [activeAccountId, activeFolder]);

  useEffect(() => {
    setSelectedUid(null);
    setDetail(null);
    void loadMessages();
  }, [loadMessages]);

  /* ---- load a single message ---- */
  const openMessage = useCallback(
    async (uid: string) => {
      if (!activeAccountId) return;
      setSelectedUid(uid);
      setShareOpen(false);
      if (isMobile) setMobileReading(true);
      setDetailLoading(true);
      setDetail(null);
      try {
        const d = await fetchMessage(activeAccountId, uid, activeFolder);
        setDetail(d);
      } catch {
        setDetail(null);
      } finally {
        setDetailLoading(false);
      }
    },
    [activeAccountId, activeFolder, isMobile],
  );

  /* ---- filtered list ---- */
  const visibleMessages = useMemo(() => {
    const q = search.trim().toLowerCase();
    return messages.filter((m) => {
      if (filter === "unread" && !m.unread) return false;
      if (filter === "flagged" && !m.flagged) return false;
      if (!q) return true;
      return (
        m.from_name.toLowerCase().includes(q) ||
        m.from_addr.toLowerCase().includes(q) ||
        m.subject.toLowerCase().includes(q) ||
        m.snippet.toLowerCase().includes(q)
      );
    });
  }, [messages, search, filter]);

  const unreadCount = messages.filter((m) => m.unread).length;
  const flaggedCount = messages.filter((m) => m.flagged).length;
  const activeFolderLabel =
    CANONICAL_FOLDERS.find((f) => f.id === activeFolder)?.label ?? activeFolder;

  /* ---- compose / send ---- */
  const handleSent = useCallback(() => {
    setComposeOpen(false);
    void loadMessages();
  }, [loadMessages]);

  if (!accountsLoaded) {
    return (
      <div className={styles.root}>
        <div className={styles.empty}>
          <Mail size={36} aria-hidden="true" />
          <p>Loading mail…</p>
        </div>
      </div>
    );
  }

  if (showAddForm || accounts.length === 0) {
    return (
      <div className={styles.root}>
        <div className={styles.appbar}>
          <Mail size={16} aria-hidden="true" />
          <h1>Mail</h1>
        </div>
        <AddAccountForm
          onCancel={accounts.length > 0 ? () => setShowAddForm(false) : undefined}
          onAdded={async () => {
            setShowAddForm(false);
            setActiveAccountId(null);
            await loadAccounts();
          }}
        />
      </div>
    );
  }

  const rootClass = `${styles.root} ${isMobile && mobileReading ? styles.mobileReading : ""}`;

  return (
    <div className={rootClass}>
      <div className={styles.appbar}>
        <Mail size={16} aria-hidden="true" />
        <h1>Mail</h1>
        <div className={styles.appbarRight}>
          <button
            className={styles.composebtn}
            onClick={() => setComposeOpen(true)}
            aria-label="Compose new message"
          >
            <Plus size={14} aria-hidden="true" />
            Compose
          </button>
        </div>
      </div>

      <div className={styles.body}>
        {/* ---- sidebar ---- */}
        <aside className={styles.sidebar} aria-label="Mailboxes">
          <div className={`${styles.acct} ${acctMenuOpen ? styles.open : ""}`}>
            <button
              className={styles.acctCur}
              onClick={() => setAcctMenuOpen((v) => !v)}
              aria-expanded={acctMenuOpen}
              aria-label="Switch account"
            >
              <div className={`${styles.av} ${styles.avUser}`}>
                {initials(activeAccount?.display_name ?? "", activeAccount?.email_address ?? "")}
              </div>
              <div className={styles.acctBody}>
                <div className={styles.acctName}>{activeAccount?.display_name}</div>
                <div className={styles.acctAddr}>{activeAccount?.email_address}</div>
              </div>
              <span className={styles.acctChev}>
                <ChevronDown size={15} aria-hidden="true" />
              </span>
            </button>

            {acctMenuOpen && (
              <div className={styles.acctMenu}>
                <div className={styles.acctGrp}>Your accounts</div>
                {accounts.map((a) => (
                  <button
                    key={a.id}
                    className={`${styles.acctRow} ${a.id === activeAccountId ? styles.sel : ""}`}
                    onClick={() => {
                      setActiveAccountId(a.id);
                      setActiveFolder("INBOX");
                      setAcctMenuOpen(false);
                    }}
                  >
                    <div className={`${styles.av} ${styles.avUser}`}>
                      {initials(a.display_name, a.email_address)}
                    </div>
                    <div className={styles.acctRowBody}>
                      <div className={styles.acctRowName}>{a.display_name}</div>
                      <div className={styles.acctRowAddr}>{a.email_address}</div>
                    </div>
                    {a.id === activeAccountId && (
                      <span className={styles.acctCheck}>
                        <Star size={13} aria-hidden="true" />
                      </span>
                    )}
                  </button>
                ))}

                {/* Phase-2 affordance: agent accounts + send-as. Disabled. */}
                <div className={styles.acctGrp}>Agent accounts</div>
                <div className={styles.acctGroupDisabled}>
                  <div className={styles.acctRow} aria-disabled="true">
                    <div className={`${styles.av} ${styles.avAgent}`}>A</div>
                    <div className={styles.acctRowBody}>
                      <div className={styles.acctRowName}>
                        Send as agent <span className={styles.pillAgent}>Agent</span>
                      </div>
                      <div className={styles.acctRowAddr}>Coming in a later release</div>
                    </div>
                    <span className={styles.comingSoon}>Soon</span>
                  </div>
                </div>

                <button
                  className={styles.acctAdd}
                  onClick={() => {
                    setAcctMenuOpen(false);
                    setShowAddForm(true);
                  }}
                >
                  <Plus size={14} aria-hidden="true" />
                  Add mail account
                </button>
                {activeAccount && (
                  <button
                    className={styles.acctAdd}
                    onClick={async () => {
                      if (!activeAccount) return;
                      await deleteAccount(activeAccount.id);
                      setActiveAccountId(null);
                      setAcctMenuOpen(false);
                      await loadAccounts();
                    }}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                    Remove this account
                  </button>
                )}
              </div>
            )}
          </div>

          <nav className={styles.folders} aria-label="Folders">
            {CANONICAL_FOLDERS.map((f) => {
              const Icon = FOLDER_ICON[f.id] ?? Inbox;
              return (
                <button
                  key={f.id}
                  className={`${styles.fold} ${activeFolder === f.id ? styles.on : ""}`}
                  onClick={() => setActiveFolder(f.id)}
                  aria-current={activeFolder === f.id}
                >
                  <span className={styles.foldIco}>
                    <Icon size={17} aria-hidden="true" />
                  </span>
                  <span className={styles.foldName}>{f.label}</span>
                  {f.id === "INBOX" && unreadCount > 0 && (
                    <span className={styles.foldCount}>{unreadCount}</span>
                  )}
                </button>
              );
            })}

            {serverFolders.filter(
              (sf) => !CANONICAL_FOLDERS.some((c) => c.id.toLowerCase() === sf.toLowerCase()),
            ).length > 0 && (
              <>
                <div className={styles.fgrpLbl}>All mailboxes</div>
                {serverFolders
                  .filter(
                    (sf) =>
                      !CANONICAL_FOLDERS.some((c) => c.id.toLowerCase() === sf.toLowerCase()),
                  )
                  .map((sf) => (
                    <button
                      key={sf}
                      className={`${styles.fold} ${activeFolder === sf ? styles.on : ""}`}
                      onClick={() => setActiveFolder(sf)}
                      aria-current={activeFolder === sf}
                    >
                      <span className={styles.foldIco}>
                        <Inbox size={17} aria-hidden="true" />
                      </span>
                      <span className={styles.foldName}>{sf}</span>
                    </button>
                  ))}
              </>
            )}
          </nav>
        </aside>

        {/* ---- message list ---- */}
        <section className={styles.list} aria-label="Message list">
          <div className={styles.listHead}>
            <div className={styles.listTitle}>
              <h2>{activeFolderLabel}</h2>
              <span className={styles.sub}>
                {unreadCount > 0 ? `${unreadCount} unread` : `${messages.length} messages`}
              </span>
            </div>
            <div className={styles.search}>
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                placeholder="Search mail"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label="Search mail"
              />
            </div>
            <div className={styles.tabs} role="tablist" aria-label="Filter messages">
              {(
                [
                  ["all", "All", messages.length],
                  ["unread", "Unread", unreadCount],
                  ["flagged", "Flagged", flaggedCount],
                ] as [Filter, string, number][]
              ).map(([id, label, n]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={filter === id}
                  className={`${styles.tab} ${filter === id ? styles.on : ""}`}
                  onClick={() => setFilter(id)}
                >
                  {label}
                  <span className={styles.n}>{n}</span>
                </button>
              ))}
            </div>
          </div>

          <div className={styles.rows} role="list">
            {messagesLoading ? (
              <>
                <div className={styles.skel} aria-hidden="true" />
                <div className={styles.skel} aria-hidden="true" />
                <div className={styles.skel} aria-hidden="true" />
                <span className="sr-only" role="status" aria-live="polite">
                  Loading messages
                </span>
              </>
            ) : listError ? (
              <div className={styles.error} role="alert">
                {listError}
              </div>
            ) : visibleMessages.length === 0 ? (
              <div className={styles.empty}>
                <Inbox size={32} aria-hidden="true" />
                <p>{search || filter !== "all" ? "No matching messages" : "No messages here"}</p>
              </div>
            ) : (
              visibleMessages.map((m) => (
                <button
                  key={m.uid}
                  role="listitem"
                  className={`${styles.row} ${m.uid === selectedUid ? styles.on : ""} ${m.unread ? styles.unread : ""}`}
                  onClick={() => void openMessage(m.uid)}
                >
                  <div className={`${styles.av} ${styles.avUser}`}>
                    {initials(m.from_name, m.from_addr)}
                  </div>
                  <div className={styles.rowMain}>
                    <div className={styles.rowTop}>
                      <span className={styles.rowFrom}>{m.from_name || m.from_addr}</span>
                      <span className={styles.rowTime}>{formatTime(m.date)}</span>
                    </div>
                    <div className={styles.rowSubj}>{m.subject || "(no subject)"}</div>
                    <div className={styles.rowSnip}>{m.snippet}</div>
                    {(m.unread || m.flagged || m.has_attachment) && (
                      <div className={styles.rowMeta}>
                        {m.unread && <span className={styles.udot} aria-label="Unread" />}
                        {m.flagged && (
                          <span className={`${styles.mini} ${styles.flag}`}>
                            <Star size={13} aria-label="Flagged" />
                          </span>
                        )}
                        {m.has_attachment && (
                          <span className={styles.mini}>
                            <Paperclip size={13} aria-label="Has attachment" />
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </button>
              ))
            )}
          </div>
        </section>

        {/* ---- reading pane ---- */}
        <section className={styles.read} aria-label="Message">
          <div className={styles.readToolbar}>
            {isMobile && (
              <button className={styles.mobileBack} onClick={() => setMobileReading(false)}>
                <CornerUpLeft size={15} aria-hidden="true" />
                Back
              </button>
            )}
            {/* TODO(phase-2): wire Reply / Reply all / Forward to a prefilled
                compose. Phase 1 ships the compose surface and send only. */}
            <button className={`${styles.tbtn} ${styles.pri}`} disabled={!detail}>
              <CornerUpLeft size={15} aria-hidden="true" />
              Reply
            </button>
            <button className={styles.tbtn} disabled={!detail}>
              <CornerUpRight size={15} aria-hidden="true" />
              Reply all
            </button>
            <button className={styles.tbtn} disabled={!detail}>
              <Forward size={15} aria-hidden="true" />
              Forward
            </button>
            <div className={styles.tbSep} />
            <button className={styles.iconbtn} title="Archive" disabled={!detail}>
              <Archive size={16} aria-hidden="true" />
            </button>
            <button className={styles.iconbtn} title="Delete" disabled={!detail}>
              <Trash2 size={16} aria-hidden="true" />
            </button>
            <div className={styles.right}>
              {/* Share / Send to: entry point only. Full share sheet is task #69. */}
              <button
                className={styles.iconbtn}
                title="Share / Send to"
                disabled={!detail}
                onClick={() => setShareOpen((v) => !v)}
                aria-expanded={shareOpen}
              >
                <Share2 size={16} aria-hidden="true" />
              </button>
              <button className={styles.iconbtn} title="More" disabled={!detail}>
                <MoreHorizontal size={16} aria-hidden="true" />
              </button>
              {shareOpen && (
                <div className={styles.menu} role="menu">
                  <div className={styles.menuNote}>Share / Send to</div>
                  <button className={styles.menuItem} role="menuitem" disabled>
                    <Share2 size={14} aria-hidden="true" />
                    Send to a person or agent
                  </button>
                  <div className={styles.menuNote}>Full share sheet coming soon</div>
                </div>
              )}
            </div>
          </div>

          {detailLoading ? (
            <div className={styles.empty}>
              <Mail size={32} aria-hidden="true" />
              <p>Loading message…</p>
            </div>
          ) : !detail ? (
            <div className={styles.empty}>
              <Mail size={36} aria-hidden="true" />
              <p>Select a message to read it here.</p>
            </div>
          ) : (
            <div className={styles.readScroll}>
              <div className={styles.readSubj}>{detail.subject || "(no subject)"}</div>
              <div className={styles.readFrom}>
                <div className={`${styles.av} ${styles.avUser}`}>
                  {initials(detail.from_name, detail.from_addr)}
                </div>
                <div className={styles.rfBody}>
                  <div className={styles.rfName}>{detail.from_name || detail.from_addr}</div>
                  <div className={styles.rfAddr}>{detail.from_addr}</div>
                  <div className={styles.rfTo}>
                    to <b>{detail.to}</b>
                    {detail.cc ? (
                      <>
                        , cc <b>{detail.cc}</b>
                      </>
                    ) : null}
                  </div>
                </div>
                <div className={styles.readDate}>{detail.date}</div>
              </div>

              <div className={styles.readBody}>{detail.body_text || detail.body_html}</div>

              {detail.attachments.length > 0 && (
                <div className={styles.attach}>
                  <div className={styles.attLbl}>
                    {detail.attachments.length} attachment
                    {detail.attachments.length === 1 ? "" : "s"}
                  </div>
                  {detail.attachments.map((a, i) => (
                    <div className={styles.att} key={`${a.filename}-${i}`}>
                      <div className={styles.attIco}>
                        <FileText size={17} aria-hidden="true" />
                      </div>
                      <div>
                        <div className={styles.attName}>{a.filename}</div>
                        <div className={styles.attSize}>{formatBytes(a.size)}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {composeOpen && activeAccount && (
        <ComposeOverlay
          account={activeAccount}
          onClose={() => setComposeOpen(false)}
          onSent={handleSent}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Compose overlay                                                    */
/* ------------------------------------------------------------------ */

function ComposeOverlay({
  account,
  onClose,
  onSent,
}: {
  account: MailAccount;
  onClose: () => void;
  onSent: () => void;
}) {
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [showCc, setShowCc] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async () => {
    if (!to.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      await sendMessage(account.id, { to, subject, body, cc });
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send");
      setSending(false);
    }
  }, [account.id, to, cc, subject, body, sending, onSent]);

  return (
    <div
      className={styles.scrim}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={styles.compose} role="dialog" aria-label="New message">
        <div className={styles.cmpHead}>
          <h3>New message</h3>
          <div className={styles.right}>
            <button className={styles.iconbtn} title="Close" onClick={onClose} aria-label="Close">
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className={styles.cmpFields}>
          {/* From: account selector is static in Phase 1 (one user account at a
              time). The agent send-as switcher is a Phase 2 affordance below. */}
          <div className={styles.cmpField}>
            <span className={styles.k}>From</span>
            <div className={styles.fromStatic}>
              <div className={`${styles.av} ${styles.avUser}`}>
                {initials(account.display_name, account.email_address)}
              </div>
              <div>
                <div className={styles.faName}>{account.display_name}</div>
                <div className={styles.faAddr}>{account.email_address}</div>
              </div>
            </div>
          </div>
          {/* Phase-2 affordance: send as an agent. Shown, not wired. */}
          <div className={styles.sendasHint}>
            <Mail size={13} aria-hidden="true" />
            Sending as an agent is coming in a later release
          </div>
          <div className={styles.cmpField}>
            <span className={styles.k}>To</span>
            <input
              placeholder="Recipients"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              aria-label="To"
            />
            {!showCc && (
              <button
                className={styles.menuItem}
                style={{ width: "auto", padding: "2px 6px" }}
                onClick={() => setShowCc(true)}
              >
                Cc
              </button>
            )}
          </div>
          {showCc && (
            <div className={styles.cmpField}>
              <span className={styles.k}>Cc</span>
              <input
                placeholder="Cc recipients"
                value={cc}
                onChange={(e) => setCc(e.target.value)}
                aria-label="Cc"
              />
            </div>
          )}
          <div className={styles.cmpField}>
            <span className={styles.k}>Subject</span>
            <input
              placeholder="Subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              aria-label="Subject"
            />
          </div>
        </div>

        <textarea
          className={styles.cmpBody}
          placeholder="Write your message…"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          aria-label="Message body"
        />

        {error && (
          <div className={styles.error} role="alert">
            {error}
          </div>
        )}

        <div className={styles.cmpBar}>
          <button
            className={styles.sendBtn}
            onClick={() => void send()}
            disabled={!to.trim() || sending}
          >
            <Send size={15} aria-hidden="true" />
            {sending ? "Sending…" : "Send"}
          </button>
          {/* TODO(phase-2): attachment upload pass-through in compose. */}
          <button className={styles.cmpTool} title="Attach file" disabled>
            <Paperclip size={17} aria-hidden="true" />
          </button>
          <div className={styles.spacer} />
          <button
            className={`${styles.cmpTool} ${styles.discard}`}
            title="Discard draft"
            onClick={onClose}
          >
            <Trash2 size={17} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Add-account form                                                   */
/* ------------------------------------------------------------------ */

function AddAccountForm({
  onAdded,
  onCancel,
}: {
  onAdded: () => void | Promise<void>;
  onCancel?: () => void;
}) {
  const [form, setForm] = useState<NewAccount>({
    display_name: "",
    email_address: "",
    imap_host: "",
    imap_port: 993,
    imap_security: "ssl",
    smtp_host: "",
    smtp_port: 587,
    smtp_security: "starttls",
    username: "",
    password: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof NewAccount, v: string | number) =>
    setForm((f) => ({ ...f, [k]: v }));

  const submit = useCallback(async () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await addAccount({
        ...form,
        username: form.username || form.email_address,
        display_name: form.display_name || form.email_address,
      });
      await onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add account");
      setSaving(false);
    }
  }, [form, saving, onAdded]);

  return (
    <div className={styles.readScroll} style={{ maxWidth: 520, margin: "0 auto", width: "100%" }}>
      <div className={styles.readSubj}>Add a mail account</div>
      <p className={styles.menuNote} style={{ padding: "8px 0 16px" }}>
        Your password is stored encrypted in the Secrets vault, never in the
        accounts table.
      </p>

      {error && (
        <div className={styles.error} role="alert" style={{ margin: "0 0 14px" }}>
          {error}
        </div>
      )}

      <Field label="Display name">
        <input
          value={form.display_name}
          onChange={(e) => set("display_name", e.target.value)}
          placeholder="Jay Lawrence"
        />
      </Field>
      <Field label="Email address">
        <input
          type="email"
          value={form.email_address}
          onChange={(e) => set("email_address", e.target.value)}
          placeholder="jay@example.com"
        />
      </Field>
      <Field label="Username">
        <input
          value={form.username}
          onChange={(e) => set("username", e.target.value)}
          placeholder="Defaults to the email address"
        />
      </Field>
      <Field label="Password">
        <input
          type="password"
          value={form.password}
          onChange={(e) => set("password", e.target.value)}
          placeholder="App password recommended"
        />
      </Field>
      <Field label="IMAP host">
        <input
          value={form.imap_host}
          onChange={(e) => set("imap_host", e.target.value)}
          placeholder="imap.example.com"
        />
      </Field>
      <Field label="IMAP port">
        <input
          type="number"
          value={form.imap_port}
          onChange={(e) => set("imap_port", Number(e.target.value))}
        />
      </Field>
      <Field label="IMAP security">
        <SecuritySelect value={form.imap_security} onChange={(v) => set("imap_security", v)} />
      </Field>
      <Field label="SMTP host">
        <input
          value={form.smtp_host}
          onChange={(e) => set("smtp_host", e.target.value)}
          placeholder="smtp.example.com"
        />
      </Field>
      <Field label="SMTP port">
        <input
          type="number"
          value={form.smtp_port}
          onChange={(e) => set("smtp_port", Number(e.target.value))}
        />
      </Field>
      <Field label="SMTP security">
        <SecuritySelect value={form.smtp_security} onChange={(v) => set("smtp_security", v)} />
      </Field>

      <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
        <button
          className={styles.sendBtn}
          onClick={() => void submit()}
          disabled={saving || !form.email_address || !form.imap_host || !form.smtp_host || !form.password}
        >
          <Plus size={15} aria-hidden="true" />
          {saving ? "Adding…" : "Add account"}
        </button>
        {onCancel && (
          <button className={styles.tbtn} onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label
      style={{ display: "block", marginBottom: 10 }}
      className={styles.search}
    >
      <span
        style={{
          display: "block",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 2,
        }}
        className={styles.faAddr}
      >
        {label}
      </span>
      {children}
    </label>
  );
}

function SecuritySelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        flex: 1,
        background: "none",
        border: "none",
        outline: "none",
        color: "inherit",
        font: "inherit",
        fontSize: 13,
      }}
    >
      <option value="ssl">SSL / TLS</option>
      <option value="starttls">STARTTLS</option>
      <option value="none">None</option>
    </select>
  );
}
