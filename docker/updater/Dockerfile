FROM python:3.13-alpine

# The app itself is Python standard library only; all it adds is the docker
# CLI and the compose plugin, pinned to the host's own versions (Engine
# 28.5.1, Compose v2.40.0) so `docker compose` inside the container reads
# the compose files and computes config hashes exactly as it does on the host.
# Both are static binaries, so they run on Alpine as they are.
COPY --from=docker:28.5.1-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker/compose-bin:v2.40.0 /docker-compose /usr/local/lib/docker/cli-plugins/docker-compose

# A copy of the code so the image runs on its own; compose.yaml mounts ./app
# over it read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY app/ /app/

EXPOSE 8084
CMD ["python", "/app/server.py"]
