# Desktop Shell OS Apps — Implementation Plan

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 8 bundled OS utility apps for the desktop shell — Calculator, Calendar, Contacts, Browser, Media Player, Text Editor, Image Viewer, Terminal. Each replaces its PlaceholderApp in the registry.

**Architecture:** Each app is a self-contained React component at `desktop/src/apps/`. Vendored libraries (Plyr, tui.calendar, pell, Viewer.js, xterm.js) are installed as npm deps and wrapped as React components. Custom apps (Calculator, Contacts, Browser) are built from scratch.

**Tech Stack:** React 19, TypeScript, math.js, Plyr, pell, xterm.js

---

## File Structure

```
desktop/src/apps/
├── CalculatorApp.tsx       # Custom — math.js engine
├── CalendarApp.tsx         # Wrapper around tui.calendar
├── ContactsApp.tsx         # Custom CRUD
├── BrowserApp.tsx          # iframe + URL bar
├── MediaPlayerApp.tsx      # Wrapper around Plyr
├── TextEditorApp.tsx       # Wrapper around pell
├── ImageViewerApp.tsx      # Custom viewer with zoom/pan
└── TerminalApp.tsx         # Wrapper around xterm.js
```

---

## Task 1: Calculator App

**Files:**
- Create: `desktop/src/apps/CalculatorApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts` (wire up component)

- [ ] **Step 1: Install math.js**

Run: `cd /home/jay/tinyagentos/desktop && npm install mathjs`

- [ ] **Step 2: Create CalculatorApp**

```tsx
// desktop/src/apps/CalculatorApp.tsx
import { useState, useCallback } from "react";
import { evaluate } from "mathjs";

const BUTTONS = [
  ["C", "±", "%", "÷"],
  ["7", "8", "9", "×"],
  ["4", "5", "6", "−"],
  ["1", "2", "3", "+"],
  ["0", ".", "⌫", "="],
];

const OPS: Record<string, string> = { "÷": "/", "×": "*", "−": "-", "+": "+" };

export function CalculatorApp({ _windowId }: { _windowId: string }) {
  const [display, setDisplay] = useState("0");
  const [expression, setExpression] = useState("");

  const handleButton = useCallback((label: string) => {
    if (label === "C") {
      setDisplay("0");
      setExpression("");
    } else if (label === "⌫") {
      setDisplay((d) => (d.length > 1 ? d.slice(0, -1) : "0"));
    } else if (label === "±") {
      setDisplay((d) => (d.startsWith("-") ? d.slice(1) : "-" + d));
    } else if (label === "%") {
      try {
        const val = evaluate(display);
        setDisplay(String(val / 100));
      } catch { /* ignore */ }
    } else if (label === "=") {
      try {
        const expr = expression + display;
        const result = evaluate(expr);
        setDisplay(String(result));
        setExpression("");
      } catch {
        setDisplay("Error");
        setExpression("");
      }
    } else if (OPS[label]) {
      setExpression(expression + display + OPS[label]);
      setDisplay("0");
    } else {
      setDisplay((d) => (d === "0" && label !== "." ? label : d + label));
    }
  }, [display, expression]);

  const isOp = (l: string) => !!OPS[l] || ["C", "±", "%", "÷", "=", "⌫"].includes(l);

  return (
    <div className="flex flex-col h-full bg-shell-bg-deep p-3 gap-2">
      <div className="text-right px-3 py-1 text-shell-text-tertiary text-xs truncate min-h-[20px]">
        {expression || " "}
      </div>
      <div className="text-right px-3 py-2 text-3xl font-light text-shell-text truncate">
        {display}
      </div>
      <div className="flex-1 grid grid-cols-4 gap-1.5">
        {BUTTONS.flat().map((label, i) => (
          <button
            key={i}
            onClick={() => handleButton(label)}
            className={`rounded-lg text-lg font-medium transition-colors ${
              label === "="
                ? "bg-accent text-white hover:brightness-110"
                : isOp(label)
                  ? "bg-shell-surface-hover text-shell-text-secondary hover:bg-shell-surface-active"
                  : "bg-shell-surface text-shell-text hover:bg-shell-surface-hover"
            } ${label === "0" ? "col-span-1" : ""}`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire into app registry**

In `desktop/src/registry/app-registry.ts`, update the calculator entry's `component` from `placeholder` to:
```typescript
component: () => import("@/apps/CalculatorApp").then((m) => ({ default: m.CalculatorApp })),
```

- [ ] **Step 4: Rebuild**

Run: `cd /home/jay/tinyagentos/desktop && npm run build`

- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Calculator app with math.js engine"
```

