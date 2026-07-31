FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools (same set as TestEnvironmentTool bootstrap) + Python 3 / Flask / pip.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg lsb-release \
        iproute2 net-tools procps iputils-ping sudo sshpass \
        python3 python3-flask python3-pip python3-venv && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
