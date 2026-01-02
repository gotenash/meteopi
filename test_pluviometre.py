# -*- coding: utf-8 -*-
from gpiozero import Button
from signal import pause

# ---- Configuration ----
# Assurez-vous que ce pin correspond à votre câblage
RAIN_PIN = 5

def tip_detected():
    """
    Cette fonction est appelée instantanément à chaque basculement.
    """
    print("✅ Auget basculé ! Impulsion détectée.")

print("--- Script de test du pluviomètre ---")
print(f"En écoute des impulsions sur le GPIO {RAIN_PIN}...")
print("Basculez manuellement l'auget pour voir si une impulsion est détectée.")
print("Appuyez sur Ctrl+C pour quitter.")

# On initialise le bouton en activant la résistance "pull-up" interne.
# Cela évite les faux positifs dus au bruit électrique.
# pull_up=True signifie que le pin est maintenu à l'état HAUT par défaut.
# bounce_time permet d'ignorer les rebonds et le bruit pendant 0.1s après une détection.
rain_sensor = Button(RAIN_PIN, pull_up=True, bounce_time=0.1)
rain_sensor.when_pressed = tip_detected

pause() # Met le script en pause en attendant les événements