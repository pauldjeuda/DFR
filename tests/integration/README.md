# Validation d'intégration sur Debian 12

Cette suite lance le paquet dans un maillage de trois conteneurs Debian 12 et
vérifie les réplications, conflits et reprises réseau. Elle est destinée à un
essai manuel sur un hôte Linux avec Docker ; la CI standard s'appuie sur les
tests unitaires et les vérifications du paquet. Le test unitaire rsync vérifie
séparément la forme de commande Debian 13. Pour exécuter le maillage :

```sh
scripts/build-deb.sh
bash tests/integration/run-docker.sh
```

Les scénarios plus proches d'une exploitation réelle doivent aussi être lancés
sur trois VM Debian 12 avant qualification de production. Le tableau ci-dessous
indique les contrôles et leurs limites actuelles.

| ID | Vérification | Attendu |
|---|---|---|
| IT-01/02 | Créer puis modifier un fichier < 1 Mo | Trois nœuds, empreintes identiques et réplication sous 5 s — maillage manuel |
| IT-03 | Supprimer un fichier | Tombstone sur un pair et copie en corbeille locale — maillage manuel |
| IT-04/05 | Renommer fichier et répertoire | Chemin répliqué sur les deux pairs — maillage manuel |
| IT-06 | Rafale de 1 000 fichiers | Inventaire distant sans perte — maillage manuel |
| IT-07 | Modifier 1 Mo dans un fichier de 1 GiB | Compteur rsync inférieur à 20 Mio — maillage manuel |
| IT-08 | Écrire depuis chaque nœud | Multi-maître — maillage manuel |
| IT-09/10 | Éditions concurrentes et mtimes égaux | Gagnant LWW déterministe, conflit journalisé et perdant archivé — maillage manuel |
| IT-12/13 | Boucle stable et pair hors ligne | Pas de trafic répété après convergence, rattrapage après reconnexion — maillage manuel |
| IT-14 | `kill -9` du démon | Redémarrage réel par systemd — test VM restant |
| IT-15/16 | Horloge décalée et corruption en transfert | Alerte et récupération après trois échecs — tests VM restants |
| IT-17 | Chemin `..` via SSH | Requête refusée — maillage manuel ; test unitaire vérifie que la forme de commande rsync Debian 13 traverse le garde-fou |
| IT-18/19 | Débordement inotify et disque plein | Réconciliation de secours et absence de troncature — tests VM restants |
| IT-20/21 | Écriture lente et liens symboliques | Attente de `close_write`, lien répliqué sans suivre sa cible — maillage manuel |
| IT-22 | Ajout d'un nœud vide | Bootstrap au démarrage couvert ; ajout à un maillage déjà actif — test restant |
| IT-23 | Interface web | État, pause/reprise et export CSV/JSON via API — maillage manuel |
| IT-24 | Paquet `.deb` | Construction, installation et purge — CI ; mise à niveau avec état conservé à tester |

Pour un essai fumée manuel : créez `probe.txt` sous la racine de A, attendez la fenêtre de debounce, comparez `sha256sum` sur les nœuds, puis répétez en modifiant et supprimant le fichier. Contrôlez l'état avec `dfr-ctl status`, `dfr-ctl peers` et `journalctl -u dfr`. Voir la [matrice générale des exigences](../../docs/ci-validation.md) avant de qualifier une version.
