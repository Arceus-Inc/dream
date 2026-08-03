FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep bash git && \
    rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash dreamuser
USER dreamuser
