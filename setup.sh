#!/bin/bash

# --- Configuration ---
# Fichier principal du capteur (chemin relatif au répertoire du projet)
CAPTEUR_SCRIPT="meteo_capteur.py"
# Fichier pour la récupération des images satellite
SATELLITE_SCRIPT="satellite_fetcher.py"
# Fichier principal de l'application web
WEB_APP_SCRIPT="meteo_web.py"

# --- Fonctions utilitaires ---
log_info() {
    echo -e "\e[32m[INFO]\e[0m $1"
}

log_warning() {
    echo -e "\e[33m[WARN]\e[0m $1"
}

log_error() {
    echo -e "\e[31m[ERROR]\e[0m $1"
    exit 1
}

# --- Vérification des privilèges root ---
if [ "$EUID" -ne 0 ]; then
    log_error "Ce script doit être exécuté avec des privilèges root (sudo)."
fi

log_info "Démarrage de l'installation de la station météo pour Raspberry Pi 2..."

# --- Détection de la configuration ---
REAL_USER="${SUDO_USER:-$(whoami)}"
PROJECT_DIR=$(dirname "$(readlink -f "$0")")

# --- 0. Configuration des sources APT ---
log_info "Vérification et configuration des sources de paquets (APT)..."

# Détection de l'architecture pour choisir les bons dépôts
ARCH=$(dpkg --print-architecture)
log_info "Architecture détectée : $ARCH"

if [ "$ARCH" = "arm64" ]; then
    log_info "Configuration des sources pour Debian Bookworm (64-bit)..."
    echo "deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list
    echo "deb http://deb.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list
    echo "deb http://deb.debian.org/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list
    
    # Restauration du dépôt Raspberry Pi spécifique (kernel, firmware)
    if [ ! -f "/etc/apt/sources.list.d/raspi.list" ]; then
        echo "deb http://archive.raspberrypi.org/debian bookworm main" > /etc/apt/sources.list.d/raspi.list
    fi
    # Clé pour archive.raspberrypi.org
    wget -qO - https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add - 2>/dev/null || true

