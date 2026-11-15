FROM python:3.12-slim

# ffmpeg / ffprobe cut the videos into segments and thumbnails (Debian's build has libx264)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py library.py live.py ./
COPY public ./public
# the layer for a console on top (see DEPLOY.md); not used by this service itself
COPY console ./console

# videos are mounted at /media (read-only is enough); what is built from them is kept in /data
RUN useradd --uid 10001 --no-create-home app && mkdir -p /media /data && chown app /data
USER app

ENV HOST=0.0.0.0 \
    PORT=8790 \
    MEDIA_DIR=/media \
    DATA_DIR=/data
EXPOSE 8790

# One process: the build jobs for DATA_DIR run inside it (replicas: 1).
CMD ["python", "app.py"]
