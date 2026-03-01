FROM kalilinux/kali-rolling

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update repos and install core offensive toolset
# These cover current atoms (SMB, SSH) and provide headroom for future ones.
# Additional tools are one `apt install` away — no custom repos needed.
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
        metasploit-framework \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Default to bash for exec usage
CMD ["sleep", "infinity"]
