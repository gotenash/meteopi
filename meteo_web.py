#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import time
import base64
from datetime import datetime, timedelta
from flask import Flask, render_template, send_file, make_response, redirect, url_for, jsonify, request, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import pandas as pd
import os
import matplotlib.pyplot as plt
import shutil
import matplotlib.font_manager as fm
import matplotlib
import matplotlib.dates as mdates # Ajout pour le formatage des dates sur l'axe X
import io
import re # Ajout du module pour les expressions régulières
import numpy as np # Ajout de numpy pour les calculs
from werkzeug.security import generate_password_hash, check_password_hash
import requests # Ajout pour les requêtes API externes
import json # Ajout pour gérer le fichier de configuration
import paho.mqtt.client as mqtt # Ajout pour MQTT
from PIL import Image # Pour la génération du fond de carte

# On désactive l'affichage de Matplotlib sur le serveur
plt.switch_backend('Agg')

def cleanup_csv_on_startup(filepath):
    """
    Vérifie et nettoie le fichier CSV au démarrage.
    Tente de réparer les lignes corrompues (contenant des caractères NUL)
    de manière plus performante et robuste.
    """
    if not os.path.exists(filepath):
        return
    
    # Expression régulière pour trouver un timestamp valide (ex: 2025-11-08 10:30:00)
    # C'est beaucoup plus robuste que de chercher seulement l'année.
    timestamp_regex = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
    
    try:
        # On lit le fichier en mode binaire pour détecter les NUL bytes
        with open(filepath, 'rb') as f:
            content = f.read()

        # On ne procède à la réécriture que si des caractères corrompus sont trouvés
        if b'\x00' not in content:
            return

        print(f"Corruption détectée dans '{filepath}'. Début du nettoyage...")
        cleaned_lines = []
        # On décode en ignorant les erreurs pour pouvoir itérer sur les lignes
        for line in content.decode('utf-8', errors='ignore').splitlines():
            # On cherche une date valide dans la ligne pour la récupérer
            match = timestamp_regex.search(line)
            if match:
                # On récupère la partie valide de la ligne à partir de la date
                recovered_line = line[match.start():]
                cleaned_lines.append(recovered_line)
            else:
                print(f"Ligne corrompue irrécupérable ignorée : {line.strip()}")

        # On réécrit le fichier original avec les lignes nettoyées
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            f.write('\n'.join(cleaned_lines) + '\n')
        print(f"Le fichier '{filepath}' a été nettoyé avec succès.")
    except Exception as e:
        print(f"Erreur critique lors du nettoyage du fichier CSV : {e}")

CONFIG_FILE = "config.json"

def load_config():
    """Charge la configuration depuis config.json."""
    default_config = {
        "owm_api_key": "METTRE_VOTRE_CLE_ICI",
        "latitude": 48.85,
        "longitude": 2.35,
        "telegram_bot_token": "METTRE_VOTRE_TOKEN_ICI",
        "telegram_chat_id": "",
        "mqtt_enabled": False,
        "mqtt_broker": "localhost",
        "mqtt_port": 1883,
        "mqtt_user": "",
        "mqtt_password": "",
        "mqtt_topic": "meteopi/sensors",
        "samba_share": "",
        "samba_user": "",
        "samba_password": ""
    }
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                saved_config = json.load(f)
                # Fusionne les valeurs par défaut avec les valeurs sauvegardées
                return {**default_config, **saved_config}
        return default_config
    except (FileNotFoundError, json.JSONDecodeError):
        return default_config

