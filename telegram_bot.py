# -*- coding: utf-8 -*-
import json
import os
import time
import requests
from datetime import datetime

CONFIG_FILE = "config.json"
CSV_FILE = "meteo_log.csv"
SEND_INTERVAL = 3600  # 1 heure en secondes

def load_config():
    """Charge la configuration depuis config.json."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Fichier de configuration introuvable ou corrompu.")
        return {}

def get_last_csv_line(filepath):
    """Lit efficacement la dernière ligne non vide d'un fichier."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
            last_line = f.readline().decode('utf-8')
            return last_line.strip().split(',')
    except (IOError, IndexError):
        return None

def send_telegram_message(token, chat_id, message):
    """Envoie un message à un chat Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print("Message Telegram envoyé avec succès.")
        else:
            print(f"Erreur lors de l'envoi du message Telegram: {response.status_code} - {response.text}")
    except requests.RequestException as e:
        print(f"Erreur de connexion à l'API Telegram: {e}")

def main():
    """Boucle principale pour envoyer les bulletins météo."""
    print("Démarrage du bot de notification Telegram.")
    
    while True:
        config = load_config()
        token = config.get("telegram_bot_token")
        chat_id = config.get("telegram_chat_id")

        if not token or "METTRE_VOTRE_TOKEN_ICI" in token or not chat_id:
            print("Token ou Chat ID Telegram non configuré. Mise en veille pour 1h.")
            time.sleep(SEND_INTERVAL)
            continue

        last_reading_list = get_last_csv_line(CSV_FILE)
        if not last_reading_list or len(last_reading_list) < 7:
            print("Pas de données météo récentes à envoyer.")
            time.sleep(SEND_INTERVAL)
            continue

        headers = ["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"]
        last_reading = dict(zip(headers, last_reading_list))

        try:
            update_time = datetime.strptime(last_reading['time'], "%Y-%m-%d %H:%M:%S").strftime('%d/%m à %Hh%M')
            temp, hum, wind_speed, wind_dir = float(last_reading['temp']), float(last_reading['hum']), float(last_reading['wind_speed']), last_reading.get('wind_dir_str', 'N/A')
            pressure_str = f"{float(last_reading['pressure']):.1f} hPa" if last_reading['pressure'] else "N/A"

            message = f"☀️ *Bulletin Météo du {update_time}*\n\n🌡️ *Température*: {temp:.1f}°C\n💧 *Humidité*: {hum:.0f}%\n📈 *Pression*: {pressure_str}\n💨 *Vent*: {wind_speed:.1f} km/h ({wind_dir})"
            send_telegram_message(token, chat_id, message)
        except (ValueError, KeyError) as e:
            print(f"Erreur lors du formatage des données: {e}")

        print(f"Prochain envoi dans {SEND_INTERVAL / 3600:.0f} heure(s).")
        time.sleep(SEND_INTERVAL)

if __name__ == "__main__":
    main()