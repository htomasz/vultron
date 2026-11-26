# Przypięta wersja i digest (indeks multi-arch): każda instalacja buduje się
# z tej samej bazy. Podbija Dependabot (ekosystem "docker").
FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

LABEL \
    io.hass.name="Vultron" \
    io.hass.description="TotalnieNIEzaawansowana integracja z EduVulcan.pl (Sydharb)" \
    io.hass.arch="amd64|aarch64" \
    io.hass.type="addon" \
    io.hass.version="9.0.0.3" \
    maintainer="htomasz"

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    chromium-chromedriver \
    gcompat \
    libstdc++ \
    bash \
    tzdata \
    sqlite \
    curl

WORKDIR /app

COPY requirements.txt .
# requirements.txt: pełna lista zależności z hashami, wygenerowana z requirements.in:
#   uv pip compile requirements.in --python-version 3.14 --generate-hashes --no-header -o requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir --require-hashes -r requirements.txt

COPY . .

CMD ["python3", "vultron.py"]
