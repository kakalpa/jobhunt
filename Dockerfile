FROM python:3.12-slim-bookworm

LABEL maintainer="Kalpa Prabashwera"
LABEL description="Job Hunt Command Center - Autonomous IT Job Scouting, AI Tailoring & Pipeline Management"

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV WORKSPACE_DIR=/workspace
ENV CHROMIUM_PATH=/usr/bin/chromium
ENV PORT=5500

# Install headless Chromium, core system fonts, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-liberation \
    fonts-dejavu-core \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application assets, templates, and scripts
COPY dashboard /app/dashboard
COPY scripts /app/scripts
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
COPY Base_CV.md /app/Base_CV.md
COPY APPLICATIONS_TRACKER.md /app/APPLICATIONS_TRACKER.md
COPY pipeline_data.json /app/pipeline_data.json

RUN chmod +x /app/docker-entrypoint.sh

# Persist application documents, scout reports, and packages
VOLUME ["/workspace"]

EXPOSE 5500

ENTRYPOINT ["/app/docker-entrypoint.sh"]
