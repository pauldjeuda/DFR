ARG DEB_PACKAGE
FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive
ARG DEB_PACKAGE

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bats curl openssh-server python3 rsync \
    && rm -rf /var/lib/apt/lists/*

COPY ${DEB_PACKAGE} /tmp/dfr.deb
RUN apt-get update \
    && apt-get install -y --no-install-recommends /tmp/dfr.deb \
    && rm -f /tmp/dfr.deb \
    && rm -rf /var/lib/apt/lists/*

COPY tests/integration/replication.bats /opt/dfr-tests/replication.bats
COPY tests/integration/node-entrypoint.sh /usr/local/sbin/dfr-it-entrypoint
RUN chmod 0755 /usr/local/sbin/dfr-it-entrypoint \
    && mkdir -p /run/sshd \
    && printf '%s\n' \
       'PermitRootLogin no' \
       'PubkeyAuthentication yes' \
       'PasswordAuthentication no' \
       'KbdInteractiveAuthentication no' \
       'UsePAM no' \
       'AllowUsers dfr' \
       'LogLevel VERBOSE' >> /etc/ssh/sshd_config

ENTRYPOINT ["/usr/local/sbin/dfr-it-entrypoint"]
