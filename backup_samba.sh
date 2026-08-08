#!/bin/bash

# --- Script de sauvegarde vers un partage Samba (Windows/NAS) ---
# À placer dans le dossier meteopi et à rendre exécutable (chmod +x backup_samba.sh)

# Dossier local où se trouvent les fichiers de la station
PROJECT_DIR=$(dirname "$(readlink -f "$0")")
CONFIG_FILE="$PROJECT_DIR/config.json"

# ================= CONFIGURATION (chargée depuis config.json) =================
# On utilise Python pour lire le JSON proprement depuis la config de l'interface web
SMB_SHARE=$(python3 -c "import sys, json; print(json.load(open('$CONFIG_FILE')).get('samba_share', ''))")
SMB_USER=$(python3 -c "import sys, json; print(json.load(open('$CONFIG_FILE')).get('samba_user', ''))")
SMB_PASS=$(python3 -c "import sys, json; print(json.load(open('$CONFIG_FILE')).get('samba_password', ''))")

# Point de montage temporaire
MOUNT_POINT="/mnt/meteopi_backup"
# ===========================================================

# Date du jour pour nommer les fichiers
DATE_SUFFIX=$(date +"%Y-%m-%d")

if [ -z "$SMB_SHARE" ]; then
    echo "⚠️  Configuration Samba vide ou incomplète dans config.json."
    echo "   Veuillez la configurer via l'interface Admin."
    exit 0
fi

echo "[$(date)] Démarrage de la sauvegarde vers $SMB_SHARE"

# --- NOUVEAU : Persistance locale sur SD avant Samba ---
PERSISTENT_DIR="$PROJECT_DIR/data_persistent"
mkdir -p "$PERSISTENT_DIR"
echo "📦 Synchronisation locale RAM -> SD..."
cp -rp "$PROJECT_DIR/data/." "$PERSISTENT_DIR/"

# --- NOUVEAU : Vérification de la disponibilité de l'hôte Samba ---
# Extraction du nom d'hôte ou de l'IP depuis l'URL de partage Samba
SMB_HOST=$(echo "$SMB_SHARE" | sed -E 's/^\/\/([^/]+).*/\\1/')

if [ -n "$SMB_HOST" ]; then
    echo "🔍 Vérification de la connectivité réseau vers l'hôte Samba ($SMB_HOST)..."
    
    # 1. Tentative de ping rapide (2 secondes max)
    ping -c 1 -W 2 "$SMB_HOST" >/dev/null 2>&1
    PING_STATUS=$?
    
    # 2. Si le ping échoue, vérification du port SMB 445 (2 secondes max)
    SMB_PORT_STATUS=1
    if [ $PING_STATUS -ne 0 ]; then
        timeout 2 bash -c "echo > /dev/tcp/$SMB_HOST/445" >/dev/null 2>&1
        SMB_PORT_STATUS=$?
    fi
    
    # Si les deux échouent, l'hôte est injoignable
    if [ $PING_STATUS -ne 0 ] && [ $SMB_PORT_STATUS -ne 0 ]; then
        echo "⚠️ L'hôte Samba ($SMB_HOST) est injoignable (ping et port 445 en échec)."
        echo "   La sauvegarde sur le partage réseau est annulée."
        echo "   La synchronisation locale RAM -> SD s'est toutefois déroulée avec succès."
        exit 0 # Sortie propre (code 0) pour ne pas mettre le service systemd en échec
    fi
fi

# 1. Création du point de montage si inexistant
if [ ! -d "$MOUNT_POINT" ]; then
    echo "📂 Création du point de montage $MOUNT_POINT"
    sudo mkdir -p "$MOUNT_POINT"
fi

# 2. Montage du partage
echo "🔌 Montage du lecteur réseau..."
# Démontage préventif au cas où
sudo umount "$MOUNT_POINT" 2>/dev/null

# Montage avec sudo (vers=3.0 est standard pour Windows 10/11)
sudo mount -t cifs "$SMB_SHARE" "$MOUNT_POINT" -o username="$SMB_USER",password="$SMB_PASS",vers=3.0,iocharset=utf8

if [ $? -ne 0 ]; then
    echo "❌ Erreur : Impossible de monter le partage Samba."
    echo "Vérifiez l'adresse IP, le nom du partage et les identifiants."
    exit 1
fi

# 3. Copie des fichiers
echo "💾 Copie des fichiers en cours..."

# Création d'un sous-dossier sur le partage (optionnel)
REMOTE_DIR="$MOUNT_POINT/MeteoPi_Backup"
sudo mkdir -p "$REMOTE_DIR"

FILES=("data/meteo_log.csv" "data/wind_detail_log.csv" "config.json")

for file in "${FILES[@]}"; do
    if [ -f "$PROJECT_DIR/$file" ]; then
        # On extrait uniquement le nom du fichier sans le chemin (ex: meteo_log.csv)
        FILENAME=$(basename "$file")
        NAME="${FILENAME%.*}"
        EXT="${FILENAME##*.}"
        # Copie vers le NAS avec le timestamp, sans recréer l'arborescence 'data/'
        sudo cp "$PROJECT_DIR/$file" "$REMOTE_DIR/${NAME}_$DATE_SUFFIX.${EXT}"
        echo "✅ $file copié."
    fi
done

# 4. Démontage
echo "🔌 Démontage du lecteur réseau..."
sudo umount "$MOUNT_POINT"
echo "[$(date)] Sauvegarde terminée avec succès."