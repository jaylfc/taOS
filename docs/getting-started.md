# Getting Started with TinyAgentOS

Welcome. This guide walks you through installing TinyAgentOS, getting your first AI agent running, and finding your way around the platform. It assumes you're comfortable with a Linux terminal but have never run an AI agent before — every concept is explained along the way.

---

## What is TinyAgentOS?

TinyAgentOS is a self-hosted platform for running AI agents on affordable hardware — a single-board computer, a budget PC, or anything in between. You get a web dashboard (no coding required) where you can browse an app store of agent frameworks and models, deploy agents in isolated containers, configure how they communicate (Telegram, Discord, web chat), and monitor everything in one place.

Think of it like a home server for AI agents: you own the hardware, you own the data, and nothing phones home.

---

## 1. What You Need

### Hardware

Any of the following will work. More RAM means bigger, more capable models.

| Device | RAM | Notes |
|--------|-----|-------|
| **Orange Pi 5 Plus** (recommended) | 16 GB | RK3588 chip with 6 TOPS NPU for fast inference |
| **Orange Pi 5** | 8–16 GB | Same NPU, slightly fewer I/O ports |
| **Raspberry Pi 5** | 8 GB minimum | CPU-only inference unless you add an accelerator HAT |
| **Any x86/x64 PC or laptop** | 4 GB+ | Budget PC, old laptop, NUC, etc. GPU optional |
| **NVIDIA GPU system** | 4 GB+ VRAM | GTX 1050 Ti and up; CUDA acceleration |
| **AMD GPU system** | 8 GB+ VRAM | RX 6600 and up; ROCm acceleration |

The platform itself uses roughly 345 MB of RAM when idle, so it runs comfortably alongside your OS on any of the above.

**Not sure which to buy?** The Orange Pi 5 Plus with 16 GB is the recommended starting point. It has a built-in NPU (neural processing unit) that runs models significantly faster than the CPU alone, and 16 GB gives you room to run multiple agents at once.

### Software

- **OS:** Armbian or Debian-based Linux (Ubuntu works too). The installer handles everything else.
- **Network:** The device needs internet access to download models and framework packages.
- **Browser:** On any other device on the same network (laptop, phone, tablet). The TinyAgentOS web GUI runs on your device; you access it from your browser.