---

## Task 2: Text Editor App

**Files:**
- Create: `desktop/src/apps/TextEditorApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Install pell**

Run: `cd /home/jay/tinyagentos/desktop && npm install pell`

- [ ] **Step 2: Create TextEditorApp**

```tsx
// desktop/src/apps/TextEditorApp.tsx
import { useEffect, useRef } from "react";
import { init } from "pell";

export function TextEditorApp({ _windowId }: { _windowId: string }) {
  const editorRef = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (editorRef.current && !initialized.current) {
      initialized.current = true;
      init({
        element: editorRef.current,
        onChange: () => {},
        actions: [
          "bold", "italic", "underline", "strikethrough",
          "heading1", "heading2",
          "olist", "ulist",
          "link", "image",
          "line",
        ],
      });
    }
  }, []);

  return (
    <div className="flex flex-col h-full">
      <div
        ref={editorRef}
        className="flex-1 [&_.pell-actionbar]:flex [&_.pell-actionbar]:gap-1 [&_.pell-actionbar]:p-2 [&_.pell-actionbar]:border-b [&_.pell-actionbar]:border-shell-border [&_.pell-actionbar]:bg-shell-surface [&_.pell-actionbar_button]:px-2 [&_.pell-actionbar_button]:py-1 [&_.pell-actionbar_button]:rounded [&_.pell-actionbar_button]:text-shell-text-secondary [&_.pell-actionbar_button]:text-sm [&_.pell-actionbar_button:hover]:bg-shell-surface-hover [&_.pell-content]:flex-1 [&_.pell-content]:p-4 [&_.pell-content]:text-shell-text [&_.pell-content]:outline-none [&_.pell-content]:overflow-auto [&_.pell-content]:min-h-0"
      />
    </div>
  );
}
```

- [ ] **Step 3: Wire into app registry** — same pattern as Task 1
- [ ] **Step 4: Rebuild**
- [ ] **Step 5: Commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Text Editor app with pell rich text editor"
```

---

## Task 3: Browser App

**Files:**
- Create: `desktop/src/apps/BrowserApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Create BrowserApp**

```tsx
// desktop/src/apps/BrowserApp.tsx
import { useState, useCallback, useRef } from "react";
import { ArrowLeft, ArrowRight, RotateCw, Globe } from "lucide-react";