def save_config(config_data):
    """Sauvegarde la configuration dans config.json."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=4)

def read_and_process_csv(filepath):
    """
    Lit le fichier CSV, en gérant les anciens (7 colonnes) et nouveaux (8 colonnes) formats,
    et retourne un DataFrame nettoyé.
    """
    try:
        # Lire avec un nombre de colonnes flexible et sans en-tête, en traitant tous les champs comme du texte au départ
        df_raw = pd.read_csv(filepath, header=None, on_bad_lines='warn', engine='python', dtype=str, names=range(8))
        
        if df_raw.empty:
            return pd.DataFrame(columns=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_gust", "wind_dir_str"])

        # Vérifier et supprimer la ligne d'en-tête si elle existe
        if df_raw.iloc[0, 0] == 'time':
            df_raw = df_raw.iloc[1:]

        # Réinitialiser l'index après une suppression potentielle de l'en-tête
        df_raw.reset_index(drop=True, inplace=True)

        # Créer le DataFrame final en convertissant les types immédiatement pour éviter les Warnings
        df = pd.DataFrame({
            "time": df_raw[0],
            "temp": pd.to_numeric(df_raw[1], errors='coerce'),
            "hum": pd.to_numeric(df_raw[2], errors='coerce'),
            "pressure": pd.to_numeric(df_raw[3], errors='coerce'),
            "rain": pd.to_numeric(df_raw[4], errors='coerce'),
            "wind_speed": pd.to_numeric(df_raw[5], errors='coerce'),
            "wind_gust": np.nan,
            "wind_dir_str": "N/A"
        })

        # Gestion des formats (Ancien: 7 col, Nouveau: 8 col)
        is_new_format = df_raw[7].notna()
        if is_new_format.any():
            # On assigne les rafales converties en numérique
            df.loc[is_new_format, 'wind_gust'] = pd.to_numeric(df_raw.loc[is_new_format, 6], errors='coerce')
            df.loc[is_new_format, 'wind_dir_str'] = df_raw.loc[is_new_format, 7]
            
        is_old_format = ~is_new_format
        if is_old_format.any():
            df.loc[is_old_format, 'wind_dir_str'] = df_raw.loc[is_old_format, 6]
        
        # --- CORRECTION AUTO : Rafales mal placées dans la direction ---
        # On détecte si la colonne 'wind_dir_str' contient des nombres (ex: "12.5") au lieu de texte ("N", "NE")
        # et si la colonne 'wind_gust' est vide pour ces lignes.
        dir_as_num = pd.to_numeric(df['wind_dir_str'], errors='coerce')
        
        # Masque : La direction est un nombre ET la rafale est vide
        misplaced_mask = dir_as_num.notna() & df['wind_gust'].isna()
        
        if misplaced_mask.any():
            # On déplace la valeur numérique dans la bonne colonne (Rafale)
            df.loc[misplaced_mask, 'wind_gust'] = dir_as_num[misplaced_mask]
            # On marque la direction comme inconnue car elle était absente
            df.loc[misplaced_mask, 'wind_dir_str'] = "N/A"

        return df

    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_gust", "wind_dir_str"])
    except Exception as e:
        print(f"Erreur lors du traitement du fichier CSV : {e}")
        return pd.DataFrame(columns=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_gust", "wind_dir_str"])

# --- Chargement de la configuration au démarrage ---
config = load_config()

app = Flask(__name__)
# Clé secrète pour la gestion des sessions Flask (nécessaire pour le login)

# On exécute le nettoyage du CSV au démarrage de l'application
cleanup_csv_on_startup("meteo_log.csv")

# Changez cette clé pour une chaîne de caractères aléatoire !
app.secret_key = 'une-cle-secrete-tres-difficile-a-deviner'

# --- Configuration de Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirige les utilisateurs non connectés vers la page /login
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "info"

# --- Modèle Utilisateur simple ---
class User(UserMixin):
    def __init__(self, id, username, password=None, password_hash=None):
        self.id = id
        self.username = username
        if password:
            self.password_hash = generate_password_hash(password)
        elif password_hash:
            self.password_hash = password_hash
        else:
            self.password_hash = generate_password_hash("password")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

CSV_FILE = "meteo_log.csv"
WIND_CSV_FILE = "wind_detail_log.csv" # Fichier pour le vent en temps réel
PLUVIOMETER_EVENT_LOG = "pluviometer_events.log" # Chemin vers le fichier de log des événements du pluviomètre
# Calibration du pluviomètre (identique à meteo_capteur.py) - 2024-05-26 (expérimentale)
# Basé sur le test : 100ml d'eau (10mm) pour 53 basculements.
MM_PER_TIP = 0.213 # Correction pour correspondre au capteur

WINDY_THRESHOLD_KMH = 25.0 # Seuil en km/h pour considérer un épisode comme "venteux"

# --- Base de données utilisateur ---
# On récupère le hash depuis la config pour la persistance, sinon défaut "password"
admin_hash = config.get('admin_password_hash')
if not admin_hash:
    admin_hash = generate_password_hash("password")

users = {
    "1": User(id="1", username="admin", password_hash=admin_hash)
}

# On utilise maintenant les valeurs du fichier de configuration
OWM_API_KEY = config.get("owm_api_key")
LATITUDE = config.get("latitude")
LONGITUDE = config.get("longitude")

def generate_hourly_graph_base64(input_df, filter_recent=True, title="Données météo agrégées par heure (48 dernières heures)"):
    """Génère un graphique horaire à partir du DataFrame et le retourne en base64."""
    if input_df.empty:
        return None

    df = input_df.copy()
    if filter_recent:
        # Filtrer les données des dernières 48 heures
        forty_eight_hours_ago = datetime.now() - timedelta(hours=48)
        df = df[df['time'] > forty_eight_hours_ago]

    df.set_index('time', inplace=True)

    # Agréger les données par heure
    # Moyenne pour la température et l'humidité, somme pour la pluie
    df_hourly = df.resample('H').agg({'temp': 'mean', 'hum': 'mean', 'rain': 'sum'})
    df_hourly.dropna(subset=['temp', 'hum'], how='all', inplace=True) # Supprimer les heures sans données

    if df_hourly.empty:
        return None

    fig, ax1 = plt.subplots(figsize=(12, 6))

    ax1.set_xlabel("Heure")
    ax1.set_ylabel("Temp (°C) / Humidité (%)")
    # Utilisation de l'index datetime directement pour l'axe X
    ax1.plot(df_hourly.index, df_hourly["temp"], marker="o", color="tab:red", label="Température (°C)")
    ax1.plot(df_hourly.index, df_hourly["hum"], marker="o", color="tab:blue", label="Humidité (%)")
    ax1.grid(True, linestyle='--', alpha=0.6)

    # Formatage de l'axe X pour afficher uniquement l'heure et gérer l'espacement
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Hh'))
    # Définir les localisateurs pour les ticks majeurs (toutes les 3 heures) et mineurs (toutes les heures)
    # Cela aide à éviter la superposition des labels et à avoir une bonne granularité
    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    ax1.xaxis.set_minor_locator(mdates.HourLocator(interval=1))

    # Axe Y de droite pour la Pluie
    ax2 = ax1.twinx()
    ax2.set_ylabel("Pluie (mm)", color="tab:green")
    # Utilisation de l'index datetime directement pour l'axe X
    # On spécifie la largeur des barres à 1/24 d'une journée (soit 1 heure) pour un affichage précis.
    # L'alignement 'edge' place la barre à droite de son point de données, ce qui est plus intuitif pour une somme horaire.
    ax2.bar(df_hourly.index, df_hourly["rain"], width=1/24, color="tab:green", alpha=0.6, label="Pluie (mm)", align='edge')
    ax2.tick_params(axis='y', labelcolor="tab:green")

    fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
    plt.xticks(rotation=70, ha="right")
    plt.title(title)
    plt.tight_layout()

    return _save_graph_to_base64(fig)

def get_weather_prediction(df):
    """Analyse la tendance de la pression pour fournir une prédiction simple."""
    # Vérifie si la colonne 'pressure' existe et contient des données valides
    if 'pressure' not in df.columns or df['pressure'].isnull().all():
        return None # Pas de prédiction si pas de données de pression

    # On ne garde que les lignes avec des données de pression valides
    df_pressure = df.dropna(subset=['pressure'])

    if len(df_pressure) < 4:
        return "Données insuffisantes pour une prédiction."

    # On regarde les 3 dernières heures
    three_hours_ago = datetime.now() - timedelta(hours=3)
    recent_data = df_pressure[df_pressure['time'] > three_hours_ago]

    if len(recent_data) < 2:
        return "Données insuffisantes pour une prédiction."

    # Calcul de la tendance de pression (hPa par 3 heures)
    pressure_change = recent_data['pressure'].iloc[-1] - recent_data['pressure'].iloc[0]

    # Logique de prédiction
    if pressure_change < -1.6:
        return "Détérioration rapide, pluie ou vent probable."
    elif pressure_change < -0.5:
        return "Lente dégradation, temps devenant nuageux."
    elif pressure_change > 1.6:
        return "Amélioration rapide, temps s'éclaircissant."
    elif pressure_change > 0.5:
        return "Lente amélioration, temps stable."
    else:
        current_pressure = recent_data['pressure'].iloc[-1]
        if current_pressure > 1022:
            return "Temps stable et calme (haute pression)."
        elif current_pressure < 1000:
            return "Temps instable et maussade (basse pression)."
        else:
            return "Pas de changement significatif prévu."

def generate_wind_rose_base64(df):
    """Génère une rose des vents et la retourne en base64."""
    df_wind = df.dropna(subset=['wind_dir_str'])
    if df_wind.empty:
        return None

    # Ordre cardinal et conversion en radians
    directions_map = {
        'N': 0, 'NE': np.pi/4, 'E': np.pi/2, 'SE': 3*np.pi/4,
        'S': np.pi, 'SO': 5*np.pi/4, 'O': 3*np.pi/2, 'NO': 7*np.pi/4
    }
    dir_order = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO']
    
    # Calcul de la fréquence de chaque direction
    counts = df_wind['wind_dir_str'].value_counts()
    # S'assure que toutes les directions sont présentes, même avec une fréquence de 0
    freq = [counts.get(d, 0) for d in dir_order]
    angles = [directions_map[d] for d in dir_order]

    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, polar=True)
    
    # Utilise un bar plot sur un axe polaire
    ax.bar(angles, freq, width=np.pi/4, alpha=0.7, color='dodgerblue', edgecolor='k')
    
    # Configuration des labels pour les directions cardinales
    ax.set_xticks(angles)
    ax.set_xticklabels(dir_order)
    
    # Positionne les labels de rayon (fréquence)
    ax.set_rlabel_position(22.5)
    ax.tick_params(axis='y', labelsize=10)
    ax.set_title('Fréquence des Directions du Vent', pad=20, fontsize=16)
    ax.grid(True, linestyle='--', alpha=0.6)

    return _save_graph_to_base64(fig)

def generate_wind_speed_graph_48h_base64(df):
    """Génère un graphique de vitesse du vent sur 48h."""
    df_wind = df.dropna(subset=['wind_speed', 'time']).copy()
    
    # Filtrer les données des dernières 48 heures
    forty_eight_hours_ago = datetime.now() - timedelta(hours=48)
    df_wind = df_wind[df_wind['time'] > forty_eight_hours_ago]

    if len(df_wind) < 2:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Tracé de la vitesse moyenne (remplissage)
    ax.plot(df_wind['time'], df_wind['wind_speed'], color='deepskyblue', label='Vent moyen', alpha=0.7)
    ax.fill_between(df_wind['time'], df_wind['wind_speed'], color='deepskyblue', alpha=0.2)

    # Tracé des rafales (points et ligne fine)
    if 'wind_gust' in df_wind.columns:
        ax.plot(df_wind['time'], df_wind['wind_gust'], color='orange', linestyle='None', marker='o', markersize=3, label='Rafales (pics 2s)')

    # Ajout de la rafale max
    max_speed = df_wind['wind_gust'].max() if 'wind_gust' in df_wind.columns else df_wind['wind_speed'].max()
    if pd.notna(max_speed):
        ax.axhline(y=max_speed, color='red', linestyle='--', alpha=0.5, label=f'Record période: {max_speed:.1f} km/h')

    ax.set_xlabel("Heure")
    ax.set_ylabel("Vitesse (km/h)")
    ax.set_title("Vitesse du Vent (48 dernières heures)")
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # Formatage de l'axe X
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m %Hh'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()

    return _save_graph_to_base64(fig)

def generate_pressure_graph_base64(df):
    """Génère un graphique de pression sur 48h avec tendance."""
    df_pressure = df.dropna(subset=['pressure', 'time']).copy()
    
    # Filtrer les données des dernières 48 heures
    forty_eight_hours_ago = datetime.now() - timedelta(hours=48)
    df_pressure = df_pressure[df_pressure['time'] > forty_eight_hours_ago]

    if len(df_pressure) < 2:
        return None

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df_pressure['time'], df_pressure['pressure'], marker='.', linestyle='-', label='Pression mesurée', color='purple')

    # Calcul et affichage de la ligne de tendance
    x_numeric = mdates.date2num(df_pressure['time'])
    z = np.polyfit(x_numeric, df_pressure['pressure'], 1)
    p = np.poly1d(z)
    ax.plot(df_pressure['time'], p(x_numeric), "r--", label='Tendance', alpha=0.8)

    ax.set_xlabel("Heure")
    ax.set_ylabel("Pression (hPa)")
    ax.set_title("Pression Atmosphérique (48 dernières heures)")
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # Formatage de l'axe X
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m %Hh'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()

    return _save_graph_to_base64(fig)

def generate_rain_accumulation_graph_base64(df):
    """Génère un histogramme du cumul de pluie journalier sur les 7 derniers jours."""
    df_rain = df.dropna(subset=['rain', 'time']).copy()
    df_rain.set_index('time', inplace=True)

    # Agréger la pluie par jour
    daily_rain = df_rain['rain'].resample('D').sum()
    
    # Garder uniquement les 7 derniers jours
    daily_rain = daily_rain.tail(7)

    if daily_rain.empty:
        return None

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(daily_rain.index, daily_rain.values, color='mediumseagreen')

    ax.set_xlabel("Date")
    ax.set_ylabel("Cumul de Pluie (mm)")
    ax.set_title("Cumul de Pluie Journalier (7 derniers jours)")
    ax.grid(axis='y', linestyle='--', alpha=0.7)

    # Formatage de l'axe X pour afficher "Jour/Mois"
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
    plt.xticks(rotation=0, ha="center")
    fig.tight_layout()

    return _save_graph_to_base64(fig)

def generate_stats_graph_base64(stats):
    """Génère un graphique en barres pour les températures Min/Max (jour, semaine, mois)."""
    labels = ['Aujourd\'hui', 'Semaine', 'Mois']
    
    # On essaie de convertir les stats en float, en ignorant les 'N/A'
    try:
        mins = [float(stats['day'][0]), float(stats['week'][0]), float(stats['month'][0])]
        maxs = [float(stats['day'][1]), float(stats['week'][1]), float(stats['month'][1])]
    except (ValueError, TypeError):
        # Si une valeur est 'N/A', on ne génère pas le graphique
        return None

    x = range(len(labels))  # positions des labels
    width = 0.35  # largeur des barres

    fig, ax = plt.subplots(figsize=(8, 5))
    rects1 = ax.bar([i - width/2 for i in x], mins, width, label='Min', color='royalblue')
    rects2 = ax.bar([i + width/2 for i in x], maxs, width, label='Max', color='crimson')

    # Ajout des labels, titre et legendes
    ax.set_ylabel('Température (°C)')
    ax.set_title('Écarts de Température (Min/Max)')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis='y', linestyle='--', alpha=0.7)

    # Ajout des valeurs au-dessus des barres
    for rect in rects1 + rects2:
        height = rect.get_height()
        ax.annotate(f'{height:.1f}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),  # 3 points de décalage vertical
                    textcoords="offset points",
                    ha='center', va='bottom')

    fig.tight_layout()
    return _save_graph_to_base64(fig)

def generate_wind_graph_base64(df):
    """Génère un graphique de la vitesse du vent sur les 6 dernières heures."""
    df_wind = df.dropna(subset=['wind_speed', 'time']).copy()
    
    # Filtrer les données des dernières 6 heures
    six_hours_ago = datetime.now() - timedelta(hours=6)
    df_wind = df_wind[df_wind['time'] > six_hours_ago]

    if df_wind.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 4))
    
    # Tracé de la vitesse
    ax.plot(df_wind['time'], df_wind['wind_speed'], color='tab:blue', linewidth=2, label='Moyenne (1 min)')
    ax.fill_between(df_wind['time'], df_wind['wind_speed'], color='tab:blue', alpha=0.2)

    # Tracé des rafales si disponibles
    if 'wind_gust' in df_wind.columns:
        ax.plot(df_wind['time'], df_wind['wind_gust'], color='orange', linestyle='None', marker='.', markersize=4, label='Rafales (3 sec)')
        # Ajout d'une ligne pour le record sur la période
        max_gust = df_wind['wind_gust'].max()
        if pd.notna(max_gust):
            ax.axhline(y=max_gust, color='red', linestyle='--', alpha=0.3, label=f'Max: {max_gust:.1f} km/h')

    ax.set_ylabel("Vitesse (km/h)")
    ax.set_title("Vent (6 dernières heures)")
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.6)
    
    # Formatage de l'axe X
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.xticks(rotation=0, ha="center")
    
    fig.tight_layout()

    return _save_graph_to_base64(fig)

def get_rain_summary(df, start_time=None, end_time=None):
    """Analyse les données de pluie et génère un résumé textuel."""
    if df.empty:
        return "Données de pluie non disponibles."

    now = datetime.now()
    if start_time is None:
        start_time = now - timedelta(hours=24)
    
    # Filtrer les données selon la période demandée
    mask = df['time'] > start_time
    if end_time:
        mask = mask & (df['time'] <= end_time)
        
    df_period = df[mask].copy()
    rain_events = df_period[df_period['rain'] > 0].copy()

    if rain_events.empty:
        return "Pas de pluie détectée sur cette période."

    # Ajout d'une colonne 'date' pour pouvoir regrouper par jour
    rain_events['date'] = rain_events['time'].dt.date

    # On regroupe d'abord par jour
    daily_summaries = []
    for date, daily_group in rain_events.groupby('date'):
        
        # On identifie les épisodes de pluie au sein de la journée
        daily_group = daily_group.copy() # Pour éviter les avertissements pandas
        daily_group['time_diff'] = daily_group['time'].diff()
        episode_id = (daily_group['time_diff'] > timedelta(minutes=30)).cumsum()

        if date == now.date():
            day_str = "Aujourd'hui"
        elif date == (now - timedelta(days=1)).date():
            day_str = f"Hier ({date.strftime('%d/%m')})"
        else:
            day_str = f"Le {date.strftime('%d/%m')}"
        
        episode_summaries = []
        for _, episode in daily_group.groupby(episode_id):
            ep_start = episode['time'].min()
            ep_end = episode['time'].max()
            total_rain = episode['rain'].sum()

            if len(episode) == 1:
                episode_summaries.append(f"Averse de {total_rain:.2f} mm vers {ep_start.strftime('%Hh%M')}")
            else:
                duration = (ep_end - ep_start).total_seconds() / 60
                if duration > 5:
                    episode_summaries.append(f"{total_rain:.2f} mm entre {ep_start.strftime('%Hh%M')} et {ep_end.strftime('%Hh%M')}")
                else:
                    episode_summaries.append(f"{total_rain:.2f} mm autour de {ep_start.strftime('%Hh%M')}")
        
        # On assemble le résumé pour la journée
        if episode_summaries:
            daily_summaries.append(f"<strong>{day_str}:</strong><br>" + "<br>".join(reversed(episode_summaries)))
        else:
            daily_summaries.append(f"<strong>{day_str}:</strong> Pas de pluie enregistrée.")
    
    return "<br><br>".join(reversed(daily_summaries)) # On sépare les jours par un double saut de ligne

def get_temp_hum_summary(df):
    """Analyse la tendance de la température et de l'humidité sur les 3 dernières heures."""
    if df.empty or len(df) < 2:
        return None

    # On ne garde que les lignes avec des données de température et d'humidité valides
    df_filtered = df.dropna(subset=['temp', 'hum'])

    # On regarde les 3 dernières heures
    three_hours_ago = datetime.now() - timedelta(hours=3)
    recent_data = df_filtered[df_filtered['time'] > three_hours_ago].copy()

    if len(recent_data) < 2:
        return "Données récentes insuffisantes pour une analyse de tendance."

    # Calcul des changements
    temp_change = recent_data['temp'].iloc[-1] - recent_data['temp'].iloc[0]
    hum_change = recent_data['hum'].iloc[-1] - recent_data['hum'].iloc[0]

    # --- Analyse de la température ---
    if temp_change > 0.8:
        temp_trend = f"en hausse ({temp_change:+.1f}°C)"
    elif temp_change > 0.2:
        temp_trend = "en légère hausse"
    elif temp_change < -0.8:
        temp_trend = f"en baisse ({temp_change:+.1f}°C)"
    elif temp_change < -0.2:
        temp_trend = "en légère baisse"
    else:
        temp_trend = "stable"

    # --- Construction de la phrase ---
    return f"Tendance sur 3h : Température {temp_trend}."

