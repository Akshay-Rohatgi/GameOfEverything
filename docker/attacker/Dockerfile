FROM kalilinux/kali-rolling

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update repos and install core offensive toolset.
# Covers all current atoms (SMB, SSH, Redis, PostgreSQL, MySQL, FTP, web, recon).
# Additional tools are one `apt install` away — no custom repos needed for most.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        smbclient \
        sshpass \
        openssh-client \
        nmap \
        hydra \
        curl \
        wget \
        netcat-traditional \
        ncat \
        metasploit-framework \
        redis-tools \
        postgresql-client \
        default-mysql-client \
        ftp \
        dnsutils \
        nikto \
        enum4linux \
        gnupg \
        wget \
        unzip \
        tar \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install mongosh via direct binary download from MongoDB's download CDN.
# This avoids external apt repo configuration entirely (no GPG keys, no version-locked
# repo URLs, no Kali/Debian compatibility issues). MongoDB's CDN retains all releases
# so pinned versions stay downloadable. To upgrade: bump MONGOSH_VERSION.
# Needed by: mongodb_disable_auth, database_expose atoms.
ARG MONGOSH_VERSION=2.3.1
RUN wget -q "https://downloads.mongodb.com/compass/mongosh-${MONGOSH_VERSION}-linux-x64.tgz" \
        -O /tmp/mongosh.tgz \
    && tar -xzf /tmp/mongosh.tgz -C /tmp \
    && mv "/tmp/mongosh-${MONGOSH_VERSION}-linux-x64/bin/mongosh" /usr/local/bin/mongosh \
    && chmod +x /usr/local/bin/mongosh \
    && rm -rf /tmp/mongosh* \
    && mongosh --version

# Default to bash for exec usage
CMD ["sleep", "infinity"]
