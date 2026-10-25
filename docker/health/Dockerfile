FROM python:3.12-slim

# poppler-utils reads the reports (pdftotext) and renders pages (pdftoppm);
# tesseract reads scanned pages that have no text layer. tzdata for IST.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng tzdata \
 && rm -rf /var/lib/apt/lists/*

# A copy of the code so the image runs on its own; compose.yaml mounts ./app
# over it read-only, so an edit only needs a restart, not a rebuild.
WORKDIR /app
COPY app/ /app/

EXPOSE 8090
CMD ["python", "/app/server.py"]
