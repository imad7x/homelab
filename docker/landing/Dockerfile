FROM python:3.13-alpine

# The collector is Python standard library only. A copy of the code so the image
# runs on its own; compose.yaml mounts ./collector over it read-only, so an edit
# only needs a restart, not a rebuild.
WORKDIR /app
COPY collector/ /app/

CMD ["python", "/app/collect.py"]
