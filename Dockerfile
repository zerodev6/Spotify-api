FROM python:3.11-slim

# ffmpeg + nodejs needed by yt-dlp for JS-based signature solving
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg nodejs npm ca-certificates \
 && pip install --no-cache-dir -U yt-dlp \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