**SQLCipher (for the browser app's encrypted cookie jar)** — the browser app needs the SQLCipher C library installed at the system level before `pip install` can build its `sqlcipher3` Python binding:

- **macOS:** `brew install sqlcipher`
- **Debian / Ubuntu / Pi OS:** `sudo apt install libsqlcipher-dev`
- **Fedora / RHEL:** `sudo dnf install sqlcipher-devel`
- **Windows:** SQLCipher binaries via [vcpkg](https://vcpkg.io/) or run inside WSL

---

## 2. Installation

### Quick Install (recommended)

On your device, open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-server.sh | sudo bash
```

This script will:
1. Install system dependencies (`python3`, `git`, `nodejs`, `avahi-daemon` for mDNS, and others)
2. Clone TinyAgentOS to `~/tinyagentos` (override with `TAOS_INSTALL_DIR`)
3. Create a Python virtual environment and install all Python packages
4. Register and start a `systemd` service so TinyAgentOS runs automatically on boot

At the end, it prints your device's IP address:

```
  TinyAgentOS installed successfully!

  Open: http://your-device-ip:6969

  Service: systemctl status tinyagentos
  Logs:    journalctl -u tinyagentos -f
```

Open that URL in your browser. You're done with installation.

**If your network supports mDNS** (most home networks do), you can also use `http://taos.local:6969` from any device on the same network — no need to look up the IP.

---

### Manual Install

If you prefer to install manually or want to run TinyAgentOS in a specific location:

```bash
# Clone the repository
git clone https://github.com/jaylfc/tinyagentos.git
cd tinyagentos

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install TinyAgentOS and its dependencies
pip install -e .

# Start the server
python -m uvicorn tinyagentos.app:create_app --factory --host 0.0.0.0 --port 6969
```

Then open `http://your-device-ip:6969` in a browser.

To run it as a background service so it survives reboots, copy `tinyagentos.service` from the repo root into `/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tinyagentos
```

---

## 3. First Boot

When you open `http://your-host:6969` for the first time, TinyAgentOS loads the **web desktop shell** directly — a full browser-based desktop environment with a dock, launchpad, window manager, and 26 bundled apps. On phones and tablets it automatically swaps to a touch-first mobile view with a home grid and card switcher. A setup wizard runs on first launch to walk you through hardware detection and your first agent.

### Hardware Auto-Detection

The platform scans your hardware and builds a profile — CPU, RAM, whether you have an NPU (Rockchip, Hailo, Coral), and any GPU. This profile drives the recommendations you'll see in the Store and Models pages: apps and models that won't run well on your hardware are filtered out or flagged.

You'll see a summary like:

```
Detected: RK3588 (ARM) | 16 GB RAM | Rockchip NPU (6 TOPS) | 256 GB storage
Recommended backend: rkllama (NPU)
```

### The Setup Wizard

The wizard shows:
- Your hardware summary and what it unlocks
- Quick links: **Install a model**, **Install a framework**, **Deploy your first agent**
- A short onboarding tour (optional, takes 2 minutes)

You don't have to follow the wizard step-by-step — you can dismiss it and navigate freely. But if this is your first time, the steps below mirror the recommended path.

---

## 3a. Using the Desktop Shell

The desktop is the main way you'll interact with TinyAgentOS. It works like a regular operating system's desktop — but it runs entirely in your browser.

### The window manager

- **Open an app** — click its icon in the dock at the bottom, or open the Launchpad (the grid icon) and pick from the full app list.
- **Move a window** — drag it by its title bar.
- **Snap a window** — drag it to the left, right, top, or corners of the screen and it snaps to half/quarter tiles.
- **Resize a window** — drag any edge or corner.
- **Minimise, maximise, close** — the three buttons on every window's title bar.
- **Switch between windows** — click any visible window, or click its icon in the dock (running apps show an indicator dot).

### The dock

The bar at the bottom of the screen holds pinned apps and currently running apps. Right-click an app icon to pin or unpin it. Your dock layout is saved automatically and follows you across devices.

### Global search (Ctrl+Space)

Press `Ctrl+Space` from anywhere on the desktop to open the global search bar. It searches apps, agents, messages, files, and your personal memory entries. Hit `Enter` on a result to jump straight to it.

### Right-click for the desktop menu

Right-click anywhere on the empty desktop to get a context menu with:
- **New folder**
- **Change wallpaper** (pick from 8 built-in gradients)
- **Add widget** (Clock, Agent Status, Quick Notes, System Stats, Weather)
- **Save to Memory** (captures the current selection into your personal memory)
- **Settings**

### Widgets

Widgets are small, always-on info tiles you can drop anywhere on the desktop. Drag them to move, resize them from the corner, and close them with the X. They persist across sessions.

### Notifications

The bell in the top-right shows live toast notifications for backend events, agent status changes, and finished downloads. Click it to open the notification centre dropdown with your full history.

### Login gate

If you've enabled a password under Settings, the desktop prompts for it on first load on each device. Sessions persist in the browser until you sign out.

---

## 4. Your First Agent

An "agent" in this context is a program that uses a language model to respond to messages, take actions, or run tasks on a schedule. Getting one running involves three things: a **framework** (the code that defines how the agent behaves), a **model** (the language model the framework uses to think), and a **deployment** (running them together in an isolated container).

### Step 1 — Install a Framework

Go to **Store** in the left sidebar. You'll see a catalog of 43+ apps grouped by category.

Under **Agent Frameworks**, find and install one of these:

- **SmolAgents** (recommended for beginners) — Made by Hugging Face. Well-documented, code-based agents, 26k GitHub stars. Good choice if you want to understand what your agent is doing.
- **PocketFlow** — Minimal 100-line framework, zero dependencies, graph-based workflows. Good if you want something lightweight and easy to modify.

Click **Install** on your chosen framework. The platform downloads and configures it in the background. A progress indicator appears in the top bar. Installation typically takes 1–3 minutes depending on your connection.

### Step 2 — Download a Model

Go to **Models** in the sidebar. This page lists available language models filtered to what your hardware can run.

Choose based on your RAM:

| Your RAM | Recommended Model | Why |
|----------|------------------|-----|
| 16 GB | **Qwen3-4B** | Good balance of speed and capability for most tasks |
| 8 GB | **Qwen3-1.7B** | Fast, fits comfortably in memory, still useful |

Click **Download** next to your chosen model. Downloads happen in the background — you can continue setting things up while it downloads. The progress bar shows download speed and estimated time remaining.

A model is just a file (`.gguf` or `.rkllm` format). Once downloaded, it stays on your device and doesn't need the internet again.

### Step 3 — Deploy an Agent

Go to **Agents** in the sidebar and click **Deploy Agent**. This opens the 5-step deployment wizard:

**Step 1 of 5 — Name your agent**
Give your agent a name (e.g. "MyFirstAgent") and optionally a description. The name is just for you.

**Step 2 of 5 — Choose a framework**
Select the framework you installed in Step 1 above. Only installed frameworks appear here.

**Step 3 of 5 — Choose a model**
Select the model you downloaded in Step 2. The wizard shows which models are compatible with the chosen framework.

**Step 4 of 5 — Configure**
Basic configuration options: system prompt (the instructions that shape your agent's personality and focus), and any framework-specific settings. Leaving everything at defaults is fine for your first agent.

**Step 5 of 5 — Review and deploy**
Summary of your choices. Click **Deploy**. The platform creates an isolated LXC container for your agent — this means each agent has its own memory and environment, so they don't interfere with each other.

### Step 4 — Verify the Agent is Running

Back on the Agents page, your new agent appears in the list. The status indicator should turn green (usually within 30–60 seconds of deploying). Click on the agent to open its detail view, where you'll see:

- **Status** — running, stopped, or error
- **Logs** — live output from the agent process
- **Memory** — how much RAM it's using
- **Channels** — which communication channels it's connected to

If the status stays yellow or turns red, check the **Logs** tab for error messages. The most common issue at this stage is the model still downloading — the agent will start automatically once it's ready.

---

## 5. Exploring the Platform

Here's a quick map of the apps available from the desktop dock and launchpad.

### Platform apps

| App | What it's for |
|-----|--------------|
| **Dashboard** | Real-time overview: CPU/RAM usage, agent status, query counts, backend health, and an activity feed. This is your home base. |
| **Store** | Browse and install frameworks, models, tools, and services. Hardware-aware — incompatible apps are filtered out. |
| **Models** | Download and manage language models. Tracks download progress, shows disk usage per model, lets you delete unused ones. |
| **Images** | Generate images using Stable Diffusion (if you have a supported GPU or NPU). Gallery of past generations, prompt history, and an MCP tool agents can use to generate images on demand. |
| **Memory** | Browse and search the memories your agents have accumulated, plus your own personal **My Memory** section. Keyword search, filter by agent or collection, view or delete individual chunks. |
| **Messages** | Built-in chat (Discord-style) for talking to your agents over WebSocket, with channels, threads, rich embeds, and a canvas split-view. |
| **Channels** | Configure how your agents communicate with the outside world: Telegram, Discord, Slack, web chat, webhooks, email. |
| **Agents** | Deploy new agents, monitor running ones, view logs, assign skills, adjust configuration. |
| **Secrets** | Encrypted storage for API keys and tokens. You store a secret here once; agents access it by name without seeing the raw value. |
| **Tasks** | Schedule recurring jobs for your agents — daily summaries, memory cleanup, data imports. Built-in presets for common patterns. |
| **Import** | Drag and drop files to embed into an agent's memory. Supported formats: `.txt`, `.md`, `.pdf`, `.html`, `.json`, `.csv`. |
| **Files** | Real virtual filesystem with your personal workspace and shared folders that agents can read and write to. |
| **Settings** | System info, storage usage, backup/restore, update TinyAgentOS, test backend connections, toggle dark/light theme, and per-category toggles for User Memory auto-capture. taOS periodically checks for updates and reports an anonymous install count (a daily aggregate estimate, no identifiers); disable with `TAOS_NO_UPDATE_PING=1` or in Settings. |

### OS apps

| App | What it's for |
|-----|--------------|
| **Calculator** | Full math.js expression engine. |
| **Calendar** | Month view for events and reminders. |
| **Contacts** | Simple CRUD address book. |
| **Browser** | Built-in web browser that goes through a server-side URL-rewriting proxy so any site renders inline. Bookmarks, Open in Tab, Agent Browse button. |
| **Media Player** | Plyr-based audio/video player for files in your workspace. |
| **Text Editor** | CodeMirror 6 editor with an Obsidian-style theme. Content written here can be captured into User Memory. |
| **Image Viewer** | Zoom and rotate for images. |
| **Terminal** | Real PTY terminal with xterm.js. Pick **Local Shell** for a shell on the host, or **SSH Connection** to connect to any remote host with host/port/user/password or key auth. Recent hosts are remembered. |

### Games

| App | What it's for |
|-----|--------------|
| **Chess** | Plays against your real agents via the LLM backend. |
| **Wordle** | Daily word game. |
| **Crosswords** | Classic crossword puzzles. |

---

## 5a. Mobile Install (iOS / Android)

TinyAgentOS works as a fullscreen Progressive Web App (PWA) on phones and tablets. Once installed, it hides the browser chrome, respects the device's safe area, and behaves like a native app.

### iOS / iPadOS

1. Open `http://your-host:6969` in **Safari** (PWA install must go through Safari on iOS).
2. Tap the **Share** button, then **Add to Home Screen**.
3. Launch TinyAgentOS from the home screen icon — it opens fullscreen with no browser bars.

The Messages app has its own dedicated PWA at `http://your-host:6969/chat-pwa` — install it the same way to get a private, agent-only messenger on your home screen (works like an internal Discord).

> **Auth at install time:** The install itself does not require you to be logged in. The PWA shell is publicly accessible so the service worker can cache it immediately. The first time you open the installed app (or any time your session has expired) you will see the taOS login screen — that screen is part of the SPA itself, not a server redirect. Sign in once and the app resumes normally. This design also means the cached shell survives a backend restart: the app opens from cache while the backend comes back up, then reconnects automatically.

### Android

1. Open the URL in **Chrome**.
2. Tap the three-dot menu, then **Install app** (or **Add to Home Screen**).
3. Launch from your app drawer.

The mobile shell uses a bottom **pill bar** for navigation: tap the pill to go home, swipe up on the pill to open the card switcher (flick cards upward to close them), and use the back arrow to step out of an app. A mobile top bar shows "< Back" and the current app title.

---

## 5b. User Memory

TinyAgentOS includes a personal memory system just for you, think of it as your own private notebook that the platform helps fill in automatically. It's separate from agent memories. Under the hood it is powered by taosmd (`pip install taosmd`, the same library that backs agent memories): writes go to taosmd's `POST /ingest/batch` endpoint and keyword reads go to `GET /search?mode=bm25`. A local SQLite FTS5 store acts as an automatic fallback if taosmd is unreachable. You configure the taosmd address via the `TAOS_USER_MEMORY_URL` environment variable.

### What gets captured

By default, TinyAgentOS can auto-capture:
- **Conversations** from the Messages app
- **Notes** you write in the Text Editor
- **File activity** in the Files app
- **Search queries** from global search

Each category has its own on/off toggle under **Settings > Memory**, so you control exactly what gets saved.

### Where to see it

- **Memory app** → **My Memory** section, alongside your agent memories
- **Global search (Ctrl+Space)** — your memory entries appear inline with app and agent results
- **Right-click the desktop → Save to Memory** to manually capture the current selection

### Letting agents read your memory

Agents cannot read your personal memory by default. To grant an agent access, set the `TAOS_USER_MEMORY_URL` environment variable for that agent — it's an explicit, per-agent permission.

---

## 6. Adding a Communication Channel

A communication channel is how your agent talks to the outside world. Without one, your agent runs but has no way to receive messages or send responses. **Telegram** is the easiest to set up.

### Setting Up Telegram

**What you'll need:** A Telegram account (free) and 5 minutes.

**Step 1 — Create a Telegram bot**

In Telegram, search for `@BotFather` and start a chat. Send:
```
/newbot
```
BotFather will ask for a name (displayed to users) and a username (must end in `bot`, e.g. `myagent_bot`). It then gives you a **bot token** — a string like `7123456789:AAHd...`. Copy it.

**Step 2 — Store the token**

In TinyAgentOS, go to **Secrets** and click **Add Secret**. Name it something like `telegram_bot_token`, paste your token as the value, and save. The token is now encrypted on your device.

**Step 3 — Configure the channel**

Go to **Channels** and click **Add Channel**. Select **Telegram** from the list (it's under "Easy Setup"). Choose:
- Which secret holds your bot token
- Which agent should receive messages from this bot
- (Optional) Whether to restrict the bot to specific Telegram user IDs for privacy

Click **Save**. The channel activates immediately.

**Step 4 — Test it**

Open Telegram and find your bot by its username. Send it a message. Your agent should respond within a few seconds.

Other channels (Discord, Slack, web chat, webhooks) follow a similar pattern — create an API key or webhook URL in that service, store it in Secrets, then configure the channel.

---

## 7. Monitoring

### Dashboard KPIs

The **Dashboard** shows key metrics that update automatically:

- **CPU %** and **RAM %** — sparkline graphs for the past few minutes. Useful for spotting if a model or agent is consuming unexpected resources.
- **Agents** — count of running vs. total deployed agents.
- **Queries** — messages processed in the last hour, with a latency chart.
- **Backend health** — whether your inference backend (rkllama, ollama, llama.cpp, etc.) is responding. A red indicator here means agents can't get model responses.
- **Activity feed** — a log of recent agent actions, errors, and system events.

### Notifications

The bell icon in the top navigation bar shows alerts. You'll receive notifications when:
- An agent stops unexpectedly
- An inference backend goes down or comes back up
- A download completes or fails
- A scheduled task fails

Click the bell to see notification history. Click a notification to mark it read or jump to the relevant page.

### Logs

Each agent has its own log stream. Go to **Agents**, click on an agent, then open the **Logs** tab. Logs stream live — useful for debugging why an agent isn't responding or is producing unexpected output.

For TinyAgentOS itself (not individual agents), you can view system logs from the terminal:

```bash
journalctl -u tinyagentos -f
```

The `-f` flag follows the log in real time.

---

## 8. Backup

TinyAgentOS stores your configuration, agent data, secrets, and memories in `~/tinyagentos/data/` (or `$TAOS_INSTALL_DIR/data/` if you set a custom install path).

### Backup via Settings

Go to **Settings** and scroll to the **Backup** section. Click **Create Backup**. The platform bundles your configuration and data into a single archive file and offers it as a download. Store this file somewhere safe — on your laptop, a USB drive, or cloud storage.

**What's included:** Agent configurations, secrets (encrypted), channel settings, scheduled tasks, system config.

**What's not included:** Downloaded model files (these are large and can be re-downloaded) and LXC container filesystems (agents will need to be re-deployed from your saved config after a restore).

### Restore

On a fresh TinyAgentOS install, go to **Settings > Backup > Restore**, upload your backup file, and click **Restore**. Your agents, channels, and secrets will be recreated.

### Manual Backup

If you prefer to do it yourself:

```bash
cp -r ~/tinyagentos/data /your/backup/location/tinyagentos-data-$(date +%Y%m%d)
```

---

## 9. Getting Help

**GitHub Issues** — the primary place to report bugs or ask questions:
[https://github.com/jaylfc/tinyagentos/issues](https://github.com/jaylfc/tinyagentos/issues)

When filing a bug, it helps to include:
- Your hardware (e.g. "Orange Pi 5 Plus, 16 GB, Armbian 24.x")
- The TinyAgentOS version (visible in **Settings > System Info**)
- What you expected to happen vs. what actually happened
- Relevant log output from `journalctl -u tinyagentos -n 50`

**Email:** [jaylfc25@gmail.com](mailto:jaylfc25@gmail.com) — for anything that doesn't fit a GitHub issue.

**A note on maturity:** TinyAgentOS is in early development. If something doesn't work, it may genuinely be a bug rather than user error — please do report it. Contributions and hardware test reports are very welcome.

---

## What Next?

Once you've got an agent running and responding over Telegram, here are some things worth exploring:

- **Import** — drop in some PDF or text files to give your agent background knowledge
- **Tasks** — set up a daily scheduled summary or memory cleanup
- **Multiple agents** — deploy a second agent with a different framework or model and compare
- **Image generation** — if you have a supported GPU, install a Stable Diffusion app from the Store and let your agents generate images
- **Memory Browser** — after your agent has had a few conversations, browse what it's remembered

Good luck. If you build something interesting, drop a note in the GitHub issues — the project is always looking for real-world usage stories.
