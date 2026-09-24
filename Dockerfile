# ---- stage 1: build FFmpeg 9.0.2 from the official tag ----
FROM debian:bookworm-slim AS ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git pkg-config nasm ca-certificates libgnutls28-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch n9.0.2 https://git.ffmpeg.org/ffmpeg.git /src
WORKDIR /src
# decoders are built in; gnutls is only needed so ffmpeg can open https streams
RUN ./configure --prefix=/opt/ffmpeg --disable-doc --disable-debug --disable-ffplay --disable-ffprobe --enable-gnutls \
 && make -j"$(nproc)" && make install

# ---- stage 2: bot runtime ----
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libopus0 libgnutls30 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