def get_wind_summary(df, start_time=None, end_time=None):
    """Analyse les données de vent et génère un résumé textuel des épisodes venteux."""
    if df.empty or 'wind_speed' not in df.columns:
        return "Données de vent non disponibles."

    now = datetime.now()
    if start_time is None:
        start_time = now - timedelta(hours=24)
    
    # Filtrer les données selon la période demandée
    mask = df['time'] > start_time
    if end_time:
        mask = mask & (df['time'] <= end_time)
        
    df_period = df[mask].copy()
    
    # On ne garde que les rafales significatives
    wind_events = df_period[df_period['wind_speed'] >= WINDY_THRESHOLD_KMH].copy()

    if wind_events.empty:
        max_wind_today = df_period['wind_speed'].max()
        if pd.notna(max_wind_today):
             return f"Pas de vent fort aujourd'hui. Rafale max : {max_wind_today:.1f} km/h."
        return "Pas de vent fort détecté sur cette période."

    # Ajout d'une colonne 'date' pour pouvoir regrouper par jour
    wind_events['date'] = wind_events['time'].dt.date

    # On regroupe d'abord par jour
    daily_summaries = []
    for date, daily_group in wind_events.groupby('date'):
        
        # On identifie les épisodes de vent au sein de la journée
        daily_group = daily_group.copy() # Pour éviter les avertissements pandas
        # Un nouvel épisode commence si deux rafales sont espacées de plus de 30 minutes
        daily_group['time_diff'] = daily_group['time'].diff()
        episode_id = (daily_group['time_diff'] > timedelta(minutes=30)).cumsum()

        day_str = "Aujourd'hui" if date == now.date() else f"Le {date.strftime('%d/%m')}"
        
        episode_summaries = []
        for _, episode in daily_group.groupby(episode_id):
            peak_wind_speed = episode['wind_speed'].max()
            peak_event = episode.loc[episode['wind_speed'].idxmax()]
            peak_wind_time = peak_event['time']
            peak_wind_dir = peak_event['wind_dir_str'] if pd.notna(peak_event['wind_dir_str']) else ""

            episode_summaries.append(f"Rafale à {peak_wind_speed:.1f} km/h ({peak_wind_dir}) vers {peak_wind_time.strftime('%Hh%M')}")
        
        if episode_summaries:
            daily_summaries.append(f"<strong>{day_str}:</strong><br>" + "<br>".join(reversed(episode_summaries)))
    
    return "<br><br>".join(reversed(daily_summaries)) if daily_summaries else "Pas de vent fort détecté sur cette période."

