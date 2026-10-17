# Validation d'intégration sur Debian 12

La CI construit le `.deb`, puis lance ce paquet dans trois conteneurs Debian 12
reliés en maillage. Elle configure les clés SSH, le compte restreint et les
services, exécute les tests Bats depuis chaque nœud, puis teste les conflits et
la reprise réseau. Pour reproduire l'intégration sur un hôte Linux avec Docker :

```sh
scripts/build-deb.sh
bash tests/integration/run-docker.sh
```

Les scénarios plus proches d'une exploitation réelle doivent aussi être lancés
sur trois VM Debian 12 avant qualification de production. Le tableau ci-dessous
indique les contrôles et leurs limites actuelles.

| ID | Vérification | Attendu |
|---|---|---|
| IT-01/02 | Créer puis modifier un fichier < 1 Mo | Trois nœuds, empreintes identiques et réplication sous 5 s — CI |
| IT-03 | Supprimer un fichier | Tombstone sur un pair et copie en corbeille locale — CI |
| IT-04/05 | Renommer fichier et répertoire | Chemin répliqué sur les deux pairs — CI |
| IT-06 | Rafale de 1 000 fichiers | Inventaire distant sans perte — CI |
| IT-07 | Modifier 1 Mo dans un fichier de 1 GiB | Compteur rsync inférieur à 20 Mio — CI |
| IT-08 | Écrire depuis chaque nœud | Multi-maître — CI |
| IT-09/10 | Éditions concurrentes et mtimes égaux | Gagnant LWW déterministe, conflit journalisé et perdant archivé — CI |
| IT-12/13 | Boucle stable et pair hors ligne | Pas de trafic répété après convergence, rattrapage après reconnexion — CI |
| IT-14 | `kill -9` du démon | Redémarrage réel par systemd — test VM restant |
| IT-15/16 | Horloge décalée et corruption en transfert | Alerte et récupération après trois échecs — tests VM restants |
| IT-17 | Chemin `..` via SSH | Requête refusée — CI |
| IT-18/19 | Débordement inotify et disque plein | Réconciliation de secours et absence de troncature — tests VM restants |
| IT-20/21 | Écriture lente et liens symboliques | Attente de `close_write`, lien répliqué sans suivre sa cible — CI |
| IT-22 | Ajout d'un nœud vide | Bootstrap au démarrage couvert ; ajout à un maillage déjà actif — test restant |
| IT-23 | Interface web | État, pause/reprise et export CSV/JSON via API — CI |
| IT-24 | Paquet `.deb` | Construction, installation et purge — CI ; mise à niveau avec état conservé à tester |

Pour un essai fumée manuel : créez `probe.txt` sous la racine de A, attendez la fenêtre de debounce, comparez `sha256sum` sur les nœuds, puis répétez en modifiant et supprimant le fichier. Contrôlez l'état avec `dfr-ctl status`, `dfr-ctl peers` et `journalctl -u dfr`. Voir la [matrice générale des exigences](../../docs/ci-validation.md) avant de qualifier une version.
