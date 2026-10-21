FROM python:3.10-slim

# সিস্টেম ডিপেন্ডেন্সি যেমন Tesseract OCR এবং Poppler ইনস্টল করা
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD uvicorn server_ai:app --host 0.0.0.0 --port ${PORT:-8000}
