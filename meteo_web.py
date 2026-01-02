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
            print(f"Le fichier '{filepath}' est propre. Aucun nettoyage nécessaire.")
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
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Si le fichier n'existe pas ou est corrompu, on crée une config par défaut
        default_config = {
            "owm_api_key": "METTRE_VOTRE_CLE_ICI",
            "latitude": 48.85, # Paris par défaut
            "longitude": 2.35
        }
        save_config(default_config)
        return default_config

def save_config(config_data):
    """Sauvegarde la configuration dans config.json."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=4)

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
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        # Ne stockez jamais de mots de passe en clair ! Utilisez un hash.
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

CSV_FILE = "meteo_log.csv"
PLUVIOMETER_EVENT_LOG = "pluviometer_events.log" # Chemin vers le fichier de log des événements du pluviomètre
# Calibration du pluviomètre (identique à meteo_capteur.py) - 2024-05-26 (expérimentale)
# Basé sur le test : 100ml d'eau (10mm) pour 53 basculements.
MM_PER_TIP = 0.213 # Correction pour correspondre au capteur

# --- Base de données utilisateur "en mémoire" ---
# Pour un usage réel, il faudrait une base de données (SQLite, etc.)
# Remplacez 'admin' et 'password' par vos identifiants
users = {
    "1": User(id="1", username="admin", password="password")
}

# On utilise maintenant les valeurs du fichier de configuration
OWM_API_KEY = config.get("owm_api_key")
LATITUDE = config.get("latitude")
LONGITUDE = config.get("longitude")

def generate_hourly_graph_base64(input_df):
    """Génère un graphique horaire à partir du DataFrame et le retourne en base64."""
    if input_df.empty:
        return None

    df = input_df.copy()
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
    plt.title("Données météo agrégées par heure (48 dernières heures)")
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

def get_rain_summary(df):
    """Analyse les données de pluie des dernières 24h et génère un résumé textuel."""
    if df.empty:
        return "Données de pluie non disponibles."

    now = datetime.now()
    # Filtrer les données des dernières 24 heures
    df_24h = df[df['time'] > (now - timedelta(hours=24))].copy()
    rain_events = df_24h[df_24h['rain'] > 0]

    if rain_events.empty:
        return "Pas de pluie détectée dans les dernières 24 heures."

    # Ajout d'une colonne 'date' pour pouvoir regrouper par jour
    rain_events['date'] = rain_events['time'].dt.date

    # On regroupe d'abord par jour
    daily_summaries = []
    for date, daily_group in rain_events.groupby('date'):
        
        # On identifie les épisodes de pluie au sein de la journée
        daily_group = daily_group.copy() # Pour éviter les avertissements pandas
        daily_group['time_diff'] = daily_group['time'].diff()
        episode_id = (daily_group['time_diff'] > timedelta(minutes=30)).cumsum()

        day_str = "Aujourd'hui" if date == now.date() else f"Hier ({date.strftime('%d/%m')})"
        
        episode_summaries = []
        for _, episode in daily_group.groupby(episode_id):
            start_time = episode['time'].min()
            end_time = episode['time'].max()
            total_rain = episode['rain'].sum()

            if len(episode) == 1:
                episode_summaries.append(f"Averse de {total_rain:.2f} mm vers {start_time.strftime('%Hh%M')}")
            else:
                duration = (end_time - start_time).total_seconds() / 60
                if duration > 5:
                    episode_summaries.append(f"{total_rain:.2f} mm entre {start_time.strftime('%Hh%M')} et {end_time.strftime('%Hh%M')}")
                else:
                    episode_summaries.append(f"{total_rain:.2f} mm autour de {start_time.strftime('%Hh%M')}")
        
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

@app.route("/")
@login_required
def home():
    # Initialisation des variables
    temp, hum, pressure, rain, wind, wind_dir, last_update, prediction, rain_summary, temp_hum_summary = "N/A", "N/A", "N/A", "N/A", "N/A", "", "inconnue", None, "Analyse en cours...", None
    stats = {'day': ('N/A', 'N/A'), 'week': ('N/A', 'N/A'), 'month': ('N/A', 'N/A')}

    try:
        # On lit le CSV en spécifiant les 7 colonnes écrites par le capteur
        df = pd.read_csv(CSV_FILE, header=0, names=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"], on_bad_lines='skip')
        if not df.empty:
            # Conversion des types, en gérant les erreurs
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df['pressure'] = pd.to_numeric(df['pressure'], errors='coerce')
            df.dropna(subset=['time'], inplace=True) # On supprime les lignes où la date est invalide

            last_reading = df.iloc[-1]
            temp = f"{last_reading['temp']:.1f}"  # Formatte avec une décimale
            hum = f"{last_reading['hum']:.0f}"    # Formatte en entier
            wind = f"{last_reading['wind_speed']:.1f}"
            wind_dir = last_reading['wind_dir_str'] if pd.notna(last_reading['wind_dir_str']) else ""
            pressure = f"{last_reading['pressure']:.1f}" if pd.notna(last_reading['pressure']) else "N/A"
            
            # Calcul du cumul de pluie sur les dernières 24h
            last_24h = df[df['time'] > (datetime.now() - timedelta(hours=24))]
            rain_24h = last_24h['rain'].sum()
            rain = f"{rain_24h:.2f}"

            # --- Calcul des statistiques Min/Max ---
            now = datetime.now()
            
            # Jour
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            df_today = df[df['time'] >= today_start]
            if not df_today.empty:
                stats['day'] = (f"{df_today['temp'].min():.1f}", f"{df_today['temp'].max():.1f}")

            # Semaine (commence le Lundi)
            week_start = today_start - timedelta(days=now.weekday())
            df_week = df[df['time'] >= week_start]
            if not df_week.empty:
                stats['week'] = (f"{df_week['temp'].min():.1f}", f"{df_week['temp'].max():.1f}")

            # Mois
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            df_month = df[df['time'] >= month_start]
            if not df_month.empty:
                stats['month'] = (f"{df_month['temp'].min():.1f}", f"{df_month['temp'].max():.1f}")

            # Génération de la prédiction (uniquement si des données de pression existent)
            prediction = get_weather_prediction(df)

            last_update = last_reading['time'].strftime("%d/%m/%Y à %H:%M:%S")

    except (FileNotFoundError, pd.errors.EmptyDataError):
        # Le fichier n'existe pas encore ou est vide
        pass
    
    # On déplace les appels aux fonctions d'analyse ici pour plus de clarté
    stats_graph_html = generate_stats_graph_base64(stats) if 'df' in locals() and not df.empty else None
    rain_summary = get_rain_summary(df) if 'df' in locals() and not df.empty else "Données non disponibles."
    temp_hum_summary = get_temp_hum_summary(df) if 'df' in locals() and not df.empty else None
    
    return render_template("home.html", temp=temp, hum=hum, pressure=pressure, rain=rain, wind=wind, wind_dir=wind_dir, last_update=last_update, stats_graph_html=stats_graph_html, prediction=prediction, stats=stats, rain_summary=rain_summary, temp_hum_summary=temp_hum_summary)

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

@app.route('/history')
@login_required
def history():
    """Affiche l'historique complet des données avec pagination et filtrage par date."""
    try:
        df = pd.read_csv(CSV_FILE, header=0, names=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"], on_bad_lines='skip')
        df.dropna(subset=['time'], inplace=True) # On s'assure que la colonne 'time' n'est pas vide
        df['time'] = pd.to_datetime(df['time'], errors='coerce')
        df.dropna(subset=['time'], inplace=True) # On supprime les lignes où la conversion de date a échoué

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

        start = (page - 1) * per_page
        end = start + per_page
        df_page = df.iloc[start:end].copy() # .copy() pour éviter un avertissement

        # Formater pour l'affichage
        df_page['time'] = pd.to_datetime(df_page['time']).dt.strftime('%d/%m/%Y %H:%M')
        df_page = df_page.rename(columns={'time': 'Date/Heure', 'temp': 'Temp (°C)', 'hum': 'Humidité (%)', 'pressure': 'Pression (hPa)', 'rain': 'Pluie (mm)', 'wind_speed': 'Vent (km/h)', 'wind_dir_str': 'Direction'})
        table_html = df_page.to_html(classes='data-table', index=False, justify='center')

    except (FileNotFoundError, pd.errors.EmptyDataError):
        table_html = "<p>Aucune donnée d'historique à afficher.</p>"
        page, total_pages, start_date_str, end_date_str = 1, 1, '', ''

    return render_template('history.html', table_html=table_html, current_page=page, total_pages=total_pages, start_date=start_date_str, end_date=end_date_str)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_page():
    """Page d'administration pour les actions sensibles."""
    return render_template('admin.html', config=config)

@app.route('/admin/update_config', methods=['POST'])
@login_required
def admin_update_config():
    """Met à jour le fichier de configuration."""
    try:
        new_config = {
            "owm_api_key": request.form['owm_api_key'],
            "latitude": float(request.form['latitude']),
            "longitude": float(request.form['longitude'])
        }
        save_config(new_config)
        flash("Configuration mise à jour avec succès ! L'application va redémarrer pour appliquer les changements.", "success")
        # On recharge la configuration pour la session en cours
        global config, LATITUDE, LONGITUDE, OWM_API_KEY
        config = new_config
        LATITUDE, LONGITUDE, OWM_API_KEY = config['latitude'], config['longitude'], config['owm_api_key']
    except ValueError:
        flash("Erreur : La latitude et la longitude doivent être des nombres.", "danger")
    except Exception as e:
        flash(f"Une erreur est survenue : {e}", "danger")
    return redirect(url_for('admin_page'))

@app.route("/download")
@login_required
def download():
    return send_file(CSV_FILE, as_attachment=True)

@app.route("/hourly_graph")
@login_required
def hourly_graph():
    graph_html = None
    try:
        df = pd.read_csv(CSV_FILE, header=0, names=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"], on_bad_lines='skip')
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'])
            df['temp'] = pd.to_numeric(df['temp'], errors='coerce')
            graph_html = generate_hourly_graph_base64(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass
    return render_template("hourly_graph.html", graph_html=graph_html)

@app.route("/wind_rose")
@login_required
def wind_rose():
    """Affiche la rose des vents."""
    graph_html = None
    try:
        df = pd.read_csv(CSV_FILE, header=0, names=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"], on_bad_lines='skip')
        if not df.empty:
            graph_html = generate_wind_rose_base64(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass
    return render_template("graph_page.html", title="Rose des Vents", graph_html=graph_html)

@app.route("/pressure_graph")
@login_required
def pressure_graph():
    """Affiche le graphique de pression."""
    graph_html = None
    try:
        df = pd.read_csv(CSV_FILE, header=0, names=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"], on_bad_lines='skip')
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
        df = pd.read_csv(CSV_FILE, header=0, names=["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"], on_bad_lines='skip')
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

    return render_template("satellite.html", image_files=image_files)


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
    """Fournit les dernières données des capteurs au format JSON pour Home Assistant."""
    try:
        last_reading_list = get_last_csv_line(CSV_FILE)

        # On vérifie qu'on a bien nos 7 colonnes
        if not last_reading_list or len(last_reading_list) < 7:
            return jsonify({"error": "No data available"}), 404

        # On recrée un dictionnaire avec les noms des colonnes pour plus de clarté
        headers = ["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"]
        last_reading = dict(zip(headers, last_reading_list))

        # Conversion des valeurs en types corrects (float, int)
        temp = float(last_reading['temp'])
        hum = float(last_reading['hum'])
        rain = float(last_reading['rain'])
        wind_speed = float(last_reading['wind_speed'])
        wind_dir = last_reading.get('wind_dir_str', 'N/A') # On récupère la direction du vent
        
        # La pression peut être une chaîne vide si le BME280 n'est pas là
        try:
            pressure = float(last_reading['pressure'])
        except (ValueError, KeyError):
            pressure = None

        data = {
            "temperature": round(temp, 1),
            "humidity": round(hum, 1),
            "rain": round(rain, 4),
            "pressure": round(pressure, 1) if pressure is not None else None,
            "wind_speed": round(wind_speed, 1),
            "wind_direction": wind_dir,
            # On convertit la date string en objet datetime puis en format ISO
            "last_update": datetime.strptime(last_reading['time'], "%Y-%m-%d %H:%M:%S").isoformat()
        }
        return jsonify(data)

    except (FileNotFoundError, pd.errors.EmptyDataError):
        return jsonify({"error": "Data source not found or empty"}), 404

@app.route('/favicon.ico')
def favicon():
    """Route pour servir le logo comme favicon (icône de l'onglet)."""
    return send_file(os.path.join(app.root_path, 'static', 'img', 'meteoppi.png'), mimetype='image/png')


if __name__ == "__main__":
    # Le nettoyage est maintenant fait au-dessus.
    app.run(host="0.0.0.0", port=5000)
