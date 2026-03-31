FROM python:3.12-slim

WORKDIR /app

# Install system deps (SSH client for remote monitoring)
RUN apt-get update && \
    apt-get install -y --no-install-recommends openssh-client curl && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js + Claude Code CLI
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Copy templates
COPY templates/ templates/

# Data directory
VOLUME /app/.supervisor

EXPOSE 8080

ENTRYPOINT ["supervisor"]
CMD ["serve", "--port", "8080"]
