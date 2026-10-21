### Added
- Windows support in gaming_detector.py: process scanning via psutil, fullscreen detection via Win32 API (GetForegroundWindow, GetWindowRect, MonitorFromWindow, GetMonitorInfoW), and nvidia-smi GPU monitoring
- detect_game_processes() now enumerates processes on Windows using psutil.process_iter with the same _GAME_PATTERNS
- New detect_fullscreen_windows() function checks if foreground window rect matches monitor rect, ignoring shell/desktop windows
- GamingDetector.check() dispatches to detect_fullscreen_windows() on win32, detect_fullscreen_x11() on Linux
- Updated module docstring: Windows now supported (process scan + fullscreen + nvidia-smi VRAM); macOS remains future