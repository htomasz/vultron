FROM alpine:latest

LABEL \
    io.hass.name="Vultron" \
    io.hass.description="TotalnieNIEzaawansowana integracja z EduVulcan.pl (Oxgang)" \
    io.hass.arch="amd64|aarch64|armv7" \
    io.hass.type="addon" \
    io.hass.version="6.3.1" \
    maintainer="htomasz"

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    chromium-chromedriver \
    xvfb \
    gcompat \
    libstdc++ \
    bash \
    tzdata \
    sqlite \
    curl

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "vultron.py"]
