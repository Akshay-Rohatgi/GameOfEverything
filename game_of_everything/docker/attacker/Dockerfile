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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install mongosh from the official MongoDB apt repository.
# mongosh is not in Kali/Debian default repos — requires MongoDB's own repo.
# Needed by: mongodb_disable_auth, database_expose atoms.
RUN wget -qO- https://www.mongodb.org/static/pgp/server-7.0.asc | \
    gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg && \
    echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
    > /etc/apt/sources.list.d/mongodb-org-7.0.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends mongosh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Default to bash for exec usage
CMD ["sleep", "infinity"]