def send_telegram_message(token, chat_id, message):
    """Envoie un message à un chat Telegram et retourne un statut."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            return True, "Message de test envoyé avec succès."
        else:
            error_text = response.text
            try:
                error_json = response.json()
                if 'description' in error_json:
                    error_text = error_json['description']
            except json.JSONDecodeError:
                pass # Garder le texte brut si ce n'est pas du JSON
            return False, f"Erreur {response.status_code}: {error_text}"
    except requests.RequestException as e:
        return False, f"Erreur de connexion à l'API Telegram: {e}"

def latlon_to_tile_coords(lat, lon, zoom):
    """Convertit des coordonnées GPS en coordonnées de tuile OpenStreetMap."""
    import math
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

@login_manager.user_loader
def load_user(user_id):
    """Charge un utilisateur à partir de son ID pour Flask-Login."""
    return users.get(user_id)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Page de connexion."""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # On cherche l'utilisateur par son nom
        user_to_check = next((user for user in users.values() if user.username == username), None)

        if user_to_check and user_to_check.check_password(password):
            login_user(user_to_check)
            # Redirige vers la page demandée initialement, ou vers l'accueil
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        else:
            flash('Identifiant ou mot de passe incorrect.', 'danger')

    return render_template('login.html')

# --- Fonctions utilitaires pour les couleurs dynamiques ---
def get_color_from_value(value):
    """Retourne une couleur (r,g,b) interpolée pour une température donnée."""
    # Pivots : (Température, R, G, B)
    # On définit ici l'échelle de couleur : Bleu < 0, Vert ~10, Jaune ~18, Rouge > 25
    stops = [
        (-10, 52, 152, 219), # #3498db Blue (Froid)
        (0,   52, 152, 219), # #3498db Blue (Zéro)
        (10,  46, 204, 113), # #2ecc71 Green (Frais)
        (18,  241, 196, 15), # #f1c40f Yellow (Doux)
        (25,  231, 76, 60),  # #e74c3c Red (Chaud)
        (40,  231, 76, 60)   # #e74c3c Red (Très chaud)
    ]
    
    # Si hors limites, on prend la couleur extrême
    if value <= stops[0][0]: return stops[0][1:]
    if value >= stops[-1][0]: return stops[-1][1:]
    
    # Interpolation linéaire entre deux pivots
    for i in range(len(stops) - 1):
        t1, r1, g1, b1 = stops[i]
        t2, r2, g2, b2 = stops[i+1]
        if t1 <= value <= t2:
            ratio = (value - t1) / (t2 - t1)
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            return (r, g, b)
    return (128, 128, 128) # Gris par défaut

def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)

def get_temp_gradient(min_t, max_t):
    """Génère un string CSS linear-gradient dynamique."""
    stops_vals = [0, 10, 18, 25] # Les températures pivots à inclure dans le dégradé
    gradient_parts = []
    
    # Couleur de début (Min)
    c_start = rgb_to_hex(get_color_from_value(min_t))
    gradient_parts.append(f"{c_start} 0%")
    
    # Ajout des pivots intermédiaires s'ils sont dans la plage [min_t, max_t]
    range_t = max_t - min_t
    if range_t > 0.1:
        for stop in stops_vals:
            if min_t < stop < max_t:
                pct = (stop - min_t) / range_t * 100
                c_stop = rgb_to_hex(get_color_from_value(stop))
                gradient_parts.append(f"{c_stop} {pct:.1f}%")
    
    # Couleur de fin (Max)
    c_end = rgb_to_hex(get_color_from_value(max_t))
    gradient_parts.append(f"{c_end} 100%")
    
    return f"linear-gradient(90deg, {', '.join(gradient_parts)})"

def read_log_tail(filename, num_lines=30):
    """Lit les dernières lignes d'un fichier de log."""
    if not os.path.exists(filename):
        return f"Fichier non trouvé : {filename} (Vérifiez le dossier logs/)"
    try:
        with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
            # On lit tout le fichier (supposé de taille raisonnable pour des logs rotatifs)
            lines = f.readlines()
            return "".join(lines[-num_lines:])
    except Exception as e:
        return f"Erreur de lecture : {e}"

