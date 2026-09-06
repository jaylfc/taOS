# Agent Lobby & Chat Room — Design Spec (Internal/Demo Only)

**Date:** 2026-04-05
**Status:** Approved
**Purpose:** Marketing demo — 100+ agents discussing TinyAgentOS on a single Orange Pi, with inference on remote Fedora box.

## Overview

A `/lobby` page that spawns 100+ lightweight agent tasks, each with a unique name/role/avatar, running a moderated group chat. Completely isolated from the main platform — no database writes, no config changes, lives entirely in `tinyagentos/lobby/`.

## Architecture

```
Pi (16GB, orchestrator only)          Fedora (77GB RAM, RTX 3060)
┌─────────────────────────┐          ┌──────────────────────┐
│  TinyAgentOS            │          │  ollama              │
│  ├── /lobby page        │  HTTP    │  └── qwen3.5:9b      │
│  ├── Moderator          │ ──────── │     (or faster model) │
│  ├── 100+ agent tasks   │          │                      │
│  └── SSE chat stream    │          └──────────────────────┘
└─────────────────────────┘
```

RAM on Pi: ~350MB total (platform + 100 async tasks). All inference on Fedora.

## Components

### 1. Agent Definitions (`lobby/agents.py`)
- List of 100+ agents, each with: name, role, system_prompt, avatar_seed
- Roles like: "Skeptical Sysadmin", "Budget Builder", "Mac Mini Convert", "First-time User", "Raspberry Pi Fan", "Home Lab Enthusiast", "Enterprise Architect", "Open Source Advocate", etc.
- Human names: Marcus, Priya, Dave, Aisha, Tom, etc.
- System prompts tailor personality to role and direct them to discuss TinyAgentOS

### 2. Chat Room Engine (`lobby/chat.py`)
- In-memory message list (no database)
- Moderator picks 3-5 agents per round based on recency (who hasn't spoken recently)
- Sends recent chat context + agent's system prompt to the inference backend
- Collects response, adds to message list
- Configurable: topic, num_agents, backend_url, rounds, agents_per_round

### 3. Lobby Routes (`lobby/routes.py`)
- `GET /lobby` — config page: set topic, agent count, backend URL, start button
- `GET /lobby/chat` — the live chat room page with SSE stream
- `GET /lobby/stream` — SSE endpoint streaming new messages as they arrive
- `POST /lobby/start` — starts the chat session
- `POST /lobby/stop` — stops the session

### 4. Chat Template (`lobby/templates/chat.html`)
- Full-screen dark chat interface (not using base.html — standalone)
- Messages appear in real-time via SSE/EventSource
- Each message: avatar (SVG) + name + role badge + text + timestamp
- Auto-scrolls to bottom
- Header shows: agent count, messages/min, topic, stop button
- Stats bar: "100 agents active · 47 messages · Running for 3m 12s"

### 5. Avatar Generator (`lobby/avatars.py`)
- Deterministic SVG from name hash
- Generates a coloured circle with initials, unique colour per agent
- Returns inline SVG string, no external files

## Isolation

- Everything in `tinyagentos/lobby/` — one directory
- Router mounted only when accessed, no startup cost
- No database tables, no config file changes
- No impact on metrics, notifications, health monitor
- To remove: delete `tinyagentos/lobby/` and remove the router include from app.py

## Agent Name/Role Examples (first 20 of 100+)

| Name | Role | Personality |
|------|------|-------------|
| Marcus | Skeptical Sysadmin | "Show me the benchmarks" |
| Priya | First-time Builder | Just bought an Orange Pi, excited |
| Dave | Mac Mini Convert | Switched from Mac Mini, compares constantly |
| Aisha | Enterprise Architect | Thinks about scale and security |
| Tom | Budget Builder | Everything about price/performance |
| Sofia | Open Source Advocate | Loves that it's MIT licensed |
| James | Home Lab Enthusiast | Runs 20 services already |
| Yuki | AI Researcher | Interested in the model ecosystem |
| Carlos | Privacy Maximalist | Loves local-first, no cloud |
| Emma | Raspberry Pi Fan | Wants Pi support, asks about HATs |
| Raj | Discord Bot Developer | Runs agents in Discord channels |
| Olga | Data Scientist | Interested in memory/RAG quality |
| Alex | Hardware Reviewer | Compares SBCs objectively |
| Chen | DevOps Engineer | Asks about CI/CD, deployment |
| Nina | Student | Learning about AI agents on a budget |
| Kwame | IoT Developer | Wants agents controlling smart home |
| Liam | Content Creator | Wants agents for social media |
| Fatima | Security Researcher | Probes for vulnerabilities |
| Max | Minimalist | Loves picoclaw, smallest possible setup |
| Zara | Community Manager | Thinks about growing the ecosystem |
