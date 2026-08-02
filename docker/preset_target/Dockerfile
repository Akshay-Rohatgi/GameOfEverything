FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install WP-CLI prerequisites and common tools used by preset app testing snippets.
# WP-CLI is baked in here so deploy snippets don't need to download it at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        wget \
        ca-certificates \
        php-cli \
        php-mysql \
        php-xml \
        php-mbstring \
        php-curl \
        iproute2 \
        net-tools \
        procps \
        iputils-ping \
        sudo \
        sshpass \
        mysql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Pin WP-CLI version so the image is reproducible.
# To upgrade: bump WP_CLI_VERSION and rebuild.
ARG WP_CLI_VERSION=2.10.0
RUN curl -sL "https://github.com/wp-cli/wp-cli/releases/download/v${WP_CLI_VERSION}/wp-cli-${WP_CLI_VERSION}.phar" \
        -o /usr/local/bin/wp \
    && chmod +x /usr/local/bin/wp \
    && wp --info --allow-root

CMD ["sleep", "infinity"]
