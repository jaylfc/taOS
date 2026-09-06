# Fedora / Debian LXC Setup for GPU Workers

This document covers running a TAOS GPU worker inside an Incus LXC container on
a Linux host. It also documents the CUDA 12.8/12.9 + glibc 2.42 build conflict
on Fedora 43, and the Debian 12 workaround we use in production today.

## Host requirements

- Incus 6.x (or LXD 5.x) installed on the host
- NVIDIA driver installed on the host (the container borrows it via bind-mount)
- `/etc/subuid` and `/etc/subgid` with a root entry so unprivileged containers
  can map UIDs:

  ```
  root:1000000:1000000
  ```

  If either file is missing that entry, add it and restart Incus:

  ```bash
  sudo systemctl restart incus
  ```

## Container flavour: pick Debian, not Fedora (for now)

We tried Fedora 43 first because the host is Fedora. It does not work with CUDA
12.8 or 12.9 today. Details in the "CUDA build blocker" section below. For a
GPU worker, use Debian 12.

```bash
sudo incus launch images:debian/12 taos-debian-cuda
```

Leave it unprivileged. The NVIDIA runtime bind-mount rejects privileged
containers outright:

```
Error: nvidia.runtime is incompatible with privileged containers
```

## Wire the GPU into the container

```bash
sudo incus config set taos-debian-cuda nvidia.runtime true
sudo incus config set taos-debian-cuda nvidia.driver.capabilities all
sudo incus config device add taos-debian-cuda gpu gpu
sudo incus restart taos-debian-cuda
```

Verify inside the container:

```bash
sudo incus exec taos-debian-cuda -- nvidia-smi
```

If `nvidia-smi` shows the card, the runtime is wired correctly.

## CUDA toolkit install (Debian 12)

Debian 12 ships glibc 2.36, which is old enough to work with the CUDA 12.9
toolkit headers out of the box.

```bash
sudo incus exec taos-debian-cuda -- bash -lc '
  apt-get update
  apt-get install -y wget gnupg build-essential git cmake
  wget https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i cuda-keyring_1.1-1_all.deb
  apt-get update
  apt-get install -y cuda-toolkit-12-9
'
```

Add CUDA to PATH in the container's shell profile:

```bash
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
```

## Build llama-cpp-turboquant with CUDA

```bash
git clone https://github.com/TheTom/llama-cpp-turboquant.git
cd llama-cpp-turboquant
git checkout tqp-v0.1.0
cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)
```

Benchmark it on Qwen3.5-9B-Q4_K_M to confirm TurboQuant K/V flags are honoured:

```bash
./build/bin/llama-bench -m models/qwen3.5-9b-q4_k_m.gguf -ctk q8_0 -ctv turbo3 -c 32768
```

## CUDA build blocker on Fedora 43 (why we use Debian)

Fedora 43 ships **glibc 2.42**. The CUDA 12.8 and 12.9 `math_functions.h`
headers declare `cospi`, `sinpi`, `rsqrt` (and friends) without `noexcept`,
while glibc 2.42 declares the same symbols *with* `noexcept`. The two
declarations conflict and `nvcc` fails with:

```
/usr/local/cuda/include/crt/math_functions.h:XXXX:YY: error:
  exception specification is incompatible with that of previous
  declaration 'double cospi(double) noexcept'
```

Workarounds we tried and rejected:

1. **Patch the headers inline** - adds `noexcept` to each affected
   declaration. Works for the first four symbols, then a new set of
   conflicts appears (gamma, lgamma, erf variants). Whack-a-mole.
2. **`-fpermissive` / `--diag-suppress`** - does not help, the error is
   in the `nvcc` host front-end C++ parser, not the diagnostic layer.
3. **Pull CUDA 12.9 from NVIDIA's own repo** - same headers, same issue.

What works:

- **Debian 12 unprivileged container with glibc 2.36** - the C++ header
  declaration matches what CUDA expects and the build completes cleanly.
- We keep a dedicated `taos-debian-cuda` container for builds and
  benchmarks, and the live GPU worker runs in the same container.

Upstream fix tracking: NVIDIA is aware of the glibc 2.42 incompatibility.
Once CUDA ships a toolkit whose `math_functions.h` matches modern glibc's
`noexcept`-qualified symbols, Fedora 43 will build cleanly. Until then,
**use Debian 12 for any CUDA-dependent worker**.

## Running the TAOS worker inside the container

Once the build works, install the worker agent:

```bash
sudo incus exec taos-debian-cuda -- bash -lc '
  curl -sSL https://raw.githubusercontent.com/jaylfc/tinyagentos/master/scripts/install-worker.sh | bash -s -- --controller http://<controller-lan-ip>:6969
'
```

The installer drops a `tinyagentos-worker.service` unit under systemd and
starts it. Check it registered with the controller:

```bash
curl http://<controller-lan-ip>:6969/api/cluster/workers | jq '.[].name'
```

## Troubleshooting

- **`nvidia.runtime is incompatible with privileged containers`** - recreate
  as unprivileged (`sudo incus launch images:debian/12 taos-<name>`). Do not
  try to toggle `security.privileged`; the NVIDIA integration uses user
  namespace mapping that a privileged container cannot provide.

- **Container won't start, "newuidmap failed"** - `/etc/subuid` or
  `/etc/subgid` missing a root mapping. Add `root:1000000:1000000` to both
  and restart Incus.

- **`nvidia-smi` works on the host but not inside the container** - check
  `sudo incus config show <name>` contains both `nvidia.runtime: true` and
  the `gpu` device. Restart the container after adding either.

- **`nvcc` builds on Fedora 43 despite this doc's warning** - you are
  probably on an older CUDA (<=12.5). That works, but you won't have the
  TurboQuant kernels. Use CUDA 12.8+ in a Debian container.
