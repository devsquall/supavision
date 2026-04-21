FROM python:3.12-slim

WORKDIR /app

# Install system deps (SSH client for remote monitoring)
RUN apt-get update && \
    apt-get install -y --no-install-recommends openssh-client curl && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js + Claude Code CLI
# Pinned to major version 2 — bump after testing against new Claude Code releases
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code@^2 && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Data directory (templates + scanner patterns are inside the package)
VOLUME /app/.supavision

ENV SUPAVISION_BACKEND=claude_cli
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/api/v1/health || exit 1

ENTRYPOINT ["supavision"]
CMD ["serve", "--port", "8080"]