@app.route("/")
@login_required
def home():
    # Initialisation des variables
    temp, hum, pressure, rain, wind, wind_gust, wind_dir, last_update, prediction, rain_summary, temp_hum_summary, wind_summary, wind_graph = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "", "inconnue", None, "Analyse en cours...", None, "Analyse en cours...", None
    stats = {}
    scale_min, scale_max = 100, -100 # Valeurs initiales pour déterminer l'échelle des barres
    rain_scale_max, wind_scale_max = 0, 0 # Valeurs max pour les échelles
    press_scale_min, press_scale_max = 2000, 0 # Valeurs initiales pour l'échelle de pression

    try:
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            # Conversion des types, en gérant les erreurs
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df['pressure'] = pd.to_numeric(df['pressure'], errors='coerce')
            df['temp'] = pd.to_numeric(df['temp'], errors='coerce')
            df['hum'] = pd.to_numeric(df['hum'], errors='coerce')
            df['rain'] = pd.to_numeric(df['rain'], errors='coerce')
            df['wind_speed'] = pd.to_numeric(df['wind_speed'], errors='coerce')
            df['wind_gust'] = pd.to_numeric(df['wind_gust'], errors='coerce')
            df.dropna(subset=['time'], inplace=True) # On supprime les lignes où la date est invalide

            last_reading = df.iloc[-1]
            temp = f"{last_reading['temp']:.1f}"
            wind = f"{last_reading['wind_speed']:.1f}"
            wind_gust = f"{last_reading['wind_gust']:.1f}" if pd.notna(last_reading['wind_gust']) else "N/A"
            wind_dir = last_reading['wind_dir_str'] if pd.notna(last_reading['wind_dir_str']) else ""
            pressure = f"{last_reading['pressure']:.1f}" if pd.notna(last_reading['pressure']) else "N/A"
            hum = f"{last_reading['hum']:.0f}"
            
            # Calcul du cumul de pluie sur les dernières 24h
            last_24h = df[df['time'] > (datetime.now() - timedelta(hours=24))]
            rain_24h = last_24h['rain'].sum()
            rain = f"{rain_24h:.2f}"

            # --- Calcul des statistiques Min/Max ---
            now = datetime.now()
            
            periods = [
                ('day', "Aujourd'hui", now.replace(hour=0, minute=0, second=0, microsecond=0)),
                ('week', "Semaine", (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)),
                ('month', "Mois", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
            ]

            for key, label, start_time in periods:
                df_period = df[df['time'] >= start_time]
                
                # Default values if no data for the period
                p_min, p_max = np.nan, np.nan
                press_min, press_max = np.nan, np.nan
                has_press = False
                w_mean, w_max = np.nan, np.nan
                rain_sum = 0.0
                icon = "☀️" # Default icon
                gradient = "none"

                if not df_period.empty:
                    p_min = df_period['temp'].min()
                    p_max = df_period['temp'].max()
                    
                    press_min = df_period['pressure'].min()
                    press_max = df_period['pressure'].max()
                    has_press = not pd.isna(press_min)
                    
                    if has_press:
                        if pd.notna(press_min) and press_min < press_scale_min: press_scale_min = press_min
                        if pd.notna(press_max) and press_max > press_scale_max: press_scale_max = press_max

                    w_mean = df_period['wind_speed'].mean()
                    w_max = df_period['wind_gust'].max() if 'wind_gust' in df_period.columns else df_period['wind_speed'].max()
                    if pd.notna(w_max) and w_max > wind_scale_max: wind_scale_max = w_max

                    rain_sum = df_period['rain'].sum()
                    press_mean = df_period['pressure'].mean()
                    if rain_sum > 0.2:
                        icon = "🌧️"
                    elif pd.notna(press_mean) and press_mean < 1015:
                        icon = "☁️"
                    else:
                        icon = "☀️"
                    gradient = get_temp_gradient(p_min, p_max)

                    # Update global scales only if data is valid
                    if pd.notna(p_min) and p_min < scale_min: scale_min = p_min
                    if pd.notna(p_max) and p_max > scale_max: scale_max = p_max
                    if rain_sum > rain_scale_max: rain_scale_max = rain_sum

                stats[key] = {
                    'label': label,
                    'min': p_min if pd.notna(p_min) else 0,
                    'max': p_max if pd.notna(p_max) else 0,
                    'min_str': f"{p_min:.1f}" if pd.notna(p_min) else "N/A",
                    'max_str': f"{p_max:.1f}" if pd.notna(p_max) else "N/A",
                    'icon': icon,
                    'rain_total': rain_sum,
                    'press_min': press_min if pd.notna(press_min) else 0,
                    'press_max': press_max if pd.notna(press_max) else 0,
                    'has_press': has_press,
                    'date_iso': now.strftime('%Y-%m-%d') if key == 'day' else None,
                    'gradient': gradient,
                    'wind_mean': w_mean if pd.notna(w_mean) else 0,
                    'wind_max': w_max if pd.notna(w_max) else 0,
                }

            # --- Ajout des 5 derniers jours ---
            days_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
            for i in range(1, 6):
                d = now - timedelta(days=i)
                start = d.replace(hour=0, minute=0, second=0, microsecond=0)
                end = d.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                df_day = df[(df['time'] >= start) & (df['time'] <= end)]
                
                
                # Default values if no data for the day
                p_min, p_max = np.nan, np.nan
                press_min, press_max = np.nan, np.nan
                has_press = False
                w_mean, w_max = np.nan, np.nan
                rain_sum = 0.0
                icon = "☀️" # Default icon
                gradient = "none"

                if not df_day.empty:
                    p_min = df_day['temp'].min()
                    p_max = df_day['temp'].max()
                    
                    press_min = df_day['pressure'].min()
                    press_max = df_day['pressure'].max()
                    has_press = not pd.isna(press_min)
                    
                    if has_press:
                        if pd.notna(press_min) and press_min < press_scale_min: press_scale_min = press_min
                        if pd.notna(press_max) and press_max > press_scale_max: press_scale_max = press_max

                    w_mean = df_day['wind_speed'].mean()
                    w_max = df_day['wind_gust'].max() if 'wind_gust' in df_day.columns else df_day['wind_speed'].max()
                    if pd.notna(w_max) and w_max > wind_scale_max: wind_scale_max = w_max

                    rain_sum = df_day['rain'].sum()
                    press_mean = df_day['pressure'].mean()
                    if rain_sum > 0.2:
                        icon = "🌧️"
                    elif pd.notna(press_mean) and press_mean < 1015:
                        icon = "☁️"
                    else:
                        icon = "☀️"
                    gradient = get_temp_gradient(p_min, p_max)

                    # Update global scales only if data is valid
                    if pd.notna(p_min) and p_min < scale_min: scale_min = p_min
                    if pd.notna(p_max) and p_max > scale_max: scale_max = p_max
                    if rain_sum > rain_scale_max: rain_scale_max = rain_sum

                stats[f'day_{i}'] = {
                    'label': days_fr[d.weekday()],
                    'min': p_min if pd.notna(p_min) else 0,
                    'max': p_max if pd.notna(p_max) else 0,
                    'min_str': f"{p_min:.1f}" if pd.notna(p_min) else "N/A",
                    'max_str': f"{p_max:.1f}" if pd.notna(p_max) else "N/A",
                    'icon': icon,
                    'rain_total': rain_sum,
                    'press_min': press_min if pd.notna(press_min) else 0,
                    'press_max': press_max if pd.notna(press_max) else 0,
                    'has_press': has_press,
                    'date_iso': d.strftime('%Y-%m-%d'),
                    'gradient': gradient,
                    'wind_mean': w_mean if pd.notna(w_mean) else 0,
                    'wind_max': w_max if pd.notna(w_max) else 0,
                }

            # Ajout d'une petite marge pour l'affichage graphique
            if scale_min != 100: scale_min -= 2
            if scale_max != -100: scale_max += 2
            
            # Marges pour la pression
            if press_scale_min == 2000: press_scale_min = 980 # Valeur par défaut si pas de données
            if press_scale_max == 0: press_scale_max = 1040
            press_scale_min -= 2
            press_scale_max += 2
            
            # Génération de la prédiction (uniquement si des données de pression existent)
            prediction = get_weather_prediction(df)
            
            # Génération du graphique de vent (6h)
            wind_graph = generate_wind_graph_base64(df)

            last_update = last_reading['time'].strftime("%d/%m/%Y à %H:%M:%S")

    except (FileNotFoundError, pd.errors.EmptyDataError):
        # Le fichier n'existe pas encore ou est vide
        pass
    
    # On déplace les appels aux fonctions d'analyse ici pour plus de clarté
    rain_summary = get_rain_summary(df) if 'df' in locals() and not df.empty else "Données non disponibles."
    temp_hum_summary = get_temp_hum_summary(df) if 'df' in locals() and not df.empty else None
    wind_summary = get_wind_summary(df) if 'df' in locals() and not df.empty else "Données non disponibles."
    
    return render_template("home.html", temp=temp, hum=hum, pressure=pressure, rain=rain, wind=wind, wind_gust=wind_gust, wind_dir=wind_dir, last_update=last_update, prediction=prediction, stats=stats, scale_min=scale_min, scale_max=scale_max, press_scale_min=press_scale_min, press_scale_max=press_scale_max, rain_scale_max=rain_scale_max, wind_scale_max=wind_scale_max, rain_summary=rain_summary, temp_hum_summary=temp_hum_summary, wind_summary=wind_summary, wind_graph=wind_graph)

@app.route("/pluviometer_logs")
@login_required
def pluviometer_logs_page():
    """Affiche les logs en direct du pluviomètre."""
    logs_content = "Aucun basculement enregistré pour le moment."
    total_tips = 0
    total_rain = 0.0
    try:
        with open(PLUVIOMETER_EVENT_LOG, "r") as f:
            lines = f.readlines()
            if lines:
                logs_content = "".join(lines)
                total_tips = len(lines)
                total_rain = total_tips * MM_PER_TIP
    except FileNotFoundError:
        pass # Le fichier n'existe pas encore
    return render_template("pluviometer_logs.html", 
                           logs_content=logs_content, 
                           total_tips=total_tips, 
                           total_rain=f"{total_rain:.2f}")

@app.route("/admin/clear_data", methods=['POST'])
@login_required
def admin_clear_data():
    """Efface les fichiers de données (CSV principal et logs du pluviomètre)."""
    try:
        # Supprime le fichier de données principal. Le script capteur le recréera.
        if os.path.exists(CSV_FILE):
            os.remove(CSV_FILE)
        
        # Supprime également le fichier de log des basculements pour la cohérence.
        if os.path.exists(PLUVIOMETER_EVENT_LOG):
            os.remove(PLUVIOMETER_EVENT_LOG)
            
    except Exception as e:
        print(f"Erreur lors de l'effacement des fichiers de données : {e}")
    
    flash("Toutes les données ont été effacées.", "success")
    return redirect(url_for('admin_page'))

@app.route("/admin/clear_pluviometer_logs", methods=['POST'])
@login_required
def admin_clear_pluviometer_logs():
    """Efface le contenu du fichier de log du pluviomètre."""
    try:
        with open(PLUVIOMETER_EVENT_LOG, "w") as f:
            # Le simple fait d'ouvrir en mode 'w' efface le fichier.
            pass
    except Exception as e:
        print(f"Erreur lors de l'effacement du fichier de log : {e}")
    
    flash("Les logs du pluviomètre ont été effacés.", "success")
    return redirect(url_for('pluviometer_logs_page'))

@app.route('/history/delete', methods=['POST'])
@login_required
def delete_history_line():
    """Supprime une ligne de l'historique basée sur son timestamp."""
    timestamp = request.form.get('timestamp')
    if not timestamp:
        flash("Identifiant de ligne manquant.", "danger")
        return redirect(url_for('history'))
    
    try:
        lines = []
        found = False
        # Lecture du fichier CSV et filtrage de la ligne à supprimer
        if os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header:
                    lines.append(header)
                
                for row in reader:
                    if len(row) > 0 and row[0] == timestamp:
                        found = True
                        continue # On saute (supprime) cette ligne
                    lines.append(row)
            
            if found:
                with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerows(lines)
                flash(f"Mesure du {timestamp} supprimée avec succès.", "success")
            else:
                flash("Ligne introuvable dans le fichier.", "warning")
        else:
            flash("Fichier de données introuvable.", "danger")
            
    except Exception as e:
        flash(f"Erreur lors de la suppression : {e}", "danger")
        
    return redirect(url_for('history'))

@app.route('/history/update', methods=['POST'])
@login_required
def update_history_line():
    """Met à jour une ligne de l'historique."""
    original_time = request.form.get('original_time')
    
    if not original_time:
        flash("Identifiant de ligne manquant.", "danger")
        return redirect(url_for('history'))

    # Récupération des nouvelles valeurs
    new_temp = request.form.get('temp')
    new_hum = request.form.get('hum')
    new_pressure = request.form.get('pressure')
    new_rain = request.form.get('rain')
    new_wind = request.form.get('wind_speed')
    new_gust = request.form.get('wind_gust')
    new_wind_dir = request.form.get('wind_dir')

    try:
        lines = []
        updated = False
        if os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header:
                    lines.append(header)
                
                for row in reader:
                    if len(row) > 0 and row[0] == original_time:
                        # Mise à jour de la ligne (on conserve l'ordre du CSV)
                        lines.append([original_time, new_temp, new_hum, new_pressure, new_rain, new_wind, new_gust, new_wind_dir])
                        updated = True
                    else:
                        lines.append(row)
            
            if updated:
                # Écriture atomique via un fichier temporaire pour la sécurité
                temp_file = CSV_FILE + '.tmp'
                with open(temp_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerows(lines)
                shutil.move(temp_file, CSV_FILE)
                flash(f"Mesure du {original_time} mise à jour avec succès.", "success")
            else:
                flash("Ligne introuvable pour mise à jour.", "warning")
    except Exception as e:
        flash(f"Erreur lors de la mise à jour : {e}", "danger")

    return redirect(url_for('history'))

@app.route('/history')
@login_required
def history():
    """Affiche l'historique complet des données avec pagination et filtrage par date."""
    try:
        df = read_and_process_csv(CSV_FILE)
        df.dropna(subset=['time'], inplace=True) # On s'assure que la colonne 'time' n'est pas vide
        df['time'] = pd.to_datetime(df['time'], errors='coerce')
        df.dropna(subset=['time'], inplace=True) # On supprime les lignes où la conversion de date a échoué

        # Conversion des colonnes en numérique pour éviter les erreurs de formatage
        numeric_cols = ['temp', 'hum', 'pressure', 'rain', 'wind_speed', 'wind_gust']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # --- Logique de filtrage par date ---
        start_date_str = request.args.get('start_date', '')
        end_date_str = request.args.get('end_date', '')
        
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            df = df[df['time'] >= start_date]
        
        if end_date_str:
            # On ajoute un jour et on compare à "inférieur à" pour inclure toute la journée de la date de fin.
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
            df = df[df['time'] < end_date]

        # On inverse le DataFrame pour avoir les données les plus récentes en premier
        df = df.iloc[::-1]

        # Logique de pagination
        page = request.args.get('page', 1, type=int)
        per_page = 50  # 50 entrées par page
        total_rows = len(df) 
        total_pages = (total_rows + per_page - 1) // per_page
        
        if page < 1: page = 1
        if page > total_pages and total_pages > 0: page = total_pages

        start = (page - 1) * per_page
        end = start + per_page
        df_page = df.iloc[start:end].copy() # .copy() pour éviter un avertissement

        # --- Calcul de la pagination intelligente ---
        # Génère une liste comme [1, None, 49, 50, 51, None, 100] où None deviendra "..."
        pagination_iter = []
        if total_pages > 1:
            if total_pages <= 7:
                pagination_iter = list(range(1, total_pages + 1))
            else:
                pages_set = set()
                # Toujours afficher la première et la dernière
                pages_set.add(1)
                pages_set.add(total_pages)
                # Afficher autour de la page courante (ex: page-2 à page+2)
                for p in range(page - 2, page + 3):
                    if 1 <= p <= total_pages:
                        pages_set.add(p)
                
                sorted_pages = sorted(list(pages_set))
                
                prev = None
                for p in sorted_pages:
                    if prev is not None:
                        if p > prev + 1:
                            pagination_iter.append(None) # Trou détecté
                    pagination_iter.append(p)
                    prev = p

        # Préparation des données pour le template (liste de dictionnaires)
        history_data = []
        for _, row in df_page.iterrows():
            history_data.append({
                'original_time': row['time'].strftime('%Y-%m-%d %H:%M:%S'), # Identifiant unique pour suppression
                'display_time': row['time'].strftime('%d/%m/%Y %H:%M'),
                'temp': f"{row['temp']:.1f}" if pd.notna(row['temp']) else "",
                'hum': f"{row['hum']:.0f}" if pd.notna(row['hum']) else "",
                'pressure': f"{row['pressure']:.1f}" if pd.notna(row['pressure']) else "",
                'rain': f"{row['rain']:.3f}" if pd.notna(row['rain']) else "",
                'wind_speed': f"{row['wind_speed']:.1f}" if pd.notna(row['wind_speed']) else "",
                'wind_gust': f"{row['wind_gust']:.1f}" if pd.notna(row['wind_gust']) else "",
                'wind_dir': row['wind_dir_str'] if pd.notna(row['wind_dir_str']) else ""
            })

    except (FileNotFoundError, pd.errors.EmptyDataError):
        history_data = []
        page, total_pages, start_date_str, end_date_str = 1, 1, '', ''
        pagination_iter = []

    return render_template('history.html', history_data=history_data, current_page=page, total_pages=total_pages, start_date=start_date_str, end_date=end_date_str, pagination_iter=pagination_iter)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_page():
    """Page d'administration pour les actions sensibles."""
    # --- Récupération des logs ---
    # Les chemins sont relatifs au dossier d'exécution (défini dans run_all.sh)
    log_files = {
        'Capteurs (capteur.log)': 'logs/capteur.log',
        'Satellite (satellite.log)': 'logs/satellite.log',
        'Serveur Web (gunicorn.log)': 'logs/gunicorn.log'
    }
    
    logs_data = {}
    for label, path in log_files.items():
        logs_data[label] = read_log_tail(path)

    # --- État du système (basé sur la fraîcheur du CSV) ---
    system_status = {'active': False, 'last_update': 'Inconnu', 'csv_size': '0 Ko'}
    
    if os.path.exists(CSV_FILE):
        try:
            mtime = os.path.getmtime(CSV_FILE)
            size = os.path.getsize(CSV_FILE)
            last_mod = datetime.fromtimestamp(mtime)
            system_status['last_update'] = last_mod.strftime("%d/%m/%Y %H:%M:%S")
            system_status['csv_size'] = f"{size / 1024:.1f} Ko"
            if datetime.now() - last_mod < timedelta(minutes=5):
                system_status['active'] = True
        except Exception: pass

    return render_template('admin.html', config=config, logs=logs_data, system_status=system_status)

@app.route('/admin/update_config', methods=['POST'])
@login_required
def admin_update_config():
    """Met à jour le fichier de configuration."""
    # On utilise un bloc try...except pour intercepter les erreurs de conversion (ex: latitude non numérique)
    try:
        # On charge la config existante pour ne pas écraser des clés non présentes dans le formulaire (ex: mot de passe)
        current_config = load_config()
        
        current_config["owm_api_key"] = request.form.get('owm_api_key', '')
        current_config["latitude"] = float(request.form.get('latitude', 0))
        current_config["longitude"] = float(request.form.get('longitude', 0))
        current_config["telegram_bot_token"] = request.form.get('telegram_bot_token', '')
        current_config["telegram_chat_id"] = request.form.get('telegram_chat_id', '')
        
        # Ajout des paramètres Samba
        current_config["samba_share"] = request.form.get('samba_share', '')
        current_config["samba_user"] = request.form.get('samba_user', '')
        current_config["samba_password"] = request.form.get('samba_password', '')
        
        # Paramètres MQTT
        current_config["mqtt_enabled"] = request.form.get('mqtt_enabled') == 'on'
        current_config["mqtt_broker"] = request.form.get('mqtt_broker', 'localhost')
        current_config["mqtt_port"] = int(request.form.get('mqtt_port') or 1883)
        current_config["mqtt_user"] = request.form.get('mqtt_user', '')
        current_config["mqtt_password"] = request.form.get('mqtt_password', '')
        current_config["mqtt_topic"] = request.form.get('mqtt_topic', 'meteopi/sensors')

        save_config(current_config)
        
        # On recharge la configuration pour la session en cours
        global config, LATITUDE, LONGITUDE, OWM_API_KEY
        config = current_config
        # Utilisation de valeurs de secours sécurisées pour éviter float(None)
        LATITUDE = float(config.get('latitude') if config.get('latitude') is not None else 48.85)
        LONGITUDE = float(config.get('longitude') if config.get('longitude') is not None else 2.35)
        OWM_API_KEY = config.get('owm_api_key', '')

        flash("Configuration mise à jour avec succès !", "success")
    except ValueError:
        flash("Erreur : Vérifiez que les champs numériques (Latitude, Longitude, Port MQTT) sont corrects.", "danger")
    except Exception as e:
        flash(f"Une erreur est survenue : {e}", "danger")
    return redirect(url_for('admin_page'))

@app.route('/admin/test_telegram', methods=['POST'])
@login_required
def admin_test_telegram():
    """Envoie un message de test Telegram."""
    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")

    if not token or not chat_id:
        flash("Veuillez d'abord configurer et sauvegarder le Token et le Chat ID.", "warning")
    else:
        success, message = send_telegram_message(token, chat_id, "🔔 Ceci est un message de test de votre Station Météo.")
        if success:
            flash(message, "success")
        else:
            flash(message, "danger")

    return redirect(url_for('admin_page'))

@app.route('/admin/change_password', methods=['POST'])
@login_required
def admin_change_password():
    """Change le mot de passe de l'administrateur."""
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    # Vérification du mot de passe actuel
    if not current_user.check_password(current_password):
        flash("Le mot de passe actuel est incorrect.", "danger")
        return redirect(url_for('admin_page'))

    if new_password != confirm_password:
        flash("Les nouveaux mots de passe ne correspondent pas.", "danger")
        return redirect(url_for('admin_page'))

    # Mise à jour du hash
    new_hash = generate_password_hash(new_password)
    
    # Mise à jour en mémoire et dans la config
    users[current_user.id].password_hash = new_hash
    config['admin_password_hash'] = new_hash
    save_config(config)

    flash("Mot de passe modifié avec succès.", "success")
    return redirect(url_for('admin_page'))

@app.route("/download")
@login_required
def download():
    return send_file(CSV_FILE, as_attachment=True)

@app.route("/download_wind_detail")
@login_required
def download_wind_detail():
    """Permet de télécharger le fichier de logs détaillés du vent."""
    if not os.path.exists(WIND_CSV_FILE):
        flash("Le fichier de logs détaillés n'existe pas encore.", "warning")
        return redirect(url_for('admin_page'))
    return send_file(WIND_CSV_FILE, as_attachment=True)

@app.route("/download_config")
@login_required
def download_config():
    return send_file(CONFIG_FILE, as_attachment=True)

@app.route('/admin/upload_csv', methods=['POST'])
@login_required
def admin_upload_csv():
    """Restaure un fichier CSV de sauvegarde."""
    if 'file' not in request.files:
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_page'))
    
    file = request.files['file']
    
    if file.filename == '':
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_page'))
    
    if file and file.filename.endswith('.csv'):
        try:
            # Sauvegarde de sécurité du fichier actuel avant écrasement
            if os.path.exists(CSV_FILE):
                shutil.copy(CSV_FILE, CSV_FILE + ".bak")
            
            file.save(CSV_FILE)
            flash('Données restaurées avec succès. Une sauvegarde de l\'ancien fichier a été créée (.bak).', 'success')
        except Exception as e:
            flash(f"Erreur lors de la restauration : {e}", "danger")
    else:
        flash('Format invalide. Veuillez fournir un fichier .csv.', 'danger')
        
    return redirect(url_for('admin_page'))

@app.route('/admin/upload_config', methods=['POST'])
@login_required
def admin_upload_config():
    """Restaure un fichier de configuration JSON."""
    if 'file' not in request.files:
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_page'))
    
    file = request.files['file']
    
    if file.filename == '':
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_page'))
    
    if file and file.filename.endswith('.json'):
        try:
            # On lit le contenu pour vérifier que c'est un JSON valide
            file_content = file.read()
            json.loads(file_content) # Lève une exception si invalide

            # Sauvegarde de sécurité du fichier actuel
            if os.path.exists(CONFIG_FILE):
                shutil.copy(CONFIG_FILE, CONFIG_FILE + ".bak")
            
            # On écrit le nouveau contenu
            with open(CONFIG_FILE, 'wb') as f:
                f.write(file_content)
            
            # Mise à jour de la configuration en mémoire
            global config, LATITUDE, LONGITUDE, OWM_API_KEY
            config = load_config()
            LATITUDE = config.get("latitude")
            LONGITUDE = config.get("longitude")
            OWM_API_KEY = config.get("owm_api_key")
            
            flash('Configuration restaurée avec succès. Une sauvegarde (.bak) a été créée.', 'success')
        except json.JSONDecodeError:
            flash("Le fichier fourni n'est pas un JSON valide.", "danger")
        except Exception as e:
            flash(f"Erreur lors de la restauration : {e}", "danger")
    else:
        flash('Format invalide. Veuillez fournir un fichier .json.', 'danger')
        
    return redirect(url_for('admin_page'))

@app.route('/admin/upload_overlay', methods=['POST'])
@login_required
def admin_upload_overlay():
    """Permet d'uploader une image de calque (frontières/carte) pour la vue satellite."""
    if 'file' not in request.files:
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_page'))
    
    file = request.files['file']
    if file.filename == '':
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin_page'))
    
    # On accepte PNG (transparence)
    if file:
        file.save(os.path.join(app.root_path, 'static', 'img', 'map_overlay.png'))
        flash('Calque de carte (overlay) mis à jour avec succès.', 'success')
    else:
        flash("Erreur lors de l'envoi du fichier.", "danger")
        
    return redirect(url_for('admin_page'))

@app.route('/admin/generate_overlay', methods=['POST'])
@login_required
def admin_generate_overlay():
    """Génère automatiquement le fond de carte depuis OpenStreetMap."""
    try:
        # On utilise le même niveau de zoom que le script satellite (5)
        zoom = 5
        grid_size = 3
        
        # Calcul des coordonnées de la tuile centrale
        center_x, center_y = latlon_to_tile_coords(LATITUDE, LONGITUDE, zoom)
        
        # Création d'une image vide (RGBA pour la transparence potentielle)
        full_image = Image.new('RGBA', (256 * grid_size, 256 * grid_size), (0, 0, 0, 0))
        
        # User-Agent requis par la politique d'utilisation des tuiles OSM
        headers = {
            'User-Agent': 'MeteoPi/1.0 (Raspberry Pi Weather Station)'
        }
        
        # Vérification de l'option noir et blanc
        to_grayscale = request.form.get('grayscale') == 'true'

        for i in range(grid_size):
            for j in range(grid_size):
                # On décalle pour centrer la grille (comme dans satellite_fetcher.py)
                tile_x = center_x + i - 1
                tile_y = center_y + j - 1
                
                url = f"https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png"
                
                try:
                    response = requests.get(url, headers=headers, stream=True, timeout=5)
                    if response.status_code == 200:
                        tile_img = Image.open(response.raw).convert("RGBA")
                        full_image.paste(tile_img, (i * 256, j * 256))
                except Exception as e:
                    print(f"Erreur téléchargement tuile OSM {tile_x},{tile_y}: {e}")

        # Conversion en niveaux de gris si demandé
        if to_grayscale:
            full_image = full_image.convert("L")

        # Sauvegarde
        output_path = os.path.join(app.root_path, 'static', 'img', 'map_overlay.png')
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        full_image.save(output_path, format="PNG")
        
        flash('Fond de carte OpenStreetMap généré avec succès.', 'success')
    except Exception as e:
        flash(f"Erreur lors de la génération : {e}", "danger")
        
    return redirect(url_for('admin_page'))

@app.route("/rain_detail")
@login_required
def rain_detail():
    """Affiche le détail des pluies pour une période donnée."""
    period = request.args.get('period')
    title = "Détail des pluies"
    summary = "Période non spécifiée."
    
    try:
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df['rain'] = pd.to_numeric(df['rain'], errors='coerce')
            df.dropna(subset=['time'], inplace=True)
            
            now = datetime.now()
            start_time = None
            end_time = None
            
            if period == 'day':
                start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
                title = "Pluies - Aujourd'hui"
            elif period == 'week':
                start_time = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
                title = "Pluies - Cette Semaine"
            elif period == 'month':
                start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                title = "Pluies - Ce Mois"
            elif period and period.startswith('day_'):
                try:
                    days_ago = int(period.split('_')[1])
                    target_date = now - timedelta(days=days_ago)
                    start_time = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_time = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                    title = f"Pluies - {target_date.strftime('%d/%m')}"
                except (ValueError, IndexError):
                    pass

            if start_time:
                summary = get_rain_summary(df, start_time=start_time, end_time=end_time)
            else:
                summary = "Période invalide."
                
    except (FileNotFoundError, pd.errors.EmptyDataError):
        summary = "Aucune donnée disponible."
    except Exception as e:
        summary = f"Erreur inattendue lors de l'analyse : {e}"
        print(f"Erreur dans rain_detail : {e}")

    return render_template("rain_detail.html", title=title, summary=summary)

@app.route("/hourly_graph")
@login_required
def hourly_graph():
    graph_html = None
    try:
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'])
            df['temp'] = pd.to_numeric(df['temp'], errors='coerce')
            graph_html = generate_hourly_graph_base64(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass
    return render_template("hourly_graph.html", graph_html=graph_html)

@app.route("/daily_graph")
@login_required
def daily_graph():
    """Affiche le graphique détaillé pour une journée spécifique."""
    date_str = request.args.get('date')
    if not date_str:
        return redirect(url_for('home'))
        
    graph_html = None
    title = f"Météo du {date_str}"
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        start_day = target_date.replace(hour=0, minute=0, second=0)
        end_day = target_date.replace(hour=23, minute=59, second=59)
        
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df['temp'] = pd.to_numeric(df['temp'], errors='coerce')
            df['hum'] = pd.to_numeric(df['hum'], errors='coerce')
            df['rain'] = pd.to_numeric(df['rain'], errors='coerce')
            
            df_day = df[(df['time'] >= start_day) & (df['time'] <= end_day)]
            if not df_day.empty:
                graph_html = generate_hourly_graph_base64(df_day, filter_recent=False, title=f"Données horaires du {date_str}")
    except (ValueError, FileNotFoundError, pd.errors.EmptyDataError):
        pass
        
    return render_template("graph_page.html", title=title, graph_html=graph_html)

@app.route("/wind_rose")
@login_required
def wind_rose():
    """Affiche la rose des vents, ou un graphique de vitesse si pas de direction."""
    graph_html = None
    title = "Analyse du Vent"
    try:
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            
            # Vérifie si des données de direction valides existent (différentes de 'N/A')
            valid_directions = df['wind_dir_str'].dropna().unique()
            if len(valid_directions) == 0 or (len(valid_directions) == 1 and valid_directions[0] == 'N/A'):
                title = "Graphique de Vitesse du Vent"
                graph_html = generate_wind_speed_graph_48h_base64(df)
            else:
                title = "Rose des Vents"
                graph_html = generate_wind_rose_base64(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass
    return render_template("graph_page.html", title=title, graph_html=graph_html)

@app.route("/pressure_graph")
@login_required
def pressure_graph():
    """Affiche le graphique de pression."""
    graph_html = None
    try:
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df['pressure'] = pd.to_numeric(df['pressure'], errors='coerce')
            graph_html = generate_pressure_graph_base64(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass
    return render_template("graph_page.html", title="Graphique de Pression", graph_html=graph_html)

@app.route("/rain_graph")
@login_required
def rain_graph():
    """Affiche le graphique du cumul de pluie."""
    graph_html = None
    try:
        df = read_and_process_csv(CSV_FILE)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df['rain'] = pd.to_numeric(df['rain'], errors='coerce')
            graph_html = generate_rain_accumulation_graph_base64(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass
    return render_template("graph_page.html", title="Cumul de Pluie Journalier", graph_html=graph_html)

@app.route("/satellite")
@login_required
def satellite_page():
    """Affiche l'image satellite."""
    archive_dir = "static/satellite_archive"
    image_files = []
    if os.path.exists(archive_dir):
        # On trie les images pour les avoir dans l'ordre chronologique
        files = sorted(os.listdir(archive_dir))
        image_files = [os.path.join(archive_dir, f) for f in files]

    # Vérifie si un calque de carte existe
    overlay_path = os.path.join(app.root_path, 'static', 'img', 'map_overlay.png')
    overlay_exists = os.path.exists(overlay_path)

    return render_template("satellite.html", image_files=image_files, overlay_exists=overlay_exists)


def _save_graph_to_base64(fig):
    img = io.BytesIO()
    fig.savefig(img, format="png")
    plt.close(fig)
    img.seek(0)
    graph_url = base64.b64encode(img.getvalue()).decode('utf8')
    return f"data:image/png;base64,{graph_url}"

def get_last_csv_line(filepath):
    """
    Lit efficacement la dernière ligne non vide d'un fichier.
    C'est beaucoup plus performant que de lire tout le fichier avec Pandas.
    """
    try:
        with open(filepath, 'rb') as f:
            # On va à la fin du fichier, moins un peu pour trouver la dernière ligne
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
            # La dernière ligne complète est après le '\n' que nous avons trouvé
            last_line = f.readline().decode('utf-8')
            return last_line.strip().split(',')
    except (IOError, IndexError):
        # Fichier trop petit, vide ou inexistant
        return None

@app.route("/api/v1/sensors")
def api_sensors():
    """Fournit les dernières données des capteurs au format JSON pour Home Assistant (version optimisée)."""
    try:
        last_reading_list = get_last_csv_line(CSV_FILE)

        if not last_reading_list or len(last_reading_list) < 7: # On vérifie qu'on a au moins 7 colonnes
            return jsonify({"error": "No data available or invalid format"}), 404

        # Gère l'ancien et le nouveau format
        if len(last_reading_list) == 8:
            headers = ["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_gust", "wind_dir_str"]
        else: # len is 7
            headers = ["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"]
        
        last_reading = dict(zip(headers, last_reading_list))

        # Conversion des valeurs en types corrects (float, int)
        temp = float(last_reading['temp'])
        hum = float(last_reading['hum'])
        rain_since_last = float(last_reading['rain'])
        wind_speed = float(last_reading['wind_speed'])
        
        # Utilise .get() pour la nouvelle colonne pour éviter une KeyError sur les anciennes données
        wind_gust = float(last_reading.get('wind_gust', 0.0)) 
        wind_dir = last_reading.get('wind_dir_str', 'N/A')
        
        # La pression peut être une chaîne vide si le BME280 n'est pas là
        try:
            pressure = float(last_reading['pressure'])
        except (ValueError, KeyError):
            pressure = None

        data = {
            "temperature": round(temp, 1),
            "humidity": round(hum, 1),
            "pressure": round(pressure, 1) if pressure is not None else None,
            "wind_speed": round(wind_speed, 1),
            "wind_gust": round(wind_gust, 1),
            "wind_direction": wind_dir,
            "rain": round(rain_since_last, 4),
            # On convertit la date string en objet datetime puis en format ISO
            "last_update": datetime.strptime(last_reading['time'], "%Y-%m-%d %H:%M:%S").isoformat()
        }
        return jsonify(data)

    except (FileNotFoundError, pd.errors.EmptyDataError):
        return jsonify({"error": "Data source not found or empty"}), 404
    except Exception as e:
        # En cas de problème, on log l'erreur et on retourne une réponse claire
        print(f"Erreur dans l'API (version optimisée) : {e}")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route("/api/live_wind")
@login_required
def api_live_wind():
    """API légère pour récupérer le vent en temps réel (pour l'affichage JS)."""
    try:
        if not os.path.exists(WIND_CSV_FILE):
             return jsonify({"error": "No data"}), 404
             
        last_line = get_last_csv_line(WIND_CSV_FILE)
        
        # Validation (time, speed, dir)
        # On ignore si la ligne est trop courte ou si c'est l'en-tête
        if not last_line or len(last_line) < 3 or last_line[1] == "wind_speed":
            return jsonify({"error": "Invalid data"}), 404

        return jsonify({
            "wind_speed": float(last_line[1]),
            "wind_dir": last_line[2]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    """Route pour servir le logo comme favicon (icône de l'onglet)."""
    return send_file(os.path.join(app.root_path, 'static', 'img', 'meteopi.png'), mimetype='image/png')


if __name__ == "__main__":
    # Le nettoyage est maintenant fait au-dessus.
    app.run(host="0.0.0.0", port=5000)
