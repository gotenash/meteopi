# -*- coding: utf-8 -*-
import time
import threading
from gpiozero import Button
from grove_rgb_lcd import RgbLcd # Le pilote pour votre écran

# ---- Configuration ----
# Pin GPIO auquel l'anémomètre est connecté
WIND_PIN = 6
# Intervalle de mesure en secondes (2s pour une mise à jour rapide en voiture)
SAMPLE_TIME = 2.0
# Facteur de conversion : 1 impulsion/seconde (Hz) = 2.4 km/h
# Nouveau facteur calibré : 2.4 * (70/30) = 5.6
WIND_SPEED_FACTOR = 5.6

# Variable globale pour compter les impulsions de l'anémomètre
wind_pulse_count = 0
wind_pulse_count_lock = threading.Lock()

# ---- Initialisation du matériel ----

# Anémomètre
def count_wind_pulse():
    """Fonction appelée à chaque rotation pour incrémenter le compteur."""
    global wind_pulse_count
    with wind_pulse_count_lock:
        wind_pulse_count += 1

try:
    # Initialise le capteur sur le pin GPIO, avec une résistance de pull-up
    wind_sensor = Button(WIND_PIN, pull_up=True, bounce_time=None)
    wind_sensor.when_pressed = count_wind_pulse
    print(f"✅ Anémomètre initialisé sur le GPIO {WIND_PIN}")
except Exception as e:
    print(f"❌ Erreur d'initialisation de l'anémomètre : {e}")
    print("Vérifiez le câblage et les permissions GPIO. Le script va s'arrêter.")
    exit()

# Écran LCD
try:
    lcd = RgbLcd()
    lcd.set_rgb(0, 150, 255) # Couleur de fond bleutée
    lcd.write("Test Anemometre\nPret...")
    print("✅ Écran LCD initialisé.")
except Exception as e:
    print(f"❌ Erreur d'initialisation de l'écran LCD : {e}")
    print("Vérifiez le câblage I2C. Le script continuera sans affichage sur l'écran.")
    lcd = None

# ---- Boucle principale ----

def calculate_and_display():
    """
    Calcule la vitesse du vent et l'affiche sur la console et l'écran LCD.
    Cette fonction s'auto-appelle toutes les SAMPLE_TIME secondes.
    """
    global wind_pulse_count

    # Planifie la prochaine exécution
    threading.Timer(SAMPLE_TIME, calculate_and_display).start()

    # --- Calcul ---
    with wind_pulse_count_lock:
        # Calcule la fréquence en Hz (nombre d'impulsions par seconde)
        wind_hz = wind_pulse_count / SAMPLE_TIME
        # Réinitialise le compteur pour le prochain intervalle
        wind_pulse_count = 0

    # Convertit la fréquence en vitesse (km/h)
    wind_speed_kmh = wind_hz * WIND_SPEED_FACTOR

    # --- Affichage ---
    speed_str = f"{wind_speed_kmh:.1f} km/h"
    print(f"Vitesse du vent : {speed_str}")

    if lcd:
        try:
            lcd.clear()
            lcd.write("Vitesse du vent:")
            lcd.set_cursor(0, 1) # Passe à la deuxième ligne
            lcd.write(speed_str)
        except Exception as e:
            print(f"Erreur d'écriture sur l'écran LCD : {e}")

if __name__ == "__main__":
    print("\n--- Test de l'anémomètre en temps réel ---")
    print(f"La vitesse est mesurée toutes les {SAMPLE_TIME} secondes.")
    print("Placez l'anémomètre face au vent (ou commencez à rouler !).")
    print("Appuyez sur Ctrl+C pour quitter.")

    # Lance la première boucle de mesure
    calculate_and_display()