export function BrowserApp({ _windowId }: { _windowId: string }) {
  const [url, setUrl] = useState("https://duckduckgo.com");
  const [input, setInput] = useState("https://duckduckgo.com");
  const [history, setHistory] = useState<string[]>(["https://duckduckgo.com"]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const navigate = useCallback((newUrl: string) => {
    let normalized = newUrl;
    if (!normalized.startsWith("http://") && !normalized.startsWith("https://")) {
      normalized = "https://" + normalized;
    }
    setUrl(normalized);
    setInput(normalized);
    setHistory((h) => [...h.slice(0, historyIndex + 1), normalized]);
    setHistoryIndex((i) => i + 1);
  }, [historyIndex]);

  const goBack = useCallback(() => {
    if (historyIndex > 0) {
      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      setUrl(history[newIndex]!);
      setInput(history[newIndex]!);
    }
  }, [historyIndex, history]);

  const goForward = useCallback(() => {
    if (historyIndex < history.length - 1) {
      const newIndex = historyIndex + 1;
      setHistoryIndex(newIndex);
      setUrl(history[newIndex]!);
      setInput(history[newIndex]!);
    }
  }, [historyIndex, history]);

  const refresh = useCallback(() => {
    if (iframeRef.current) {
      iframeRef.current.src = url;
    }
  }, [url]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate(input);
  };

  return (
    <div className="flex flex-col h-full">
      <form onSubmit={handleSubmit} className="flex items-center gap-2 px-2 py-1.5 bg-shell-surface border-b border-shell-border">
        <button type="button" onClick={goBack} disabled={historyIndex <= 0} className="p-1 rounded hover:bg-shell-surface-hover disabled:opacity-30" aria-label="Back">
          <ArrowLeft size={14} className="text-shell-text-secondary" />
        </button>
        <button type="button" onClick={goForward} disabled={historyIndex >= history.length - 1} className="p-1 rounded hover:bg-shell-surface-hover disabled:opacity-30" aria-label="Forward">
          <ArrowRight size={14} className="text-shell-text-secondary" />
        </button>
        <button type="button" onClick={refresh} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Refresh">
          <RotateCw size={14} className="text-shell-text-secondary" />
        </button>
        <div className="flex-1 flex items-center gap-2 px-3 py-1 rounded-md bg-shell-bg border border-shell-border">
          <Globe size={12} className="text-shell-text-tertiary" />
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="flex-1 bg-transparent text-xs text-shell-text outline-none"
          />
        </div>
      </form>
      <iframe
        ref={iframeRef}
        src={url}
        className="flex-1 border-none bg-white"
        sandbox="allow-downloads allow-forms allow-modals allow-pointer-lock allow-popups allow-presentation allow-same-origin allow-scripts"
        referrerPolicy="no-referrer"
        title="Browser"
      />
    </div>
  );
}
```

- [ ] **Step 2: Wire into app registry**
- [ ] **Step 3: Rebuild and commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Browser app with iframe navigation"
```

---

## Task 4: Media Player App

**Files:**
- Create: `desktop/src/apps/MediaPlayerApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Install Plyr**

Run: `cd /home/jay/tinyagentos/desktop && npm install plyr`

- [ ] **Step 2: Create MediaPlayerApp**

```tsx
// desktop/src/apps/MediaPlayerApp.tsx
import { useEffect, useRef, useState } from "react";
import Plyr from "plyr";
import "plyr/dist/plyr.css";

export function MediaPlayerApp({ _windowId }: { _windowId: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const playerRef = useRef<Plyr | null>(null);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (videoRef.current && !playerRef.current) {
      playerRef.current = new Plyr(videoRef.current, {
        controls: ["play-large", "play", "progress", "current-time", "mute", "volume", "fullscreen"],
      });
    }
    return () => {
      playerRef.current?.destroy();
      playerRef.current = null;
    };
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const url = URL.createObjectURL(file);
      setSrc(url);
      if (videoRef.current) {
        videoRef.current.src = url;
        playerRef.current?.play();
      }
    }
  };

  return (
    <div className="flex flex-col h-full bg-black">
      {!src && (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <p className="text-shell-text-secondary text-sm">Drop a media file or click to open</p>
          <label className="px-4 py-2 rounded-lg bg-accent text-white text-sm cursor-pointer hover:brightness-110">
            Open File
            <input type="file" accept="video/*,audio/*" onChange={handleFileSelect} className="hidden" />
          </label>
        </div>
      )}
      <video ref={videoRef} className={src ? "flex-1" : "hidden"} />
    </div>
  );
}
```

- [ ] **Step 3: Wire into app registry, rebuild, commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Media Player app with Plyr"
```

---

## Task 5: Image Viewer App

**Files:**
- Create: `desktop/src/apps/ImageViewerApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Create ImageViewerApp (custom, no external dep)**

```tsx
// desktop/src/apps/ImageViewerApp.tsx
import { useState, useRef, useCallback } from "react";
import { ZoomIn, ZoomOut, RotateCw, Maximize } from "lucide-react";

