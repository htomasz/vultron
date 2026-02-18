FROM alpine:latest

LABEL \
    io.hass.name="Vultron" \
    io.hass.description="Integracja EduVulcan dla Home Assistant" \
    io.hass.arch="amd64|aarch64|armv7" \
    io.hass.type="addon" \
    io.hass.version="4.1" \
    maintainer="htomasz"

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

RUN chmod a+x run.sh
CMD [ "./run.sh" ]
