# Validation d'intégration sur Debian 12

Exécuter ces scénarios sur trois VM Debian 12 en réseau local après installation du paquet et configuration SSH. La matrice ci-dessous suit les exigences IT du dossier de référence. Ces essais ne sont pas exécutés sur Windows.

| ID | Vérification | Attendu |
|---|---|---|
| IT-01/02 | Créer puis modifier un fichier sur A | SHA-256 identique sur B et C en moins de 5 s pour un fichier < 1 Mo |
| IT-03 | Supprimer un fichier sur A | Tombstone sur les pairs, contenu déplacé dans `.dfr/trash` |
| IT-04/05 | Renommer fichier et répertoire | Chemin répliqué, sans perte de contenu |
| IT-06/07 | Rafale de 1 000 fichiers et modification partielle d'un gros fichier | Pas de perte, transfert différentiel rsync |
| IT-08/09/10 | Écritures depuis B et modifications concurrentes | Multi-maître, LWW déterministe et perdant conservé |
| IT-12/13/14 | Boucle, pair hors ligne, arrêt brutal du démon | Pas d'écho, rattrapage et redémarrage systemd |
| IT-15/16/17 | Dérive d'horloge, hash incorrect et chemin `..` | Alerte, contrôle d'intégrité, refus d'accès |
| IT-18/19/20/21 | Débordement inotify, disque plein, écriture lente, liens | Réconciliation de secours, aucune troncature, liens confinés |
| IT-22/23/24 | Ajout de nœud, interface web et paquet | Bootstrap sûr, état fidèle, installation/mise à jour/purge |

Pour un essai fumée manuel : créez `probe.txt` sous la racine de A, attendez la fenêtre de debounce, comparez `sha256sum` sur les nœuds, puis répétez en modifiant et supprimant le fichier. Contrôlez l'état avec `dfr-ctl status`, `dfr-ctl peers` et `journalctl -u dfr`.
