#!/bin/bash
# Utiliser des chemins absolus pour plus de robustesse
# Assurez-vous que ce chemin correspond bien à votre installation
BASE_DIR="/home/meteo/station-meteo"

# Créer un répertoire de logs si inexistant
LOG_DIR="/home/meteo/station-meteo/logs"
mkdir -p "$LOG_DIR" || exit 1

# Au lieu d'activer l'environnement, on utilise les chemins directs vers les exécutables de venv.
PYTHON_EXEC="$BASE_DIR/venv/bin/python"
GUNICORN_EXEC="$BASE_DIR/venv/bin/gunicorn"

# Lancer le script des capteurs en arrière-plan et logger sa sortie
echo "Lancement de meteo_capteur.py..." > "$LOG_DIR/service.log"
"$PYTHON_EXEC" "meteo_capteur.py" >> "$LOG_DIR/capteur.log" 2>&1 &

# Lancer le script de récupération satellite en arrière-plan et logger sa sortie
echo "Lancement de satellite_fetcher.py..." >> "$LOG_DIR/service.log"
"$PYTHON_EXEC" "satellite_fetcher.py" >> "$LOG_DIR/satellite.log" 2>&1 &

# Lancer le serveur web avec Gunicorn.
# Il va créer un "socket" pour communiquer avec Nginx.
# --workers 2 : nombre de processus pour gérer les requêtes.
# --bind unix:.. : crée un socket dans un répertoire géré par systemd.
# --umask 007 : supprime les permissions pour "les autres" mais garde celles du groupe.
echo "Lancement de Gunicorn..." >> "$LOG_DIR/service.log"
"$GUNICORN_EXEC" --workers 2 --bind unix:/run/station-meteo/station-meteo.sock --umask 007 meteo_web:app >> "$LOG_DIR/gunicorn.log" 2>&1
