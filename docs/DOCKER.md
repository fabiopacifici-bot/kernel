# Docker without Docker Desktop (WSL2)

Kernel can run in a Docker container without Docker Desktop installed — using `dockerd` directly in WSL2.

## Setup (once)

```bash
# Install Docker engine in WSL2 (no Desktop needed)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Start dockerd (or add to ~/.bashrc)
sudo dockerd &
```

## Docker Model Runner (Recommended for new installs)

The simplest way to run Kernel — no local model download needed.

### Pull the model
```bash
docker model pull gemma4
```

This pulls Gemma 4 E2B-it as an OCI artifact from Docker Hub. Works the same as pulling a container image — versioned, cached, portable.

### Run Kernel
```bash
# API server
docker compose --profile docker-model up -d kernel-hub

# Interactive CLI
docker compose --profile docker-model-cli run --rm kernel-hub-cli
```

> **Note:** Full Docker Model Runner integration (running inference via the model runner daemon) is coming in Docker Desktop / dockerd updates over the coming weeks. The current setup pulls the model artifact and mounts it for direct inference.

## Local model mount (existing installs)

## Run as API server

```bash
docker compose up -d kernel
```

Kernel API available at `http://localhost:8769`

## Run as interactive CLI

```bash
docker compose --profile cli run --rm kernel-cli
```

## GPU passthrough

The compose file includes NVIDIA GPU passthrough. Requires `nvidia-container-toolkit`:

```bash
sudo apt install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify:
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

## Model mounting

The model is mounted from `/mnt/e/models/huggingface` (read-only) — no re-download inside the container.

If your model path differs, edit `docker-compose.yml`:
```yaml
volumes:
  - /your/model/path:/models:ro
```

## Without GPU (CPU-only)

Remove the `deploy.resources` section from `docker-compose.yml`. Model will load on CPU — much slower but functional for testing.
