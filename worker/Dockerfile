
FROM python:3.10-slim

# FFmpeg is needed for MP3 processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "demucs[quantized]"

COPY worker-server.py .

ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=1

CMD ["python", "worker-server.py"]
