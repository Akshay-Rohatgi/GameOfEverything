FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools + Node.js 20 via NodeSource.
# Ubuntu 22.04 apt ships Node.js v12 which is EOL and incompatible with modern
# npm packages (Express 5, better-sqlite3, Puppeteer all require v18+).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg lsb-release \
        software-properties-common \
        iproute2 net-tools procps iputils-ping sudo sshpass && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Chromium via xtradeb PPA — Ubuntu 22.04's chromium-browser is a snap stub
# that does not work in Docker. xtradeb ships a real native .deb package.
RUN add-apt-repository ppa:xtradeb/apps -y && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        libnspr4 \
        libnss3 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
