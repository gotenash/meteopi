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

# --- 0. Configuration des sources APT ---
log_info "Vérification et configuration des sources de paquets (APT)..."

# Sauvegarde du fichier original au cas où
if [ -f "/etc/apt/sources.list" ]; then
    cp /etc/apt/sources.list /etc/apt/sources.list.bak
fi
# Nettoyage des listes additionnelles qui pourraient contenir le miroir défectueux
rm -f /etc/apt/sources.list.d/*.list

# Création d'un fichier sources.list propre pointant vers les miroirs officiels
echo "deb http://raspbian.raspberrypi.com/raspbian/ bookworm main contrib non-free rpi" > /etc/apt/sources.list

log_info "Les sources de paquets ont été configurées pour utiliser les miroirs officiels."

# --- 1. Mise à jour du système ---
log_info "Mise à jour des paquets du système..."
apt update || log_error "Échec de la mise à jour des paquets."
apt upgrade -y || log_warning "Certains paquets n'ont pas pu être mis à jour."

# --- 2. Installation des dépendances système ---
log_info "Installation des dépendances système (Python3, pip, git, i2c-tools)..."
# On installe les grosses librairies Python via APT pour éviter la compilation
apt install -y python3 python3-pip git i2c-tools python3-numpy python3-pandas python3-matplotlib nginx || log_error "Échec de l'installation des dépendances système."



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
sudo -u "$SUDO_USER" bash <<'EOF'

echo -e "\e[32m[INFO]\e[0m Nettoyage et création de l'environnement virtuel..."
# On se place dans le bon répertoire
cd "/home/meteo/station-meteo" || exit 1

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
# On n'installe plus numpy, pandas, et matplotlib car ils sont fournis par le système
pip install gpiozero smbus2 adafruit-circuitpython-dht adafruit-circuitpython-bme280 adafruit-circuitpython-as5600 flask flask-login werkzeug requests Pillow gunicorn
if [ $? -ne 0 ]; then echo -e "\e[31m[ERROR]\e[0m Échec de l'installation des dépendances Python."; exit 1; fi

echo -e "\e[32m[INFO]\e[0m Installation des dépendances Python terminée."
EOF

# --- 6. Configuration de Nginx ---
log_info "Configuration de Nginx..."

# Supprime le site par défaut de Nginx pour éviter les conflits
rm -f /etc/nginx/sites-enabled/default

# Crée le fichier de configuration pour notre application
cat <<'EOF' > /etc/nginx/sites-available/station-meteo
server {
    listen 80;
    server_name _;

    location / {
        include proxy_params;
        proxy_pass http://unix:/run/station-meteo/station-meteo.sock;
    }

    location /static {
        alias /home/meteo/station-meteo/static;
    }
}
EOF

# Active le site en créant un lien symbolique
ln -s -f /etc/nginx/sites-available/station-meteo /etc/nginx/sites-enabled/

# Donne à Nginx la permission de lire les fichiers statiques
chmod 755 /home/meteo

# --- 6. Création des services systemd (optionnel mais recommandé) ---
log_info "Création des services systemd pour le capteur et l'application web..."
BASE_DIR="/home/meteo/station-meteo"
PYTHON_EXEC="$BASE_DIR/venv/bin/python"
GUNICORN_EXEC="$BASE_DIR/venv/bin/gunicorn"

# --- Nettoyage des anciens services ---
log_info "Nettoyage des anciens services systemd..."
systemctl disable --now station-meteo.service meteo-capteur.service meteo-web.service &> /dev/null
rm -f /etc/systemd/system/station-meteo.service
rm -f /etc/systemd/system/meteo-capteur.service
rm -f /etc/systemd/system/meteo-web.service
log_info "Anciens services nettoyés."

# --- Service 1: meteo_capteur.py ---
cat <<EOF > /etc/systemd/system/meteo-capteur.service
[Unit]
Description=Service de lecture des capteurs meteo
After=network.target

[Service]
ExecStart=$PYTHON_EXEC $BASE_DIR/meteo_capteur.py
WorkingDirectory=$BASE_DIR
Restart=always
User=meteo
Group=meteo

[Install]
WantedBy=multi-user.target
EOF

# --- Service 2: satellite_fetcher.py ---
cat <<EOF > /etc/systemd/system/satellite-fetcher.service
[Unit]
Description=Service de recuperation des images satellite
After=network.target

[Service]
ExecStart=$PYTHON_EXEC $BASE_DIR/satellite_fetcher.py
WorkingDirectory=$BASE_DIR
Restart=always
User=meteo
Group=meteo

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
WorkingDirectory=$BASE_DIR
RuntimeDirectory=station-meteo
Restart=always
User=meteo
Group=www-data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload || log_error "Échec du rechargement des démons systemd."
systemctl enable meteo-capteur.service || log_error "Échec de l'activation du service meteo-capteur."
systemctl enable satellite-fetcher.service || log_error "Échec de l'activation du service satellite-fetcher."
systemctl enable meteo-web.service || log_error "Échec de l'activation du service meteo-web."

log_info "Correction des permissions du répertoire du projet..."
# Change la propriété de tout le répertoire au SUDO_USER (ex: 'meteo')
# C'est crucial pour que les services systemd puissent accéder aux fichiers.
chown -R "$SUDO_USER:$SUDO_USER" .

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
