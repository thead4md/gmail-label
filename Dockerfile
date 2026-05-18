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
COPY fly-start.sh /fly-start.sh
RUN chmod +x /fly-start.sh

# Streamlit port
EXPOSE 8501

CMD ["/fly-start.sh"]