export function ImageViewerApp({ _windowId }: { _windowId: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const imgRef = useRef<HTMLImageElement>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSrc(URL.createObjectURL(file));
      setZoom(1);
      setRotation(0);
    }
  };

  const zoomIn = useCallback(() => setZoom((z) => Math.min(z * 1.25, 5)), []);
  const zoomOut = useCallback(() => setZoom((z) => Math.max(z / 1.25, 0.1)), []);
  const rotate = useCallback(() => setRotation((r) => (r + 90) % 360), []);
  const fitToView = useCallback(() => { setZoom(1); setRotation(0); }, []);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-1 px-2 py-1.5 bg-shell-surface border-b border-shell-border">
        <label className="px-3 py-1 rounded text-xs text-shell-text-secondary bg-shell-surface-hover hover:bg-shell-surface-active cursor-pointer">
          Open
          <input type="file" accept="image/*" onChange={handleFileSelect} className="hidden" />
        </label>
        <div className="flex-1" />
        <button onClick={zoomOut} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Zoom out"><ZoomOut size={14} className="text-shell-text-secondary" /></button>
        <span className="text-xs text-shell-text-tertiary w-12 text-center">{Math.round(zoom * 100)}%</span>
        <button onClick={zoomIn} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Zoom in"><ZoomIn size={14} className="text-shell-text-secondary" /></button>
        <button onClick={rotate} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Rotate"><RotateCw size={14} className="text-shell-text-secondary" /></button>
        <button onClick={fitToView} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Fit to view"><Maximize size={14} className="text-shell-text-secondary" /></button>
      </div>
      <div className="flex-1 overflow-auto flex items-center justify-center bg-shell-bg-deep">
        {src ? (
          <img
            ref={imgRef}
            src={src}
            alt="Viewer"
            style={{ transform: `scale(${zoom}) rotate(${rotation}deg)`, transition: "transform 0.2s" }}
            className="max-w-none"
          />
        ) : (
          <p className="text-shell-text-tertiary text-sm">Open an image file to view</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire into app registry, rebuild, commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Image Viewer app with zoom, rotate, pan"
```

---

## Task 6: Contacts App

**Files:**
- Create: `desktop/src/apps/ContactsApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Create ContactsApp**

```tsx
// desktop/src/apps/ContactsApp.tsx
import { useState, useCallback } from "react";
import { Plus, Search, Trash2, User } from "lucide-react";

interface Contact {
  id: string;
  name: string;
  email: string;
  phone: string;
  notes: string;
}

export function ContactsApp({ _windowId }: { _windowId: string }) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Contact | null>(null);

  const filtered = contacts.filter((c) =>
    c.name.toLowerCase().includes(query.toLowerCase()) ||
    c.email.toLowerCase().includes(query.toLowerCase())
  );

  const selectedContact = contacts.find((c) => c.id === selected);

  const addContact = useCallback(() => {
    const newContact: Contact = {
      id: crypto.randomUUID(),
      name: "New Contact",
      email: "",
      phone: "",
      notes: "",
    };
    setContacts((cs) => [...cs, newContact]);
    setSelected(newContact.id);
    setEditing(newContact);
  }, []);

  const saveEdit = useCallback(() => {
    if (!editing) return;
    setContacts((cs) => cs.map((c) => (c.id === editing.id ? editing : c)));
    setEditing(null);
  }, [editing]);

  const deleteContact = useCallback((id: string) => {
    setContacts((cs) => cs.filter((c) => c.id !== id));
    if (selected === id) setSelected(null);
    if (editing?.id === id) setEditing(null);
  }, [selected, editing]);

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <div className="w-60 border-r border-shell-border flex flex-col bg-shell-surface">
        <div className="flex items-center gap-2 p-2 border-b border-shell-border">
          <div className="flex-1 flex items-center gap-1.5 px-2 py-1 rounded bg-shell-bg border border-shell-border">
            <Search size={12} className="text-shell-text-tertiary" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search..." className="flex-1 bg-transparent text-xs text-shell-text outline-none" />
          </div>
          <button onClick={addContact} className="p-1.5 rounded hover:bg-shell-surface-hover" aria-label="Add contact"><Plus size={14} className="text-shell-text-secondary" /></button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {filtered.map((c) => (
            <button key={c.id} onClick={() => { setSelected(c.id); setEditing(null); }}
              className={`w-full text-left px-3 py-2 flex items-center gap-2 text-sm ${selected === c.id ? "bg-accent/15 text-accent" : "text-shell-text hover:bg-shell-surface-hover"}`}>
              <User size={14} />
              <span className="truncate">{c.name}</span>
            </button>
          ))}
          {filtered.length === 0 && <p className="text-center text-xs text-shell-text-tertiary py-8">No contacts</p>}
        </div>
      </div>

      {/* Detail */}
      <div className="flex-1 p-4 overflow-y-auto">
        {selectedContact && !editing && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-medium text-shell-text">{selectedContact.name}</h2>
              <div className="flex gap-2">
                <button onClick={() => setEditing({ ...selectedContact })} className="px-3 py-1 text-xs rounded bg-shell-surface-hover text-shell-text-secondary hover:bg-shell-surface-active">Edit</button>
                <button onClick={() => deleteContact(selectedContact.id)} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Delete"><Trash2 size={14} className="text-traffic-close" /></button>
              </div>
            </div>
            {selectedContact.email && <p className="text-sm text-shell-text-secondary">{selectedContact.email}</p>}
            {selectedContact.phone && <p className="text-sm text-shell-text-secondary">{selectedContact.phone}</p>}
            {selectedContact.notes && <p className="text-sm text-shell-text-tertiary mt-2">{selectedContact.notes}</p>}
          </div>
        )}
        {editing && (
          <div className="space-y-3">
            <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="Name" className="w-full px-3 py-2 rounded bg-shell-surface border border-shell-border text-sm text-shell-text outline-none" />
            <input value={editing.email} onChange={(e) => setEditing({ ...editing, email: e.target.value })} placeholder="Email" className="w-full px-3 py-2 rounded bg-shell-surface border border-shell-border text-sm text-shell-text outline-none" />
            <input value={editing.phone} onChange={(e) => setEditing({ ...editing, phone: e.target.value })} placeholder="Phone" className="w-full px-3 py-2 rounded bg-shell-surface border border-shell-border text-sm text-shell-text outline-none" />
            <textarea value={editing.notes} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} placeholder="Notes" rows={3} className="w-full px-3 py-2 rounded bg-shell-surface border border-shell-border text-sm text-shell-text outline-none resize-none" />
            <div className="flex gap-2">
              <button onClick={saveEdit} className="px-4 py-1.5 rounded bg-accent text-white text-xs hover:brightness-110">Save</button>
              <button onClick={() => setEditing(null)} className="px-4 py-1.5 rounded bg-shell-surface-hover text-shell-text-secondary text-xs hover:bg-shell-surface-active">Cancel</button>
            </div>
          </div>
        )}
        {!selectedContact && !editing && (
          <div className="flex items-center justify-center h-full text-shell-text-tertiary text-sm">Select a contact or create one</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire, rebuild, commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Contacts app with CRUD interface"
```

---

## Task 7: Calendar App

**Files:**
- Create: `desktop/src/apps/CalendarApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Create a simple custom calendar (skip tui.calendar to avoid heavy dep for now)**

```tsx
// desktop/src/apps/CalendarApp.tsx
import { useState, useMemo } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function CalendarApp({ _windowId }: { _windowId: string }) {
  const [viewing, setViewing] = useState(new Date());
  const today = new Date();

  const year = viewing.getFullYear();
  const month = viewing.getMonth();

  const days = useMemo(() => {
    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    let startDay = firstDay.getDay() - 1;
    if (startDay < 0) startDay = 6;

    const cells: (number | null)[] = [];
    for (let i = 0; i < startDay; i++) cells.push(null);
    for (let d = 1; d <= lastDay.getDate(); d++) cells.push(d);
    while (cells.length % 7 !== 0) cells.push(null);
    return cells;
  }, [year, month]);

  const prevMonth = () => setViewing(new Date(year, month - 1, 1));
  const nextMonth = () => setViewing(new Date(year, month + 1, 1));
  const goToday = () => setViewing(new Date());

  const isToday = (d: number | null) =>
    d !== null && year === today.getFullYear() && month === today.getMonth() && d === today.getDate();

  return (
    <div className="flex flex-col h-full p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <button onClick={prevMonth} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Previous month"><ChevronLeft size={16} className="text-shell-text-secondary" /></button>
          <h2 className="text-lg font-medium text-shell-text min-w-[180px] text-center">{MONTHS[month]} {year}</h2>
          <button onClick={nextMonth} className="p-1 rounded hover:bg-shell-surface-hover" aria-label="Next month"><ChevronRight size={16} className="text-shell-text-secondary" /></button>
        </div>
        <button onClick={goToday} className="px-3 py-1 text-xs rounded bg-shell-surface-hover text-shell-text-secondary hover:bg-shell-surface-active">Today</button>
      </div>

      <div className="grid grid-cols-7 gap-px flex-1">
        {DAYS.map((d) => (
          <div key={d} className="text-center text-xs font-medium text-shell-text-tertiary py-2">{d}</div>
        ))}
        {days.map((d, i) => (
          <div key={i} className={`flex items-start justify-center pt-2 text-sm rounded ${
            d === null ? "" : "hover:bg-shell-surface-hover cursor-pointer"
          } ${isToday(d) ? "bg-accent/15" : ""}`}>
            {d !== null && (
              <span className={`w-7 h-7 flex items-center justify-center rounded-full ${
                isToday(d) ? "bg-accent text-white font-medium" : "text-shell-text"
              }`}>{d}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire, rebuild, commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Calendar app with month view"
```

---

## Task 8: Terminal App + Final Build

**Files:**
- Create: `desktop/src/apps/TerminalApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Install xterm.js**

Run: `cd /home/jay/tinyagentos/desktop && npm install @xterm/xterm`

- [ ] **Step 2: Create TerminalApp (display-only for now — needs WebSocket backend for real shell)**

```tsx
// desktop/src/apps/TerminalApp.tsx
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

export function TerminalApp({ _windowId }: { _windowId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);

  useEffect(() => {
    if (containerRef.current && !termRef.current) {
      const term = new Terminal({
        theme: {
          background: "#151625",
          foreground: "rgba(255, 255, 255, 0.85)",
          cursor: "#667eea",
          selectionBackground: "rgba(102, 126, 234, 0.3)",
        },
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
        fontSize: 13,
        cursorBlink: true,
      });
      term.open(containerRef.current);
      term.writeln("TinyAgentOS Terminal");
      term.writeln("WebSocket shell connection not yet configured.");
      term.writeln("This terminal will connect to the host shell via /ws/terminal.");
      term.writeln("");
      term.write("$ ");

      let line = "";
      term.onData((data) => {
        if (data === "\r") {
          term.writeln("");
          if (line.trim() === "help") {
            term.writeln("Terminal shell requires WebSocket backend at /ws/terminal");
          } else if (line.trim()) {
            term.writeln(`Command not available: ${line.trim()}`);
          }
          line = "";
          term.write("$ ");
        } else if (data === "\u007f") {
          if (line.length > 0) {
            line = line.slice(0, -1);
            term.write("\b \b");
          }
        } else {
          line += data;
          term.write(data);
        }
      });

      termRef.current = term;
    }
    return () => {
      termRef.current?.dispose();
      termRef.current = null;
    };
  }, []);

  return <div ref={containerRef} className="h-full w-full" />;
}
```

- [ ] **Step 3: Wire into app registry**
- [ ] **Step 4: Run frontend tests to verify nothing broke**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run`

- [ ] **Step 5: Rebuild**

Run: `cd /home/jay/tinyagentos/desktop && npm run build`

- [ ] **Step 6: Commit**

```bash
cd /home/jay/tinyagentos && git add desktop/ static/desktop/ && git commit -m "add Terminal app with xterm.js, complete OS apps suite"
```
