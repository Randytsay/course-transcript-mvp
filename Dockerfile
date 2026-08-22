FROM python:3.12-slim

ARG TARGETARCH
ARG RCLONE_VERSION=1.74.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg unzip \
    && case "${TARGETARCH}" in amd64|arm64) RCLONE_ARCH="${TARGETARCH}" ;; *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; esac \
    && curl --fail --location --silent --show-error \
      "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-${RCLONE_ARCH}.zip" \
      --output /tmp/rclone.zip \
    && unzip -q /tmp/rclone.zip -d /tmp \
    && install -m 0755 "/tmp/rclone-v${RCLONE_VERSION}-linux-${RCLONE_ARCH}/rclone" /usr/local/bin/rclone \
    && env -u RCLONE_VERSION rclone version \
    && rm -rf /tmp/rclone.zip "/tmp/rclone-v${RCLONE_VERSION}-linux-${RCLONE_ARCH}" /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests

CMD ["python", "-m", "app.infrastructure_test"]
