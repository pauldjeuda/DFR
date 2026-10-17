#!/bin/sh
set -eu
if [ "$#" -lt 1 ]; then
  echo "Usage: setup-ssh.sh user@peer [user@peer ...]" >&2
  exit 2
fi
KEY=${DFR_SSH_KEY:-/var/lib/dfr/.ssh/id_ed25519}
PUB="$KEY.pub"
if [ ! -r "$PUB" ]; then echo "Clé publique introuvable : $PUB" >&2; exit 1; fi
PUBKEY=$(awk '{print $1" "$2}' "$PUB")
case "$PUBKEY" in ssh-ed25519\ *) ;; *) echo "La clé doit être ed25519" >&2; exit 1;; esac
for peer in "$@"; do
  printf '%s\n' "Ajout de la clé DFR sur $peer (accès administrateur requis)"
  ssh "$peer" "sudo install -d -o dfr -g dfr -m 0700 /var/lib/dfr/.ssh; printf '%s\\n' 'restrict,command=\"/usr/lib/dfr/ssh-gate\" $PUBKEY dfr-peer-key' | sudo tee -a /var/lib/dfr/.ssh/authorized_keys >/dev/null; sudo chown dfr:dfr /var/lib/dfr/.ssh/authorized_keys; sudo chmod 0600 /var/lib/dfr/.ssh/authorized_keys"
  host=${peer#*@}
  ssh-keyscan -H "$host" 2>/dev/null | sudo tee -a /var/lib/dfr/.ssh/known_hosts >/dev/null
done
echo "Échange terminé. Vérifiez chaque connexion avec le compte dfr."
