FROM python:3.12-slim

RUN pip install --no-cache-dir psycopg2-binary==2.9.10

# A copy of the code so the image runs on its own; compose mounts ./app over it
# read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY . /app/

EXPOSE 8085
CMD ["python", "/app/server.py"]
