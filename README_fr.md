<p align="center">
  <img src="static/img/meteopi.png" alt="Logo MeteoPi" width="150"/>
</p>

# Station Météo Raspberry Pi (MeteoPi)

Un projet complet, robuste et autonome de station météo basé sur Raspberry Pi. Il comprend la collecte locale des données des capteurs, un radar météo satellite animé, un tableau de bord web complet avec graphiques historiques, des outils d'administration et des intégrations MQTT / InfluxDB / Telegram.

<p align="center">
  <img src="static/img/interface.png" alt="Interface du Tableau de Bord" width="800"/>
</p>

> **Note :** Ce projet est actuellement au stade de développement actif et d'optimisation.

---

## 🌟 Fonctionnalités Clés

*   **Surveillance en temps réel** : Mesure la température, l'humidité, la pression atmosphérique, les précipitations (pluie), la vitesse du vent, la vitesse des rafales et la direction du vent.
*   **Tableau de bord Web** : Une interface web basée sur Flask pour visualiser les conditions actuelles, inspecter l'historique et gérer le système.
*   **Visualisation des données** :
    *   Graphiques interactifs sur les dernières 48 heures (Température, Humidité, Pression, Pluie).
    *   Rose des vents interactive pour l'analyse de la direction du vent.
    *   Graphiques de cumul de pluie journalier et horaire.
    *   Statistiques Min/Max (Jour, Semaine, Mois).
