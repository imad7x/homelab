FROM python:3.12-slim

# poppler-utils gives `pdftotext -layout`, which preserves column alignment in
# bank statement tables far better than any pure-Python extractor. Kept
# alongside pdfplumber because different issuers respond better to each.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "ingest"]
