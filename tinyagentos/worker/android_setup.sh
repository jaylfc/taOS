#!/data/data/com.termux/files/usr/bin/bash
# TinyAgentOS Android Worker Setup
# Run in Termux: curl -sL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/tinyagentos/worker/android_setup.sh | bash

set -e
echo "======================================"
echo "  TinyAgentOS Android Worker Setup"
echo "======================================"

# Install deps
pkg update -y
pkg install -y python git cmake make clang

# Install llama.cpp
echo "Building llama.cpp..."
git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build
cmake --build build --config Release -j$(nproc)
echo "llama.cpp built successfully"

# Install TinyAgentOS worker
pip install httpx psutil

# Create worker script
cat > ~/tinyagentos-worker.py << 'PYEOF'
#!/data/data/com.termux/files/usr/bin/python
"""TinyAgentOS Android Worker — connects this phone to a TinyAgentOS cluster."""
import argparse
import asyncio
import json
import logging
import os
import platform
import socket
import subprocess
import time
import httpx
import psutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

class AndroidWorker:
    def __init__(self, controller_url, name=None, llama_port=8080):
        self.controller_url = controller_url.rstrip("/")
        self.name = name or f"android-{socket.gethostname()}"
        self.llama_port = llama_port
        self.llama_process = None
        self.running = False
        self.registered = False

    def detect_hardware(self):
        mem = psutil.virtual_memory()
        return {
            "cpu": {
                "arch": platform.machine(),
                "model": platform.processor() or "ARM",
                "cores": os.cpu_count() or 1,
                "soc": "",
            },
            "ram_mb": mem.total // (1024 * 1024),
            "npu": {"type": "none", "device": "", "tops": 0, "cores": 0},
            "gpu": {"type": "mobile", "model": "Mobile GPU (Vulkan/OpenCL)", "vram_mb": 0,
                    "vulkan": True, "cuda": False, "rocm": False},
            "disk": {"total_gb": 0, "free_gb": 0, "type": "flash"},
            "os": {"distro": "android-termux", "version": platform.version(), "kernel": platform.release()},
        }

    async def start_llama_server(self, model_path):
        """Start llama.cpp server with a model."""
        cmd = [
            os.path.expanduser("~/llama.cpp/build/bin/llama-server"),
            "-m", model_path,
            "--host", "0.0.0.0",
            "--port", str(self.llama_port),
            "-ngl", "0",  # CPU only by default
        ]
        logger.info(f"Starting llama.cpp server: {' '.join(cmd)}")
        self.llama_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(3)  # Wait for server to start
        return self.llama_process.poll() is None

    async def register(self):
        hw = self.detect_hardware()
        payload = {
            "name": self.name,
            "url": f"http://{self._get_ip()}:{self.llama_port}",
            "hardware": hw,
            "backends": [{"type": "llama-cpp", "url": f"http://localhost:{self.llama_port}"}],
            "capabilities": ["chat", "embed"],
            "platform": "android",
            "models": [],
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self.controller_url}/api/cluster/workers", json=payload)
                resp.raise_for_status()
                self.registered = True
                logger.info(f"Registered as '{self.name}'")
                return True
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            return False

    async def heartbeat(self):
        try:
            load = psutil.cpu_percent() / 100.0
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{self.controller_url}/api/cluster/heartbeat",
                                  json={"name": self.name, "load": load})
        except Exception:
            pass

    def _get_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def run(self):
        self.running = True
        while self.running and not self.registered:
            if await self.register():
                break
            await asyncio.sleep(5)
        while self.running:
            await self.heartbeat()
            await asyncio.sleep(5)

    def stop(self):
        self.running = False
        if self.llama_process:
            self.llama_process.terminate()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TinyAgentOS Android Worker")
    parser.add_argument("controller", help="Controller URL (e.g. http://192.168.1.100:6969)")
    parser.add_argument("--name", help="Worker name")
    parser.add_argument("--model", help="Path to GGUF model file")
    parser.add_argument("--port", type=int, default=8080, help="llama.cpp server port")
    args = parser.parse_args()

    worker = AndroidWorker(args.controller, args.name, args.port)

    async def main():
        if args.model:
            if not await worker.start_llama_server(args.model):
                logger.error("Failed to start llama.cpp server")
                return
        await worker.run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        worker.stop()
PYEOF

chmod +x ~/tinyagentos-worker.py

echo ""
echo "======================================"
echo "  Setup complete!"
echo "======================================"
echo ""
echo "  Download a model:"
echo "    wget -O ~/model.gguf https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/qwen3-1.7b-q4_k_m.gguf"
echo ""
echo "  Start worker:"
echo "    python ~/tinyagentos-worker.py http://YOUR-SERVER:6969 --model ~/model.gguf"
echo ""
