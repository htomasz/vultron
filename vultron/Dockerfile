FROM alpine:latest

LABEL \
    io.hass.name="Vultron" \
    io.hass.description="Integracja EduVulcan dla Home Assistant" \
    io.hass.arch="amd64|aarch64|armv7" \
    io.hass.type="addon" \
    io.hass.version="4.2" \
    maintainer="htomasz"

# Wyłączenie buforowania logów Pythona (logi pojawią się natychmiast)
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

# Poprawiony format CMD dla lepszej obsługi sygnałów
CMD ["python3", "vultron.py"]
