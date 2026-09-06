# TinyAgentOS Worker

Cross-platform worker app that connects a machine's compute resources to a TinyAgentOS controller. Runs as a system tray icon on desktops or headless on servers.

## Installation

```bash
# From PyPI (when published)
pip install tinyagentos[worker]

# From source
cd tinyagentos
pip install -e ".[worker]"
```

The `worker` extra installs `pystray` and `Pillow` for the system tray GUI. These are not needed for headless mode.

## Usage

### Desktop (system tray)

```bash
python -m tinyagentos.worker http://your-server:6969
```

This places a small green icon in the notification area. The menu shows the worker name and connection status, with a Quit option.

### Headless (servers, Raspberry Pi, etc.)

```bash
python -m tinyagentos.worker http://your-server:6969 --headless
```

No GUI dependencies required. Runs until interrupted with Ctrl+C.

### Custom worker name

```bash
python -m tinyagentos.worker http://your-server:6969 --name "gpu-box-01"
```

Default name is the machine's hostname.

## What it does

1. **Detects hardware** — CPU, GPU, NPU, RAM via `tinyagentos.hardware`
2. **Discovers backends** — probes standard ports for Ollama (11434), rkllama (8080), llama.cpp (8080), vLLM (8000)
3. **Registers** with the controller via `POST /api/cluster/workers`
4. **Heartbeats** every 5 seconds via `POST /api/cluster/heartbeat` with CPU load

## Standalone binaries

Build a single-file executable with PyInstaller:

```bash
bash scripts/build-worker.sh
```

The output goes to `dist/tinyagentos-worker-linux` (or platform equivalent). Distribute this file — no Python installation required on the target machine.

## Platform notes

### macOS

- The tray app hides itself from the Dock automatically (LSBackgroundOnly)
- Requires `pyobjc-framework-Cocoa` if you want guaranteed Dock hiding; falls back gracefully without it
- Build with py2app for a native `.app` bundle

### Windows

- Works with the default Windows notification area
- To run at startup, add a shortcut to `shell:startup` or use Task Scheduler
- Build with `pyinstaller --noconsole` for a windowless executable

### Linux

- Requires a system tray (most desktop environments have one)
- On GNOME, install the AppIndicator extension
- For autostart, add a `.desktop` file to `~/.config/autostart/`
- Headless mode works on any Linux system including servers and SBCs

### Android (Termux)

Android phones can join the cluster as compute workers via Termux. The setup
script installs llama.cpp, builds it from source, and creates a standalone
worker script:

```bash
# In Termux:
curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/tinyagentos/worker/android_setup.sh | bash

# Download a small model:
wget -O ~/model.gguf https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/qwen3-1.7b-q4_k_m.gguf

# Start the worker:
python ~/tinyagentos-worker.py http://YOUR-SERVER:6969 --model ~/model.gguf
```

The worker registers with platform `android` and runs llama.cpp in CPU mode.
See `android_setup.sh` for the full setup script.

### iOS

iOS does not support background inference servers. See `ios_guide.md` for
options including using LLM apps with Shortcuts automation and using an
iPad/iPhone as a dashboard client via the PWA web interface.
