#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from gpiozero import Button
import time

# ---- Configuration ----
# GPIO 6 selon votre README
WIND_PIN = 6
INTERVAL = 2 # Intervalle de mise à jour en secondes pour le test
WIND_SPEED_FACTOR = 2.4 # Doit correspondre à celui de meteo_capteur.py

wind_count = 0

def spin_detected():
    global wind_count
    wind_count += 1

print("--- Script de test de l'anémomètre ---")
print(f"Connecté sur le GPIO {WIND_PIN}")
print("Faites tourner l'anémomètre...")
print("Appuyez sur Ctrl+C pour quitter.")

# L'anémomètre peut tourner vite, on met un bounce_time très court
sensor = Button(WIND_PIN, pull_up=True, bounce_time=0.01)
sensor.when_pressed = spin_detected

try:
    while True:
        wind_count = 0
        time.sleep(INTERVAL)
        
        # Formule approximative : 1 impulsion/sec ~= 2.4 km/h (dépend du modèle 3D)
        speed_kmh = (wind_count / INTERVAL) * WIND_SPEED_FACTOR
        
        if wind_count > 0:
            print(f"✅ Rotation détectée ! {wind_count} impulsions -> Vitesse estimée : {speed_kmh:.1f} km/h")
        else:
            print("... En attente de rotation ...")

except KeyboardInterrupt:
    print("\nFin du test.")