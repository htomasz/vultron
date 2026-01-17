FROM alpine:latest

RUN apk update && apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    chromium-chromedriver \
    xvfb \
    gcompat \
    libstdc++ \
    bash \
    tzdata \
    sqlite && \
    pip3 install --break-system-packages --no-cache-dir \
    selenium \
    websocket-client \
    pyvirtualdisplay \
    requests

ENV TZ=Europe/Warsaw

WORKDIR /app
COPY . .

RUN chmod a+x run.sh
CMD [ "./run.sh" ]