else
    log_info "Configuration des sources pour Raspbian Bookworm (32-bit)..."
    echo "deb http://raspbian.raspberrypi.com/raspbian/ bookworm main contrib non-free rpi" > /etc/apt/sources.list
    # Nettoyage des listes additionnelles qui pourraient causer des conflits sur 32-bit
    rm -f /etc/apt/sources.list.d/*.list
fi

# --- 0.5 Correction des clés GPG (Fix NO_PUBKEY) ---
if [ "$ARCH" != "arm64" ]; then
    log_info "Récupération de la clé publique Raspbian et mise à jour du trousseau..."
    wget -qO - https://archive.raspbian.org/raspbian.public.key | apt-key add - 2>/dev/null || true
    # On force une mise à jour et l'installation du paquet de clés pour être sûr
    apt-get -o Acquire::AllowInsecureRepositories=true -o Acquire::AllowDowngradeToInsecureRepositories=true update || true
    apt-get -o Acquire::AllowInsecureRepositories=true -o Acquire::AllowDowngradeToInsecureRepositories=true install -y --allow-unauthenticated raspbian-archive-keyring || true
fi

# --- 0.6 Création du dossier de logs (Early) ---
# On le crée ici pour garantir qu'il existe même si l'installation échoue plus tard
log_info "Création du dossier de logs..."
mkdir -p "$PROJECT_DIR/logs"
chown -R "$SUDO_USER:$SUDO_USER" "$PROJECT_DIR/logs"
chmod 755 "$PROJECT_DIR/logs"

# --- 1. Mise à jour du système ---
log_info "Mise à jour des paquets du système..."
apt update || log_error "Échec de la mise à jour des paquets."
apt upgrade -y || log_warning "Certains paquets n'ont pas pu être mis à jour."

# --- 2. Installation des dépendances système ---
log_info "Installation des dépendances système (Python3, pip, git, i2c-tools)..."
# On installe les grosses librairies Python via APT pour éviter la compilation
apt install -y python3 python3-pip git i2c-tools libopenjp2-7 libatlas-base-dev nginx cifs-utils || log_error "Échec de l'installation des dépendances système."



# --- 3. Activation de l'interface I2C ---
log_info "Vérification et activation de l'interface I2C..."
# Vérifie si i2c est déjà activé dans /boot/config.txt
if ! grep -q "dtparam=i2c_arm=on" /boot/config.txt; then
    echo "dtparam=i2c_arm=on" | tee -a /boot/config.txt > /dev/null
    log_info "L'interface I2C a été activée dans /boot/config.txt. Un redémarrage sera nécessaire."
else
    log_info "L'interface I2C est déjà activée."
fi

# Ajoute l'utilisateur qui a lancé sudo aux groupes matériels nécessaires (i2c, gpio, spi)
log_info "Ajout de l'utilisateur '$SUDO_USER' aux groupes i2c, gpio, spi..."
usermod -a -G i2c "$SUDO_USER" || log_warning "Échec de l'ajout de l'utilisateur au groupe i2c."
usermod -a -G gpio "$SUDO_USER" || log_warning "Échec de l'ajout de l'utilisateur au groupe gpio."
usermod -a -G spi "$SUDO_USER" || log_warning "Échec de l'ajout de l'utilisateur au groupe spi."
log_info "Permissions matérielles configurées pour l'utilisateur '$SUDO_USER'."

# --- 4. Mise à jour du dépôt (si git est présent et que c'est un dépôt) ---
if command -v git &> /dev/null; then
    if [ -d ".git" ]; then # Vérifie si le répertoire courant est un dépôt Git
        log_info "Mise à jour du dépôt Git..."
        git pull || log_warning "Échec de la mise à jour du dépôt. Poursuite de l'installation."
    else
        log_warning "Le répertoire courant n'est pas un dépôt Git. Le clonage est ignoré."
    fi
else
    log_warning "Git non trouvé. Impossible de mettre à jour le dépôt."
fi

# --- 5. Installation des dépendances Python ---
log_info "Lancement de l'installation des dépendances Python en tant qu'utilisateur '$SUDO_USER'..."

# On exécute la partie non-root du script en tant que l'utilisateur original
sudo -u "$SUDO_USER" bash -s "$PROJECT_DIR" <<'EOF'
PROJECT_DIR="$1"

echo -e "\e[32m[INFO]\e[0m Nettoyage et création de l'environnement virtuel..."
# On se place dans le bon répertoire
cd "$PROJECT_DIR" || exit 1

# On supprime l'ancien environnement virtuel pour garantir une installation propre
rm -rf venv

# Création de l'environnement virtuel avec accès aux paquets système
python3 -m venv venv --system-site-packages
if [ $? -ne 0 ]; then echo -e "\e[31m[ERROR]\e[0m Échec de la création de venv."; exit 1; fi

source venv/bin/activate
if [ $? -ne 0 ]; then echo -e "\e[31m[ERROR]\e[0m Échec de l'activation de venv."; exit 1; fi

echo -e "\e[32m[INFO]\e[0m Mise à jour de pip..."
pip install --upgrade pip

echo -e "\e[32m[INFO]\e[0m Installation des dépendances Python restantes..."
# Installation avec --no-cache-dir pour économiser la RAM sur Pi et éviter les timeouts
pip install --no-cache-dir numpy pandas matplotlib gpiozero smbus2 adafruit-circuitpython-dht adafruit-circuitpython-bme280 adafruit-circuitpython-as5600 flask flask-login werkzeug requests Pillow gunicorn
if [ $? -ne 0 ]; then echo -e "\e[31m[ERROR]\e[0m Échec de l'installation des dépendances Python."; exit 1; fi

echo -e "\e[32m[INFO]\e[0m Installation des dépendances Python terminée."
EOF

# --- 6. Configuration de Nginx ---
log_info "Configuration de Nginx..."

# Supprime le site par défaut de Nginx pour éviter les conflits
rm -f /etc/nginx/sites-enabled/default

# Crée le fichier de configuration pour notre application
cat <<EOF > /etc/nginx/sites-available/station-meteo
server {
    listen 80;
    server_name _;

    location / {
        include proxy_params;
        proxy_pass http://unix:/run/station-meteo/station-meteo.sock;
    }

    location /static {
        alias $PROJECT_DIR/static;
    }
}
EOF

# Active le site en créant un lien symbolique
ln -s -f /etc/nginx/sites-available/station-meteo /etc/nginx/sites-enabled/
systemctl restart nginx

# Donne à Nginx la permission de lire les fichiers statiques
chmod 755 $(dirname "$PROJECT_DIR")

# --- 6. Création des services systemd (optionnel mais recommandé) ---
log_info "Création des services systemd pour le capteur et l'application web..."
PYTHON_EXEC="$PROJECT_DIR/venv/bin/python"
GUNICORN_EXEC="$PROJECT_DIR/venv/bin/gunicorn"

# --- Nettoyage des anciens services ---
log_info "Nettoyage des anciens services systemd..."
systemctl disable --now station-meteo.service meteo-capteur.service meteo-web.service satellite-fetcher.service telegram-bot.service &> /dev/null
rm -f /etc/systemd/system/station-meteo.service
rm -f /etc/systemd/system/meteo-capteur.service
rm -f /etc/systemd/system/meteo-web.service
rm -f /etc/systemd/system/satellite-fetcher.service
rm -f /etc/systemd/system/telegram-bot.service
log_info "Anciens services nettoyés."

# --- Service 1: meteo_capteur.py ---
cat <<EOF > /etc/systemd/system/meteo-capteur.service
[Unit]
Description=Service de lecture des capteurs meteo
After=network.target

[Service]
ExecStart=$PYTHON_EXEC $PROJECT_DIR/meteo_capteur.py
WorkingDirectory=$PROJECT_DIR
Restart=always
User=$REAL_USER
Group=$REAL_USER
StandardOutput=append:$PROJECT_DIR/logs/capteur.log
StandardError=append:$PROJECT_DIR/logs/capteur.log

[Install]
WantedBy=multi-user.target
EOF

# --- Service 2: satellite_fetcher.py ---
cat <<EOF > /etc/systemd/system/satellite-fetcher.service
[Unit]
Description=Service de recuperation des images satellite
After=network.target

[Service]
ExecStart=$PYTHON_EXEC $PROJECT_DIR/satellite_fetcher.py
WorkingDirectory=$PROJECT_DIR
Restart=always
User=$REAL_USER
Group=$REAL_USER
StandardOutput=append:$PROJECT_DIR/logs/satellite.log
StandardError=append:$PROJECT_DIR/logs/satellite.log

[Install]
WantedBy=multi-user.target
EOF

# --- Service 4: telegram_bot.py ---
cat <<EOF > /etc/systemd/system/telegram-bot.service
[Unit]
Description=Service de notification meteo par Telegram
After=network.target

[Service]
ExecStart=$PYTHON_EXEC $PROJECT_DIR/telegram_bot.py
WorkingDirectory=$PROJECT_DIR
Restart=always
User=$REAL_USER
Group=$REAL_USER
StandardOutput=append:$PROJECT_DIR/logs/telegram.log
StandardError=append:$PROJECT_DIR/logs/telegram.log

[Install]
WantedBy=multi-user.target
EOF

# --- Service 3: Gunicorn (web) ---
cat <<EOF > /etc/systemd/system/meteo-web.service
[Unit]
Description=Application web Gunicorn pour la station meteo
After=network.target

[Service]
ExecStart=$GUNICORN_EXEC --workers 2 --bind unix:/run/station-meteo/station-meteo.sock --umask 007 meteo_web:app
WorkingDirectory=$PROJECT_DIR
RuntimeDirectory=station-meteo
Restart=always
User=$REAL_USER
Group=www-data
StandardOutput=append:$PROJECT_DIR/logs/gunicorn.log
StandardError=append:$PROJECT_DIR/logs/gunicorn.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload || log_error "Échec du rechargement des démons systemd."
systemctl enable --now meteo-capteur.service || log_error "Échec de l'activation du service meteo-capteur."
systemctl enable --now satellite-fetcher.service || log_error "Échec de l'activation du service satellite-fetcher."
systemctl enable --now meteo-web.service || log_error "Échec de l'activation du service meteo-web."
systemctl enable --now telegram-bot.service || log_error "Échec de l'activation du service telegram-bot."

log_info "Correction des permissions du répertoire du projet..."
# Change la propriété de tout le répertoire au SUDO_USER (ex: 'meteo')
# C'est crucial pour que les services systemd puissent accéder aux fichiers.
chown -R "$SUDO_USER:$SUDO_USER" .

# Rend les scripts exécutables
chmod +x "$PROJECT_DIR"/*.py
chmod +x "$PROJECT_DIR"/*.sh

log_info "Services systemd créés et activés. Ils démarreront au prochain redémarrage."

# --- 7. Nettoyage ---
log_info "Nettoyage des paquets inutiles..."
apt autoremove -y

log_info "Installation terminée avec succès !"
log_info "Un redémarrage est recommandé pour que tous les changements prennent effet (sudo reboot)."
log_info "Après le redémarrage, vous pouvez vérifier l'état du service avec :"
log_info "  sudo systemctl status meteo-capteur.service"
log_info "  sudo systemctl status satellite-fetcher.service"
log_info "  sudo systemctl status meteo-web.service"
log_info "  sudo systemctl status telegram-bot.service"
