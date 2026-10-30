FROM python:3.12-slim

LABEL org.opencontainers.image.title="PlexGaps" \
      org.opencontainers.image.description="Lists missing episodes and missing franchise movies in a Plex library" \
      org.opencontainers.image.source="https://github.com/RayMunro/plexgaps" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/RayMunro/plexgaps/main/icon.png" \
      net.unraid.docker.webui="http://[IP]:[PORT:8686]/"

ENV PYTHONUNBUFFERED=1 DATA_DIR=/config
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

VOLUME /config
EXPOSE 8686

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8686/api/status', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8686"]
