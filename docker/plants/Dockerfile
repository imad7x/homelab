FROM python:3.13-alpine

# Standard library server plus Pillow, which makes the small copies of the
# 3-6 MB phone photos for the grid and the detail view. tzdata lets
# TZ=Asia/Kolkata give the right "today" for the watering log.
RUN apk add --no-cache tzdata && pip install --no-cache-dir pillow==11.3.0

# A copy of the code so the image runs on its own; compose.yaml mounts ./app
# over it read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY app/ /app/

EXPOSE 8087
CMD ["python", "/app/server.py"]
