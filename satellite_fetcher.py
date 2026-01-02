#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import math
import requests
from PIL import Image
from datetime import datetime

CONFIG_FILE = "config.json"
ARCHIVE_DIR = "static/satellite_archive"
MAX_IMAGES = 12  # Nombre d'images à conserver pour l'animation (12 * 15min = 3h)
FETCH_INTERVAL = 900  # 15 minutes en secondes

def load_config():
    """Charge la configuration depuis config.json."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Fichier de configuration introuvable ou corrompu. Utilisation des valeurs par défaut.")
        return {
            "owm_api_key": "METTRE_VOTRE_CLE_ICI",
            "latitude": 48.85,
            "longitude": 2.35
        }

def latlon_to_tile_coords(lat, lon, zoom):
    """Convertit des coordonnées GPS en coordonnées de tuile."""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def fetch_and_save_satellite_grid(config):
    """Récupère une grille 3x3 et la sauvegarde en une seule image."""
    print(f"[{datetime.now()}] Début de la récupération de l'image satellite...")
    zoom = 5
    center_x, center_y = latlon_to_tile_coords(config['latitude'], config['longitude'], zoom)
    
    grid_size = 3
    full_image = Image.new('RGB', (256 * grid_size, 256 * grid_size))

    for i in range(grid_size):
        for j in range(grid_size):
            tile_x, tile_y = center_x + i - 1, center_y + j - 1
            url = f"https://tile.openweathermap.org/map/clouds_new/{zoom}/{tile_x}/{tile_y}.png?appid={config['owm_api_key']}"
            
            try:
                response = requests.get(url, stream=True, timeout=10)
                if response.status_code == 200:
                    tile_image = Image.open(response.raw)
                    full_image.paste(tile_image, (i * 256, j * 256))
            except requests.RequestException as e:
                print(f"Erreur lors du téléchargement de la tuile ({tile_x},{tile_y}): {e}")

    # Sauvegarde de l'image
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(ARCHIVE_DIR, f"satellite_{timestamp}.png")
    full_image.save(filepath, format="PNG")
    print(f"Image sauvegardée dans : {filepath}")

    # Nettoyage des anciennes images
    images = sorted(os.listdir(ARCHIVE_DIR), reverse=True)
    if len(images) > MAX_IMAGES:
        for old_image in images[MAX_IMAGES:]:
            os.remove(os.path.join(ARCHIVE_DIR, old_image))
            print(f"Ancienne image supprimée : {old_image}")

def main():
    """Boucle principale pour récupérer les images périodiquement."""
    print("Démarrage du service de récupération d'images satellite.")
    config = load_config()
    
    while True:
        try:
            fetch_and_save_satellite_grid(config)
        except Exception as e:
            print(f"Une erreur inattendue est survenue dans la boucle principale : {e}")
        
        print(f"Prochaine récupération dans {FETCH_INTERVAL / 60} minutes.")
        time.sleep(FETCH_INTERVAL)

if __name__ == "__main__":
    main()