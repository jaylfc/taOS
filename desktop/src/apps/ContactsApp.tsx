import { useState } from "react";
import { Plus, Search, Trash2, User, X, Edit, Mail, Phone, FileText, ChevronRight } from "lucide-react";
import { Button, Input, Textarea, Label, Card, CardContent } from "@/components/ui";
import { MobileSplitView } from "@/components/mobile/MobileSplitView";
import { useIsMobile } from "@/hooks/use-is-mobile";

interface Contact {
  id: string;
  name: string;
  email: string;
  phone: string;
  notes: string;
}

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

const emptyContact = (): Contact => ({
  id: newId(),
  name: "",
  email: "",
  phone: "",
  notes: "",
});

/* ------------------------------------------------------------------ */
/*  Avatar — glossy gradient identity tile                            */
/* ------------------------------------------------------------------ */

/** Stable hue (0-359) hashed from an arbitrary string. */
function hueFromString(input: string): number {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    hash = (hash * 31 + input.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 360;
}

/** Up to two initials from a contact name. */
function initialsFor(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return (parts[0] ?? "").slice(0, 2).toUpperCase();
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** A contact-identity tile: a glossy gradient square tinted with a stable
 *  per-contact hue (genuinely-semantic — identity colour, like AgentRow's
 *  per-agent colour), holding the contact's initials. Falls back to a neutral
 *  person glyph when there is no name yet. */
function ContactAvatar({ name, size = 36 }: { name: string; size?: number }) {
  const initials = initialsFor(name);
  const hue = hueFromString(name || "?");
  const fontPx = Math.round(size * 0.4);
  return (
    <span
      className="relative inline-flex items-center justify-center rounded-xl border border-shell-border shrink-0 overflow-hidden font-semibold text-white"
      style={{
        width: size,
        height: size,
        fontSize: fontPx,
        background: `linear-gradient(150deg, hsl(${hue} 62% 56%), hsl(${(hue + 28) % 360} 58% 44%))`,
      }}
      aria-hidden="true"
    >
      {/* Glossy top sheen */}
      <span
        className="absolute inset-x-0 top-0 h-1/2"
        style={{ background: "linear-gradient(180deg, rgba(255,255,255,0.22), transparent)" }}
      />
      {initials ? (
        <span className="relative leading-none">{initials}</span>
      ) : (
        <User size={Math.round(size * 0.5)} className="relative text-white/85" />
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Contact form — bottom sheet on mobile, modal on desktop            */
/* ------------------------------------------------------------------ */

function ContactForm({
  editing,
  isNew,
  onChange,
  onSave,
  onCancel,
}: {
  editing: Contact;
  isNew: boolean;
  onChange: (c: Contact) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const isMobile = useIsMobile();

  return (
    <div
      className={
        isMobile
          ? "absolute inset-0 z-50 flex items-end bg-black/50 backdrop-blur-sm"
          : "absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      }
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={isNew ? "Add contact" : "Edit contact"}
    >
      <Card
        className={
          isMobile
            ? "w-full max-h-[92%] overflow-y-auto bg-shell-surface border-shell-border shadow-2xl"
            : "w-full max-w-md max-h-full overflow-y-auto bg-shell-surface border-shell-border shadow-2xl"
        }
        style={isMobile ? { borderRadius: "20px 20px 0 0" } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        <CardContent className="p-5 space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <ContactAvatar name={editing.name} size={32} />
              <h2 className="text-sm font-semibold text-shell-text">{isNew ? "New Contact" : "Edit Contact"}</h2>
            </div>
            <Button variant="ghost" size="icon" onClick={onCancel} aria-label="Close form" className="h-7 w-7">
              <X size={16} />
            </Button>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="contact-name">Name</Label>
            <Input
              id="contact-name"
              value={editing.name}
              onChange={(e) => onChange({ ...editing, name: e.target.value })}
              placeholder="Full name"
              aria-label="Name"
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="contact-email">Email</Label>
            <Input
              id="contact-email"
              value={editing.email}
              onChange={(e) => onChange({ ...editing, email: e.target.value })}
              placeholder="email@example.com"
              aria-label="Email"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="contact-phone">Phone</Label>
            <Input
              id="contact-phone"
              value={editing.phone}
              onChange={(e) => onChange({ ...editing, phone: e.target.value })}
              placeholder="+1 234 567 890"
              aria-label="Phone"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="contact-notes">Notes</Label>
            <Textarea
              id="contact-notes"
              className="h-24"
              value={editing.notes}
              onChange={(e) => onChange({ ...editing, notes: e.target.value })}
              placeholder="Notes…"
              aria-label="Notes"
            />
          </div>

          <div className="flex gap-2 pt-1">
            <Button onClick={onSave} disabled={!editing.name.trim()}>
              Save
            </Button>
            <Button variant="secondary" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Contact detail pane                                                */
/* ------------------------------------------------------------------ */

/** One labelled detail field rendered as a card row with a leading icon. */
function DetailField({ icon, label, value, mono }: { icon: React.ReactNode; label: string; value: string; mono?: boolean }) {
  return (
    <Card className="rounded-xl border-shell-border bg-shell-surface p-3">
      <CardContent className="p-0 flex items-start gap-3">
        <span className="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-lg bg-shell-surface-active text-shell-text-secondary shrink-0">
          {icon}
        </span>
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-0.5">{label}</div>
          <div className={`text-sm text-shell-text break-words ${mono ? "font-mono tabular-nums" : ""}`}>{value}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function ContactDetail({
  contact,
  onEdit,
  onDelete,
}: {
  contact: Contact;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const isMobile = useIsMobile();

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Header — desktop only; mobile nav bar from MobileSplitView shows the name */}
      {!isMobile && (
        <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-shell-border shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <ContactAvatar name={contact.name} size={40} />
            <h2 className="text-sm font-semibold text-shell-text truncate">{contact.name}</h2>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <Button size="sm" variant="outline" onClick={onEdit} aria-label={`Edit ${contact.name}`}>
              <Edit size={13} />
              Edit
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={onDelete}
              className="hover:bg-red-500/15 hover:text-red-400"
              aria-label={`Delete ${contact.name}`}
            >
              <Trash2 size={13} />
              Delete
            </Button>
          </div>
        </div>
      )}

      {/* Mobile: avatar + action row */}
      {isMobile && (
        <div className="shrink-0 px-4 py-3 border-b border-shell-border">
          <div className="flex items-center gap-3 mb-3 min-w-0">
            <ContactAvatar name={contact.name} size={48} />
            <span className="text-base font-semibold text-shell-text truncate">{contact.name}</span>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={onEdit} className="flex-1" aria-label={`Edit ${contact.name}`}>
              <Edit size={13} />
              Edit
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={onDelete}
              className="flex-1 hover:bg-red-500/15 hover:text-red-400"
              aria-label={`Delete ${contact.name}`}
            >
              <Trash2 size={13} />
              Delete
            </Button>
          </div>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {contact.email && <DetailField icon={<Mail size={14} />} label="Email" value={contact.email} />}
        {contact.phone && <DetailField icon={<Phone size={14} />} label="Phone" value={contact.phone} mono />}
        {contact.notes && (
          <Card className="rounded-xl border-shell-border bg-shell-surface p-3">
            <CardContent className="p-0 flex items-start gap-3">
              <span className="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-lg bg-shell-surface-active text-shell-text-secondary shrink-0">
                <FileText size={14} />
              </span>
              <div className="min-w-0">
                <div className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mb-0.5">Notes</div>
                <div className="text-sm text-shell-text whitespace-pre-wrap break-words">{contact.notes}</div>
              </div>
            </CardContent>
          </Card>
        )}
        {!contact.email && !contact.phone && !contact.notes && (
          <p className="text-xs text-shell-text-tertiary italic px-1">No details recorded</p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Contact list row — shared card treatment                          */
/* ------------------------------------------------------------------ */

function ContactRow({
  contact,
  selected,
  onSelect,
  showChevron,
}: {
  contact: Contact;
  selected: boolean;
  onSelect: () => void;
  showChevron?: boolean;
}) {
  const cardCls = [
    "group w-full flex items-center gap-3 rounded-xl px-3 py-2.5 text-left",
    "border transition-[background-color,box-shadow,transform] duration-200",
    "hover:-translate-y-0.5 hover:shadow-[var(--shadow-card-hover)]",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
    selected
      ? "bg-shell-surface-active border-shell-border-strong shadow-[var(--shadow-card)]"
      : "bg-shell-surface border-shell-border hover:bg-shell-surface-hover",
  ].join(" ");

  return (
    <button type="button" onClick={onSelect} aria-label={`Select ${contact.name}`} className={cardCls}>
      <ContactAvatar name={contact.name} size={36} />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-shell-text truncate">{contact.name}</div>
        {contact.email && <div className="text-xs text-shell-text-secondary truncate">{contact.email}</div>}
      </div>
      {showChevron && (
        <ChevronRight size={16} className="text-shell-text-tertiary shrink-0" aria-hidden="true" />
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  ContactsApp                                                        */
/* ------------------------------------------------------------------ */

export function ContactsApp({ windowId: _windowId }: { windowId: string }) {
  const isMobile = useIsMobile();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Contact | null>(null);
  const [search, setSearch] = useState("");

  const filtered = contacts.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.email.toLowerCase().includes(search.toLowerCase()) ||
      c.phone.includes(search)
  );

  const selected = contacts.find((c) => c.id === selectedId) ?? null;

  function handleAdd() {
    setEditing(emptyContact());
  }

  function handleSave() {
    if (!editing) return;
    if (!editing.name.trim()) return;
    setContacts((prev) => {
      const idx = prev.findIndex((c) => c.id === editing.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = editing;
        return next;
      }
      return [...prev, editing];
    });
    setSelectedId(editing.id);
    setEditing(null);
  }

  function handleDelete(id: string) {
    setContacts((prev) => prev.filter((c) => c.id !== id));
    if (selectedId === id) setSelectedId(null);
  }

  function handleEdit() {
    if (selected) setEditing({ ...selected });
  }

  function handleCancel() {
    setEditing(null);
  }

  // Hide the app-level toolbar on mobile when detail is open —
  // MobileSplitView provides its own nav bar with back button there.
  const showToolbar = !isMobile || selectedId === null;

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-shell-bg-deep text-shell-text select-none relative">
      {/* Toolbar */}
      {showToolbar && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-shell-border shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-shell-surface-active text-accent shrink-0">
              <User size={16} />
            </span>
            <div className="min-w-0">
              <h1 className="text-sm font-semibold text-shell-text leading-tight">Contacts</h1>
              <span className="text-xs text-shell-text-tertiary">
                {contacts.length} {contacts.length === 1 ? "contact" : "contacts"}
              </span>
            </div>
          </div>
          <Button size="sm" onClick={handleAdd} aria-label="Add contact">
            <Plus size={14} />
            {isMobile ? "Add" : "Add Contact"}
          </Button>
        </div>
      )}

      {/* Master-detail — MobileSplitView stacks on mobile, splits on desktop */}
      <MobileSplitView
        selectedId={selectedId}
        onBack={() => setSelectedId(null)}
        listTitle="Contacts"
        detailTitle={selected?.name}
        detailActions={
          isMobile && selected ? (
            <Button variant="ghost" size="sm" onClick={handleAdd} aria-label="Add contact" className="h-8">
              <Plus size={14} />
            </Button>
          ) : undefined
        }
        list={
          <div aria-label="Contact list">
            {/* Search bar */}
            <div className="p-3">
              <div className="relative">
                <Search
                  size={14}
                  className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none z-10"
                />
                <Input
                  type="text"
                  placeholder="Search…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-8 h-8"
                  aria-label="Search contacts"
                />
              </div>
            </div>

            {/* Empty states */}
            {filtered.length === 0 && (
              <div className="flex flex-col items-center text-center px-6 mt-10">
                <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-shell-surface border border-shell-border text-shell-text-tertiary mb-3">
                  {contacts.length === 0 ? <User size={22} /> : <Search size={22} />}
                </span>
                <p className="text-sm font-medium text-shell-text">
                  {contacts.length === 0 ? "No contacts yet" : "No results"}
                </p>
                <p className="text-xs text-shell-text-tertiary mt-1">
                  {contacts.length === 0 ? "Add your first contact to get started." : "Try a different search."}
                </p>
                {contacts.length === 0 && (
                  <Button size="sm" onClick={handleAdd} className="mt-4" aria-label="Add contact">
                    <Plus size={14} />
                    Add Contact
                  </Button>
                )}
              </div>
            )}

            {/* Contact rows */}
            {filtered.length > 0 && (
              <div className="px-3 pb-4 space-y-1.5">
                {filtered.map((c) => (
                  <ContactRow
                    key={c.id}
                    contact={c}
                    selected={!isMobile && selectedId === c.id}
                    onSelect={() => setSelectedId(c.id)}
                    showChevron={isMobile}
                  />
                ))}
              </div>
            )}
          </div>
        }
        detail={
          selected ? (
            <ContactDetail
              contact={selected}
              onEdit={handleEdit}
              onDelete={() => handleDelete(selected.id)}
            />
          ) : !isMobile ? (
            <div className="flex-1 flex flex-col items-center justify-center h-full text-center px-6">
              <span className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-shell-surface border border-shell-border text-shell-text-tertiary mb-4">
                <User size={26} />
              </span>
              <p className="text-sm font-medium text-shell-text">No contact selected</p>
              <p className="text-xs text-shell-text-tertiary mt-1">Select a contact or add a new one.</p>
            </div>
          ) : null
        }
      />

      {/* Contact form — bottom sheet on mobile, modal on desktop */}
      {editing && (
        <ContactForm
          editing={editing}
          isNew={!contacts.find((c) => c.id === editing.id)}
          onChange={setEditing}
          onSave={handleSave}
          onCancel={handleCancel}
        />
      )}
    </div>
  );
}
