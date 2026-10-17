# DFR — Distributed File Replicator

DFR réplique un répertoire entre 2 à 5 nœuds Debian 12 en maillage, sans serveur central. Le projet fournit le démon, les commandes de pair, le tableau de bord web local, les unités systemd et les sources du paquet `.deb`.

## Architecture

- Démon Python 3.11+ : inotify récursif via libc, file d'événements avec debounce, index SQLite et réconciliation périodique.
- Transport : rsync sur SSH avec clés ed25519 ; le script `ssh-gate` limite les commandes distantes et les chemins à la racine configurée.
- Cohérence : tombstones, corbeille, copies de conflits, règle LWW sur mtime et départage déterministe par identifiant de nœud.
- Contrôle : socket Unix JSON local, `dfr-ctl`, API Flask et interface française.
- Web : écoute locale uniquement par défaut sur `127.0.0.1:8080`.

## Construire le paquet Debian

Sur Debian 12, installer les outils de construction puis générer le paquet :

```sh
sudo apt-get update
sudo apt-get install -y debhelper dh-python python3-all python3-flask gunicorn rsync openssh-client openssh-server
scripts/build-deb.sh
```

Le `.deb` est créé dans `build/`. Installer sur chaque nœud :

```sh
sudo apt install ./build/dfr_0.1.0_all.deb
sudo nano /etc/dfr/dfr.conf
sudo dfrd --check --config /etc/dfr/dfr.conf
sudo systemctl enable --now dfr dfr-web
sudo systemctl status dfr dfr-web
```

Le paquet crée l'utilisateur système `dfr`, sa clé ed25519 et les dossiers d'état/journal. Les services restent désactivés jusqu'à la configuration. Modifiez `/etc/dfr/dfr.conf` sur chaque nœud : choisissez un identifiant distinct et la même racine. Déclarez tous les pairs dans `[peers]`, puis ajoutez les clés DFR sur les pairs avec un compte administrateur (la première connexion SSH demande de vérifier l'empreinte de l'hôte) : `sudo dfr-setup-ssh admin@node-b admin@node-c`. Une fois les clés configurées sur chaque nœud, activez les services. Si tu choisis une racine hors de `/srv/dfr`, ajoute-la à `ReadWritePaths` dans les unités systemd.

Avant le premier démarrage, faites une sauvegarde indépendante des répertoires concernés : la réplication propage les suppressions. Synchronisez les horloges avec chrony. Vérifiez que le compte `dfr` possède la racine et que les pairs utilisent la même racine, par exemple `/srv/dfr/data`. Pour les grandes arborescences, réglez `fs.inotify.max_user_watches` à au moins `524288` (par exemple `sudo sysctl -w fs.inotify.max_user_watches=524288`).

## Commandes d'exploitation

```sh
sudo dfr-ctl status
sudo dfr-ctl peers
sudo dfr-ctl logs --limit 100
sudo dfr-ctl conflicts
sudo dfr-ctl restore ID_CONFLIT
sudo dfr-ctl pause
sudo dfr-ctl resume
sudo dfr-ctl resync
sudo dfr-ctl reload
```

Le tableau de bord est accessible sur `http://127.0.0.1:8080/`. Pour un accès distant, placez-le derrière un proxy HTTPS authentifié. Les journaux sont consultables avec `journalctl -u dfr` et exportables en CSV/JSON dans l'interface.

## Développement et validation

Les tests unitaires et scénarios Debian sont dans `tests/`. Les tests unitaires tournent sur chaque push. La CI construit ensuite le paquet et le teste dans un maillage Docker à trois nœuds Debian 12. Pour rejouer ce maillage sur Linux :

```sh
scripts/build-deb.sh
bash tests/integration/run-docker.sh
```

Voir [la matrice de qualification](docs/ci-validation.md), [les scénarios d'intégration](tests/integration/README.md) et [la feuille de route](docs/roadmap.md).

La pipeline GitHub Actions s'exécute sur chaque push, pull request et déclenchement manuel. Elle compile le Python et le JavaScript, exécute les tests avec un seuil de couverture pour la configuration et l'état, vérifie les linters, construit et installe le `.deb` sur Debian 12, puis exécute les tests de cluster. Une Release taguée exige également une licence et un copyright déclarés. Les tests VM et limites de qualification restent listés dans la [matrice](docs/ci-validation.md) ; une Release CI ne prouve pas à elle seule la conformité à chaque seuil matériel.

## Publier une release

Pour générer une release GitHub avec son paquet Debian, pousse un tag de version au format `vMAJOR.MINOR.PATCH` après avoir fusionné les changements à publier :

```bash
git tag -a v0.1.1 -m "DFR 0.1.1"
git push origin v0.1.1
```

La CI rejoue les tests et le build Debian sur ce tag. Si tout réussit, elle crée la GitHub Release avec des notes générées et y ajoute `dfr_<version>_all.deb`. Les pushes de branches et les pull requests continuent de publier un artefact de CI pendant 30 jours, sans créer de release.

## Spécification

- [Dossier de référence](docs/DFR%20%E2%80%93%20Dossier%20de%20r%C3%A9f%C3%A9rence%20pour%20l'%C3%A9quipe%20de%20d%C3%A9veloppement.pdf)
- [Décisions d'architecture](docs/architecture-decisions.md)
- [Configuration commentée](config/dfr.conf.example)

Licence à définir avant publication.
