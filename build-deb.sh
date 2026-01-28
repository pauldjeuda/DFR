#!/bin/bash
# Script de construction du paquet Debian DFR

set -e

PACKAGE_NAME="dfr"
VERSION="1.0.0"
BUILD_DIR="build"
DEB_DIR="${BUILD_DIR}/${PACKAGE_NAME}_${VERSION}"

echo "=== Construction du paquet DFR v${VERSION} ==="

# Nettoyage
echo "Nettoyage des fichiers précédents..."
rm -rf ${BUILD_DIR}
make clean

# Compilation
echo "Compilation du démon..."
make

# Création de la structure Debian
echo "Création de la structure du paquet..."
mkdir -p ${DEB_DIR}/DEBIAN
mkdir -p ${DEB_DIR}/usr/bin
mkdir -p ${DEB_DIR}/etc/dfr
mkdir -p ${DEB_DIR}/lib/systemd/system
mkdir -p ${DEB_DIR}/usr/share/dfr/web/templates
mkdir -p ${DEB_DIR}/var/log/dfr
mkdir -p ${DEB_DIR}/var/run/dfr
mkdir -p ${DEB_DIR}/var/dfr/sync

# Copie du fichier control
echo "Copie des métadonnées..."
cat > ${DEB_DIR}/DEBIAN/control << EOF
Package: ${PACKAGE_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: DJEUDA TCHAPNGA PAUL HERVÉ <paul@example.com>
Depends: libc6, libjson-c5, python3, python3-flask, python3-flask-cors
Description: Distributed File Replicator
 DFR est un système de réplication distribuée de fichiers
 qui permet de synchroniser automatiquement des fichiers
 entre plusieurs machines Linux sans point de défaillance unique.
 .
 Le système utilise inotify pour détecter les changements
 de fichiers en temps réel et les réplique sur tous les
 nœuds du cluster de manière cohérente.
EOF

# Scripts post-installation
cat > ${DEB_DIR}/DEBIAN/postinst << 'EOF'
#!/bin/bash
set -e

# Créer les répertoires nécessaires
mkdir -p /var/log/dfr
mkdir -p /var/run/dfr
mkdir -p /var/dfr/sync

# Définir les permissions
chmod 755 /var/log/dfr
chmod 755 /var/run/dfr
chmod 755 /var/dfr/sync

# Recharger systemd
systemctl daemon-reload

echo "DFR installé avec succès!"
echo "Configuration: /etc/dfr/dfr.conf"
echo "Pour démarrer: sudo systemctl start dfr"
echo "Pour activer au démarrage: sudo systemctl enable dfr"
echo "Interface web: python3 /usr/share/dfr/web/api.py"

exit 0
EOF

# Script de pré-suppression
cat > ${DEB_DIR}/DEBIAN/prerm << 'EOF'
#!/bin/bash
set -e

# Arrêter le service s'il est en cours d'exécution
if systemctl is-active --quiet dfr; then
    systemctl stop dfr
fi

# Désactiver le service
if systemctl is-enabled --quiet dfr; then
    systemctl disable dfr
fi

exit 0
EOF

# Script post-suppression
cat > ${DEB_DIR}/DEBIAN/postrm << 'EOF'
#!/bin/bash
set -e

# Recharger systemd
systemctl daemon-reload

# Nettoyer les répertoires (optionnel, commenté par défaut)
# rm -rf /var/log/dfr
# rm -rf /var/run/dfr
# rm -rf /var/dfr

exit 0
EOF

chmod 755 ${DEB_DIR}/DEBIAN/postinst
chmod 755 ${DEB_DIR}/DEBIAN/prerm
chmod 755 ${DEB_DIR}/DEBIAN/postrm

# Copie des fichiers binaires et de configuration
echo "Copie des fichiers..."
cp dfrd ${DEB_DIR}/usr/bin/
chmod 755 ${DEB_DIR}/usr/bin/dfrd

cp dfr.conf ${DEB_DIR}/etc/dfr/
cp dfr.service ${DEB_DIR}/lib/systemd/system/

# Copie de l'interface web
cp api.py ${DEB_DIR}/usr/share/dfr/web/
cp index.html ${DEB_DIR}/usr/share/dfr/web/templates/
chmod 755 ${DEB_DIR}/usr/share/dfr/web/api.py

# Construction du paquet
echo "Construction du paquet .deb..."
dpkg-deb --build ${DEB_DIR}

# Déplacement du paquet
mv ${BUILD_DIR}/${PACKAGE_NAME}_${VERSION}.deb .

echo ""
echo "=== Paquet créé avec succès! ==="
echo "Fichier: ${PACKAGE_NAME}_${VERSION}.deb"
echo ""
echo "Installation:"
echo "  sudo dpkg -i ${PACKAGE_NAME}_${VERSION}.deb"
echo "  sudo apt-get install -f  # Pour résoudre les dépendances"
echo ""
echo "Configuration:"
echo "  1. Éditer /etc/dfr/dfr.conf"
echo "  2. Ajouter les peers distants"
echo "  3. sudo systemctl start dfr"
echo "  4. python3 /usr/share/dfr/web/api.py &"
echo ""