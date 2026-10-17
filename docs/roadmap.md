# État et feuille de route

## Présent dans le dépôt

- Démon de service, INI et SQLite.
- Surveillance récursive inotify avec debounce et réconciliation.
- Push rsync/SSH, transferts pull au bootstrap, vérification SHA-256.
- Tombstones, corbeille, sauvegarde des versions conflictuelles, restauration.
- Socket Unix et commandes `dfr-ctl` locales/distantes.
- API REST Flask, interface web, export JSON/CSV.
- `ssh-gate`, unités systemd, scripts de paquet Debian, CI et scénarios d'intégration.

## Validation en cours

- La CI construit, installe et purge le `.deb` dans Debian 12, puis utilise le paquet dans un maillage de trois conteneurs Debian.
- La matrice [CI et qualification de production](ci-validation.md) suit les fonctionnalités et les scénarios du dossier de référence.
- La validation en VM des seuils de performance, des pannes système/disque, des mises à niveau et de la reprise systemd reste nécessaire.
- L'interface est réservée à localhost par défaut ; toute exposition réseau exige un proxy HTTPS authentifié.

Une CI verte valide les scénarios automatisés, mais ne qualifie pas encore à elle seule une version stable ou une installation de production.
