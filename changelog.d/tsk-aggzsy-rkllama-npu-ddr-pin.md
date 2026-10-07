### Fixed

- The RK NPU installer now pins rkllama 2faa12f1: at startup it pins only the NPU and DDR clocks, so the CPU can scale and idle (set RKLLAMA_PIN_CPU=1 / RKLLAMA_PIN_GPU=1 on rkllama.service to restore the old pins).
