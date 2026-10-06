FROM python:3.13-alpine

# The app is Python standard library only (http.server + sqlite3). tzdata lets
# TZ=Asia/Kolkata put watch data on the right local day.
RUN apk add --no-cache tzdata

# A copy of the code so the image runs on its own; compose.yaml mounts ./app
# over it read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY app/ /app/

EXPOSE 8086
CMD ["python", "/app/server.py"]
