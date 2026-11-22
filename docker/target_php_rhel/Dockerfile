FROM rockylinux:9

# RHEL-family twin of docker/target_php (Ubuntu). Bakes base tools + Apache/PHP so
# per-entity web builds on Red Hat are as fast as the Debian appliance. PHP on RHEL 9
# is served by php-fpm (no mod_php); the deploy script starts php-fpm + httpd.
# sshpass is EPEL-only, so enable EPEL first. --allowerasing lets curl replace
# curl-minimal.
RUN dnf -y install epel-release && \
    dnf -y install --allowerasing \
        curl wget ca-certificates gnupg2 \
        iproute net-tools procps-ng iputils sudo sshpass \
        httpd php php-fpm php-mysqlnd php-pdo && \
    dnf clean all

CMD ["sleep", "infinity"]
