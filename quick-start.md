d ~/Documents/GitHub/tinyagentos
source .venv/bin/activate
nohup python -m uvicorn tinyagentos.app:create_app --factory --host 0.0.0.0 --port 6969 > /tmp/tinyagentos.log 2>&1 &