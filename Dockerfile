FROM python:3.11-slim

# System deps for audio + build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (torch excluded — mount from host or install separately)
COPY requirements.txt .
RUN pip install --no-cache-dir \
    fastapi uvicorn pyyaml requests sounddevice psutil \
    transformers>=5.5.0 \
    accelerate

# Copy source
COPY src/ ./src/
COPY skills/ ./skills/
COPY routines/ ./routines/
COPY config.yaml .
COPY microclaw .
RUN chmod +x microclaw

# Model dir will be mounted from host — no download in image
ENV HF_HOME=/models
ENV TRANSFORMERS_CACHE=/models

# Default: API mode (chat via HTTP)
EXPOSE 8769
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8769"]
