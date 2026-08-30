FROM python:3.12-slim

WORKDIR /app

# Node.js 20+ is only used to run the optional iptv-org/epg scraper (see
# DPTV_IPTV_ORG_EPG_DIR / README's "Optional: iptv-org/epg scraper") against
# a checkout mounted from the host - Debian's own package is too old
# (the scraper requires >=20.20), hence NodeSource instead of plain apt.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libxml2-dev libxslt1-dev ffmpeg curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
