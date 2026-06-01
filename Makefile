# TinyAgentOS build targets

.PHONY: help docker docker-all electron electron-mac electron-win electron-linux
.PHONY: deb deb-simple os-image os-image-board spa-dev spa-build
.PHONY: compose-up compose-down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

docker: ## Build Docker image (linux/amd64)
	scripts/build-docker.sh

docker-all: ## Build Docker image (multi-arch)
	scripts/build-docker.sh "linux/amd64,linux/arm64"

electron: ## Build Electron app for current platform
	scripts/build-electron.sh

electron-mac: ## Build Electron DMG for macOS
	scripts/build-electron.sh --mac

electron-win: ## Build Electron EXE+MSI for Windows
	scripts/build-electron.sh --win

electron-linux: ## Build Electron AppImage for Linux
	scripts/build-electron.sh --linux

deb: ## Build .deb package
	scripts/build-deb.sh

os-image: ## Build SBC OS image (default: orangepi5plus)
	cd os-build && sudo ./build.sh

os-image-board: ## Build SBC OS image for specific board: make os-image-board B=rock5b
	cd os-build && sudo ./build.sh $(B)

spa-dev: ## Start SPA dev server with hot-reload
	cd desktop && npm run dev

spa-build: ## Build SPA frontend only
	cd desktop && npm ci --silent && npm run build

compose-up: ## Start full stack with docker compose
	docker compose up -d

compose-down: ## Stop docker compose
	docker compose down

clean: ## Clean build artifacts
	rm -rf electron/dist electron/out
	rm -rf static/desktop
	rm -f tinyagentos_*.deb
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
