FROM rockylinux:9

# RHEL-family twin of docker/target_express (Ubuntu). AppStream's default nodejs is
# too old for Express 5 / better-sqlite3 / Puppeteer, so enable the nodejs:20 module.
# Chromium comes from EPEL (binary /usr/bin/chromium-browser); symlink /usr/bin/chromium
# so app code using either path works on both families.
RUN dnf -y install epel-release && \
    dnf -y install --allowerasing \
        curl wget ca-certificates gnupg2 \
        iproute net-tools procps-ng iputils sudo sshpass && \
    dnf -y module reset nodejs && dnf -y module enable nodejs:20 && \
    dnf -y install nodejs npm gcc gcc-c++ make python3 chromium && \
    ln -sf /usr/bin/chromium-browser /usr/bin/chromium && \
    dnf clean all

CMD ["sleep", "infinity"]
