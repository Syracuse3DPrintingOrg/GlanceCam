FROM python:3.12-slim

WORKDIR /srv/service

# ca-certificates is needed for outbound HTTPS (snapshot fetches from cameras
# that serve stills over TLS). No other system packages: the app shells out to
# nothing and go2rtc runs as its own container/service.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /srv/service/data

# Production default: no --reload, code is baked into the image. The dev
# docker-compose.yml overrides this command to add --reload alongside a volume
# mount of ./service/app so a pull applies changes live.
# --no-proxy-headers: the app derives the client purely from request.client and
# never reads X-Forwarded-For, but it grants full trust to a loopback client
# (the password-free local kiosk). Without this flag uvicorn would rewrite
# request.client.host from a spoofable X-Forwarded-For when a same-host proxy
# connects over loopback, turning that header into an auth bypass. Disable it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
