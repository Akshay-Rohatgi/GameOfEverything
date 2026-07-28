FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools (same set as TestEnvironmentTool bootstrap) + Apache / PHP / extensions.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg lsb-release \
        iproute2 net-tools procps iputils-ping sudo sshpass \
        apache2 libapache2-mod-php php php-mysql php-sqlite3 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
