# Décisions d'architecture

La version 1 du dossier de référence autorise un démon Python si l'équipe acte ce choix en phase 0. DFR retient Python 3.11 pour le démon afin d'implémenter et maintenir les règles de réplication, conflits, tombstones et contrôle local dans un code commun. Le noyau Linux, `rsync`, SSH, SQLite et systemd restent les mécanismes du système.

| Sujet | Décision |
|---|---|
| Démon | Python 3.11+, surveillance inotify via libc, sans dépendance Python pour inotify |
| Configuration | INI (`/etc/dfr/dfr.conf`) |
| Transfert | rsync sur SSH ; aucune socket TCP propriétaire |
| Index | SQLite en mode WAL |
| Contrôle local | Socket Unix, JSON par ligne |
| Conflit | mtime le plus récent ; égalité départagée par ordre lexicographique des identifiants ; version perdante archivée |
| Suppression | Tombstone synchronisé et mise en corbeille, rétention par défaut de 7 jours |
| Interface | Flask local sur loopback ; exposition réseau uniquement derrière HTTPS et authentification |

La sélection Python remplace le premier squelette C99, qui n'avait pas encore de moteur fonctionnel. Elle suit l'alternative explicitement admise au §3.6 du dossier de référence.
