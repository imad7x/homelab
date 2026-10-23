FROM python:3.13-alpine

# Standard library only. tzdata lets TZ=Asia/Kolkata keep market hours right.
RUN apk add --no-cache tzdata

# A copy of the code so the image runs on its own; compose.yaml mounts ./app
# over it read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY app/ /app/

EXPOSE 8091
CMD ["python", "/app/server.py"]
