FROM python:3.12-slim

LABEL \
    io.hass.name="Vultron" \
    io.hass.description="TotalnieNIEzaawansowana integracja z EduVulcan.pl (Friedman Unit)" \
    io.hass.arch="amd64|aarch64|armv7" \
    io.hass.type="addon" \
    io.hass.version="7.0" \
    maintainer="htomasz"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    tzdata \
    libnss3 \
    libatk-bridge2.0-0 \
    libx11-xcb1 \
    libdrm2 \
    libgbm1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "vultron.py"]
