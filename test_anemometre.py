# -*- coding: utf-8 -*-
from gpiozero import Button
from signal import pause
import time

# ---- Configuration ----
# Assurez-vous que ce pin correspond à votre câblage
WIND_PIN = 6

def magnet_pressed():
    """
    Cette fonction est appelée instantanément à chaque passage de l'aimant.
    """
    print(f"[{time.time():.2f}] ✅ PRESSED: Aimant détecté !")

def magnet_released():
    """
    Cette fonction est appelée quand l'aimant s'éloigne.
    """
    print(f"[{time.time():.2f}] ⚪️ RELEASED: L'aimant s'est éloigné.")

print("--- Script de test de l'anémomètre ---")
print(f"En écoute des impulsions sur le GPIO {WIND_PIN}...")
print("Passez un aimant devant le capteur à effet Hall.")
print("Appuyez sur Ctrl+C pour quitter.")

wind_sensor = Button(WIND_PIN, pull_up=True, bounce_time=0.01)
wind_sensor.when_pressed = magnet_pressed
wind_sensor.when_released = magnet_released

pause() # Met le script en pause en attendant les événements