FROM rockylinux:9

# RHEL-family twin of docker/target_flask (Ubuntu). Base tools + Python 3 / Flask / pip.
RUN dnf -y install epel-release && \
    dnf -y install --allowerasing \
        curl wget ca-certificates gnupg2 \
        iproute net-tools procps-ng iputils sudo sshpass \
        python3 python3-pip && \
    pip3 install flask && \
    dnf clean all

CMD ["sleep", "infinity"]
