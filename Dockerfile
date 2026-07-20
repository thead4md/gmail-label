FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim

# Install system deps + Litestream
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && curl -fsSL https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz \
    | tar -xz -C /usr/local/bin \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY mailmind/ ./mailmind/
COPY litestream.yml /etc/litestream.yml
COPY mailmind/fly-start.sh /fly-start.sh
RUN chmod +x /fly-start.sh

# Built React SPA — served by mailmind/api/main.py's StaticFiles mount +
# catch-all route.
COPY --from=frontend-builder /app/frontend/dist ./mailmind/api/static

# Default to the Fly worker startup
CMD ["/fly-start.sh"]
