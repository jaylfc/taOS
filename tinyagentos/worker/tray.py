from __future__ import annotations
import asyncio
import sys
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def create_icon_image(color=(76, 175, 80)):
    """Create a simple colored circle icon."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=color)
    draw.text((18, 18), "T", fill="white")
    return img


def run_tray(controller_url: str, name: str | None = None):
    """Run the worker as a system tray application."""
    import pystray
    from pystray import MenuItem as Item
    from tinyagentos.worker.agent import WorkerAgent

    agent = WorkerAgent(controller_url, name)
    loop = asyncio.new_event_loop()
    worker_thread = None
    status = {"connected": False, "text": "Connecting..."}

    def start_worker():
        nonlocal worker_thread

        def run():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(agent.run())

        worker_thread = threading.Thread(target=run, daemon=True)
        worker_thread.start()
        status["connected"] = True
        status["text"] = f"Connected to {controller_url}"

    def stop_worker(icon):
        agent.stop()
        icon.stop()

    def get_status():
        return f"TinyAgentOS Worker: {agent.name}\n{status['text']}"

    icon = pystray.Icon(
        "tinyagentos-worker",
        create_icon_image(),
        "TinyAgentOS Worker",
        menu=pystray.Menu(
            Item(lambda text: f"Worker: {agent.name}", None, enabled=False),
            Item(lambda text: status["text"], None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Quit", stop_worker),
        ),
    )

    # macOS: hide dock icon
    if sys.platform == "darwin":
        try:
            import AppKit
            info = AppKit.NSBundle.mainBundle().infoDictionary()
            info["LSBackgroundOnly"] = "1"
        except ImportError:
            pass

    start_worker()
    icon.run()