*   **Préservation de la carte SD (Persistance en RAM)** : Les données actives sont écrites dans un dossier temporaire en RAM (`data/`) puis synchronisées automatiquement sur la carte SD (`data_persistent/`) au démarrage, à l'extinction et lors des sauvegardes afin de prolonger la durée de vie de la carte SD du Raspberry Pi.
*   **Sauvegardes réseau Samba automatisées** : Un service systemd planifié sauvegarde chaque jour les jeux de données (`meteo_log.csv`, `wind_detail_log.csv`) et la configuration (`config.json`) vers un partage réseau local (SMB/Windows Share), avec une détection automatique de disponibilité de l'hôte pour éviter les blocages système.
*   **Résilience Réseau & Reconnexion Auto** :
    *   Les publications MQTT et InfluxDB sont asynchrones (threads d'arrière-plan), permettant au script de capture de continuer ses mesures et de les enregistrer localement sans interruption lors d'une panne d'internet.
    *   Un watchdog Wifi autonome surveille continuellement l'interface et rétablit la connexion Wifi de manière automatique en cas de déconnexion.
*   **Radar Satellite Animé** : Télécharge automatiquement les tuiles de couverture nuageuse depuis l'API OpenWeatherMap, assemble une grille 3x3 et génère un overlay GIF animé.
*   **Affichage local LCD** : Affiche les métriques sur un écran LCD Grove RGB avec une couleur de fond variant selon la température.
*   **Intégrations matérielles et cloud** :
    *   **MQTT** : Publie des payloads de capteurs en temps réel vers un broker pour la domotique.
    *   **InfluxDB** : Envoie les métriques directement vers InfluxDB pour créer des tableaux de bord Grafana personnalisés.
    *   **Bot Telegram** : Envoie périodiquement (toutes les heures) des rapports météo détaillés à un groupe ou salon de discussion.
    *   **API Home Assistant** : Fournit un point de terminaison API JSON standard (`/api/v1/sensors`) pour faciliter l'intégration.
*   **Gestionnaire d'Historique Interactif** : Interface d'administration web permettant de visualiser, modifier ou supprimer des enregistrements de l'historique météo.

---

## 🛠 Matériel Requis

*   **Raspberry Pi** (tout modèle avec support GPIO et I2C ; optimisé pour Raspberry Pi OS Bookworm)
*   **Capteurs** :
    *   **BME280** (I2C, adresse `0x76`) : Capteur principal de température, humidité et pression atmosphérique.
    *   **DHT11** (GPIO 4) : Capteur de secours de température et humidité.
    *   **AS5600** (I2C) : Encodeur rotatif magnétique pour la direction de la girouette.
    *   **Pluviomètre** (GPIO 5) : Mécanisme à auget basculeur (calibré à `0.213 mm` par basculement).
    *   **Anemomètre** (GPIO 6) : Capteur de vitesse du vent à effet Hall.
    *   **Bouton LCD** (GPIO 26) : Bouton poussoir pour changer le mode d'affichage sur l'écran LCD.
*   **Affichage** : Écran LCD Grove RGB (I2C, adresses `0x3e` et `0x62`).

### 🖨 Sources des Pièces Imprimées en 3D
Conceptions mécaniques issues de Thingiverse :
*   **Pluviomètre** : [Thingiverse #4725413](https://www.thingiverse.com/thing:4725413)
*   **Anémomètre (Vitesse du vent)** : [Thingiverse #2559929](https://www.thingiverse.com/thing:2559929)

---

## 🔌 Schéma de Câblage

| Composant | Interface | Broche / Adresse | Détails |
| :--- | :--- | :--- | :--- |
| **BME280** | I2C | `0x76` | SCL/SDA, 3.3V, GND |
| **AS5600** | I2C | `0x36` (Par défaut) | Direction de la Girouette |
| **LCD Grove** | I2C | `0x3e` (LCD), `0x62` (RGB) | Écran à caractères |
| **DHT11** | GPIO | `GPIO 4` | Capteur de secours |
| **Pluviomètre** | GPIO | `GPIO 5` | Signal de l'auget |
| **Anémomètre** | GPIO | `GPIO 6` | Impulsions de vitesse du vent |
| **Bouton LCD** | GPIO | `GPIO 26` | Commutation des modes LCD |

---

## 📦 Installation Automatique

Le projet intègre un installateur complet (`setup.sh`) qui automatise l'installation des paquets APT, la configuration du système, les droits utilisateur, Nginx, Gunicorn et les services Systemd.

1.  **Cloner le dépôt** :
    ```bash
    git clone https://github.com/gotenash/meteopi.git
    cd meteopi
    ```

2.  **Lancer le script de configuration** :
    ```bash
    sudo ./setup.sh
    ```
    Ce script va :
    *   Mettre à jour et installer les dépendances système (`python3`, `git`, `i2c-tools`, `nginx`, `cifs-utils`).
    *   Activer l'interface I2C dans `/boot/config.txt`.
    *   Créer l'environnement virtuel Python (`venv`) et installer les dépendances nécessaires.
    *   Configurer Nginx en tant que proxy inverse vers Gunicorn.
    *   Installer et activer les démons de service systemd.

3.  **Redémarrer le Raspberry Pi** :
    ```bash
    sudo reboot
    ```

---

## 🚀 Référence des Services & Scripts

### Services Principaux (gérés via `systemd`)

*   **Collecte des Capteurs (`meteo-capteur.service`)** :
    Exécute [meteo_capteur.py](file:///c:/Users/ash/Documents/GitHub/meteopi/meteo_capteur.py). Collecte les données toutes les minutes, les enregistre dans `data/meteo_log.csv`, met à jour l'écran LCD et publie les données vers MQTT/InfluxDB.
*   **Serveur Web (`meteo-web.service`)** :
    Exécute [meteo_web.py](file:///c:/Users/ash/Documents/GitHub/meteopi/meteo_web.py) via Gunicorn. Rend le tableau de bord web disponible sur le port 80.
*   **Récupérateur Satellite (`satellite-fetcher.service`)** :
    Exécute [satellite_fetcher.py](file:///c:/Users/ash/Documents/GitHub/meteopi/satellite_fetcher.py). Récupère et assemble les cartes satellites toutes les 15 minutes.
*   **Bot Telegram (`telegram-bot.service`)** :
    Exécute [telegram_bot.py](file:///c:/Users/ash/Documents/GitHub/meteopi/telegram_bot.py). Envoie des résumés météo toutes les heures sur Telegram.
*   **Persistance RAM (`meteo-persistence.service`)** :
    Restaure les fichiers de données de la carte SD vers la RAM au démarrage et les réécrit sur la carte SD lors de l'arrêt afin de minimiser l'usure de la carte.
*   **Timer de Sauvegarde Samba (`meteo-backup.timer`)** :
    Lance quotidiennement à `03h00` le script [backup_samba.sh](file:///c:/Users/ash/Documents/GitHub/meteopi/backup_samba.sh) pour copier les données sur le réseau.
*   **Watchdog Connexion Wifi (`meteo-wifi-watchdog.service`)** :
    Exécute le script [wifi_watchdog.sh](file:///c:/Users/ash/Documents/GitHub/meteopi/wifi_watchdog.sh) en tâche de fond pour surveiller l'état de l'interface Wifi et reconnecter le Raspberry Pi en cas de coupure Wifi.

### Outils & Scripts de Diagnostic

*   [reset_password.py](file:///c:/Users/ash/Documents/GitHub/meteopi/reset_password.py) : Script en ligne de commande pour réinitialiser le mot de passe de l'administrateur du tableau de bord.
    ```bash
    ./venv/bin/python reset_password.py
    ```
*   [reparer_csv.py](file:///c:/Users/ash/Documents/GitHub/meteopi/reparer_csv.py) : Migre et convertit les anciens fichiers CSV à 7 colonnes vers le nouveau format à 8 colonnes (en ajoutant le champ des rafales `wind_gust`).
*   [convertisseur_csv.py](file:///c:/Users/ash/Documents/GitHub/meteopi/convertisseur_csv.py) : Corrige les fichiers de données en remplaçant les virgules décimales par des points pour corriger les problèmes de rendu des graphiques.
*   [test_pluviometre.py](file:///c:/Users/ash/Documents/GitHub/meteopi/test_pluviometre.py) : Permet de tester les impulsions de l'auget du pluviomètre sur le `GPIO 5`.
*   [test_anemometre.py](file:///c:/Users/ash/Documents/GitHub/meteopi/test_anemometre.py) : Diagnostique les passages d'aimants de l'anémomètre sur le `GPIO 6`.
*   [test_anemometre_lcd.py](file:///c:/Users/ash/Documents/GitHub/meteopi/test_anemometre_lcd.py) : Compteur de vitesse du vent en temps réel s'affichant sur l'écran LCD Grove.

---

## ⚙️ Fichier de Configuration (`config.json`)

La configuration système est stockée dans `config.json`. Voici l'ensemble des paramètres modifiables manuellement ou via le menu d'administration web :

```json
{
    "owm_api_key": "VOTRE_CLE_API_OPENWEATHERMAP",
    "latitude": 48.85,
    "longitude": 2.35,
    "admin_password_hash": "pbkdf2:sha256:...",
    "telegram_bot_token": "VOTRE_TOKEN_BOT_TELEGRAM",
    "telegram_chat_id": "VOTRE_CHAT_ID_TELEGRAM",
    "samba_share": "//192.168.1.100/WeatherBackups",
    "samba_user": "utilisateur_nas",
    "samba_password": "mot_de_passe_nas",
    "mqtt_enabled": true,
    "mqtt_broker": "192.168.1.50",
    "mqtt_port": 1883,
    "mqtt_user": "user_mqtt",
    "mqtt_password": "password_mqtt",
    "mqtt_topic": "meteopi/sensors",
    "influx_enabled": false,
    "influx_url": "http://localhost:8086",
    "influx_token": "VOTRE_TOKEN_INFLUXDB",
    "influx_org": "mon_org",
    "influx_bucket": "meteopi"
}
```

---

## 📊 API & Format des Données

### Point de terminaison API JSON
*   **URL** : `GET /api/v1/sensors`
*   **Format de Réponse** :
    ```json
    {
      "humidity": 45.0,
      "last_update": "2026-08-02T14:30:00",
      "pressure": 1015.2,
      "rain": 0.0,
      "temperature": 22.5,
      "wind_direction": "NE",
      "wind_speed": 12.4,
      "wind_gust": 18.2
    }
    ```

### Structure du Fichier CSV (`meteo_log.csv`)
Les enregistrements sont stockés dans `data/meteo_log.csv` sous un format à 8 colonnes :
`[Horodatage, Température (°C), Humidité (%), Pression (hPa), Pluie depuis dernier (mm), Vitesse vent (km/h), Rafale (km/h), Direction vent (str)]`
Exemple :
```csv
2026-08-02 09:15:00,21.43,58.30,1012.40,0.0000,4.20,7.80,WSW
```