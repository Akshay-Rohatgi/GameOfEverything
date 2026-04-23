FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools (same set as TestEnvironmentTool bootstrap) + Node.js 20 via NodeSource.
# Ubuntu 22.04 apt ships Node.js v12 which is EOL and incompatible with modern
# npm packages (Express 5, better-sqlite3, Puppeteer all require v18+).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg lsb-release \
        iproute2 net-tools procps iputils-ping sudo sshpass && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
