# j5-browser: j5 with a Chromium window on a KasmVNC virtual display.
#
# Starts on demand, runs one j5 script from /job, writes its results to /out and exits
# with j5's exit code; or keeps the desktop up, viewable in a web browser, to write and
# debug scripts. See docs/container.md.
#
# Build from the repository root (j5 is installed from this checkout):
#
#   docker build -f docker/j5-browser/Dockerfile -t j5-browser .

ARG BASE_IMAGE=debian:trixie-slim
FROM ${BASE_IMAGE}

# KasmVNC is not packaged by Debian: install the release .deb, checked against its hash.
ARG KASMVNC_VERSION=1.5.0
ARG KASMVNC_SHA256_AMD64=80b241de7dfe53bba2b7e1cc5ac8c5246d72271efa16be2d4f76607f30fab1c4
ARG KASMVNC_SHA256_ARM64=fbb11589958a2acccd2d67f67944be79ac1e8e3a1d6172c0e6db6dc59e55a919
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    case "${TARGETARCH:-amd64}" in \
      amd64) sha="${KASMVNC_SHA256_AMD64}" ;; \
      arm64) sha="${KASMVNC_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    deb="/tmp/kasmvncserver_trixie_${KASMVNC_VERSION}_${TARGETARCH:-amd64}.deb"; \
    curl -fsSL -o "$deb" \
      "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/$(basename "$deb")"; \
    echo "${sha}  ${deb}" | sha256sum -c -; \
    apt-get install -y --no-install-recommends \
      "$deb" \
      chromium \
      openbox \
      tini \
      x11-utils \
      xclip \
      fonts-dejavu-core \
      fonts-liberation \
      fonts-noto-color-emoji \
      python3 \
      python3-venv \
      python3-tk \
      libx11-6 libxrandr2 libxfixes3 libxext6; \
    rm -f "$deb"; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

# j5, from this checkout, in its own virtual environment. --system-site-packages so the
# venv sees Debian's tkinter, which j5 capture and j5 pick draw their overlay with.
COPY pyproject.toml README.md LICENSE /tmp/j5/
COPY src /tmp/j5/src
RUN set -eux; \
    python3 -m venv --system-site-packages /opt/j5; \
    /opt/j5/bin/pip install --no-cache-dir "/tmp/j5[x11,vision]"; \
    rm -rf /tmp/j5; \
    /opt/j5/bin/j5 --version

COPY docker/j5-browser/rootfs/ /

RUN set -eux; \
    useradd --create-home --uid 1000 --shell /bin/bash j5; \
    mkdir -p /job /out; \
    chown j5:j5 /job /out; \
    install -d -m 1777 /tmp/.X11-unix; \
    chmod 0755 /usr/local/bin/j5-entrypoint /usr/local/bin/j5-chromium

ENV PATH=/opt/j5/bin:$PATH \
    DISPLAY=:1 \
    GEOMETRY=1280x800 \
    VNC_PORT=6901 \
    LANG=C.UTF-8 \
    TZ=UTC \
    J5_RUNS_DIR=/out/runs

USER j5
WORKDIR /home/j5
EXPOSE 6901

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/j5-entrypoint"]
CMD ["run"]
