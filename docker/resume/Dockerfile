FROM python:3.13-alpine

# Standard library server plus Chromium, which turns a resume page into a PDF
# (headless, run once per download). The fonts the templates use are served
# from app/web/fonts; DejaVu and Noto cover the odd symbol they lack (arrows,
# dashes). tzdata lets TZ=Asia/Kolkata stamp saves with the local time.
RUN apk add --no-cache tzdata chromium font-dejavu font-noto

# A copy of the code so the image runs on its own; compose.yaml mounts ./app
# over it read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY app/ /app/

EXPOSE 8088
CMD ["python", "/app/server.py"]
