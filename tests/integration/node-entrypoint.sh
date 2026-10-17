#!/bin/sh
set -eu

mkdir -p /run/sshd
ssh-keygen -A
rm -f /var/lib/dfr/.ssh/id_ed25519 /var/lib/dfr/.ssh/id_ed25519.pub
su -s /bin/sh dfr -c 'ssh-keygen -q -t ed25519 -N "" -f /var/lib/dfr/.ssh/id_ed25519'
exec /usr/sbin/sshd -D -e
