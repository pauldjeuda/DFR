# État et feuille de route

## Présent dans le dépôt

- Démon de service, INI et SQLite.
- Surveillance récursive inotify avec debounce et réconciliation.
- Push rsync/SSH, transferts pull au bootstrap, vérification SHA-256.
- Tombstones, corbeille, sauvegarde des versions conflictuelles, restauration.
- Socket Unix et commandes `dfr-ctl` locales/distantes.
- API REST Flask, interface web, export JSON/CSV.
- `ssh-gate`, unités systemd, scripts de paquet Debian, CI et scénarios d'intégration.

## À faire pendant la validation Debian

1. Construire le `.deb` sur Debian 12 et corriger les erreurs de lintian/packaging.
2. Déployer trois VM, tester le bootstrap, les clés SSH et les scénarios `tests/integration/README.md`.
3. Traiter les écarts fonctionnels/performance et mesurer les seuils du dossier (§2.2 et §10).
4. Ajouter les contrôles d'authentification du tableau de bord si une écoute hors localhost est demandée.
5. Compléter les procédures de mise à niveau, retrait de nœud et récupération, puis publier une version validée.

Le code constitue une base fonctionnelle de développement ; une version stable ne doit être déclarée qu'après la campagne Debian distribuée et la revue de sécurité.
