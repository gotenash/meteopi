# -*- coding: utf-8 -*-
import csv
import time
from datetime import datetime
import logging # Ajout pour le logging des événements du pluviomètre
import smbus2
import threading
import board
import adafruit_dht
from adafruit_bme280 import basic as adafruit_bme280
from adafruit_as5600 import AS5600
from gpiozero import Button
from grove_rgb_lcd import RgbLcd # Import de la nouvelle librairie pour l'écran

CSV_FILE = "meteo_log.csv"
PLUVIOMETER_EVENT_LOG = "pluviometer_events.log" # Nouveau fichier de log pour les basculements

# Configuration du logging pour les événements du pluviomètre
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(PLUVIOMETER_EVENT_LOG)
    ]
)

# ---- Configuration du pluviomètre ----
RAIN_PIN = 5  # GPIO 5
# Nouvelle calibration (100ml d'eau = 10mm de pluie) pour 47 basculements
# 10mm / 47 tips = 0.2127659574 mm/tip
MM_PER_TIP = 0.213  # Chaque basculement correspond à 0.213mm de pluie

# Variable globale pour compter les basculements
tip_count = 0
tip_count_lock = threading.Lock()

# ---- Configuration de l'anémomètre ----
WIND_PIN = 6 # GPIO 6
WIND_SPEED_FACTOR = 2.4 # 1 Hz (1 impulsion/sec) = 2.4 km/h

# Variable globale pour compter les impulsions du vent
wind_pulse_count = 0

def count_tip():
    """Fonction appelée à chaque basculement de l'auget."""
    global tip_count
    with tip_count_lock:
        tip_count += 1
    logging.info("Pluviometer tip detected!") # Enregistre l'événement dans le log

def count_wind_pulse():
    """Fonction appelée à chaque rotation de l'anémomètre."""
    global wind_pulse_count
    wind_pulse_count += 1

# ---- Configuration des capteurs (BME280 en priorité) ----
bme280 = None
as5600 = None
i2c = None
try:
    i2c = board.I2C()  # Use board.I2C() instead of smbus2.SMBus(1)
except (ValueError, FileNotFoundError):
    print("❌ Bus I2C non trouvé. Les capteurs BME280 et AS5600 seront désactivés.")

if i2c:
    # On essaie d'initialiser le BME280
    try:
        # On spécifie l'adresse 0x76, car c'est celle détectée par i2cdetect.
        bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x76)
        print("✅ Capteur BME280 détecté. Il sera utilisé pour les mesures.")
    except (ValueError, OSError) as e:
        # Si le BME280 n'est pas trouvé, on l'indique et on se préparera
        # à utiliser le capteur de secours DHT11.
        print(f"ℹ️ Capteur BME280 non trouvé ({e}). Le capteur DHT11 sera utilisé.")

    # ---- Initialisation de la girouette (AS5600) ----
    try:
        as5600 = AS5600(i2c)
        print("✅ Girouette (AS5600) détectée.")
    except (ValueError, OSError):
        print("ℹ️ Girouette (AS5600) non trouvée sur le bus I2C.")

# ---- Initialisation du capteur de secours (DHT11) ----
dht_device = None
if not bme280:
    # Si le BME280 n'a pas été trouvé, on initialise le DHT11 comme solution de repli.
    # On désactive pulseio pour éviter des erreurs avec sysv_ipc sur Raspberry Pi.
    print("Tentative d'initialisation du capteur de secours DHT11...")
    try:
        dht_device = adafruit_dht.DHT11(board.D4, use_pulseio=False)
        print("✅ Capteur de secours DHT11 initialisé.")
    except RuntimeError as e:
        print(f"❌ Échec de l'initialisation du DHT11: {e}. Aucune mesure de température/humidité ne sera possible.")


# ---- Initialisation de l'écran LCD ----
lcd = None
try:
    lcd = RgbLcd()
    lcd.set_rgb(50, 50, 150) # Couleur de fond bleu/violet au démarrage
    lcd.write("Station Meteo\nInitialisation...")
    print("✅ Écran LCD Grove détecté.")
except (IOError, OSError):
    print("ℹ️ Écran LCD non trouvé. Le script continuera sans affichage local.")

# Initialisation du pluviomètre avec un temps de debounce de 100ms
# pour filtrer les rebonds mécaniques et le bruit.
rain_sensor = Button(RAIN_PIN, pull_up=True, bounce_time=0.05) # Réduction du bounce_time à 50ms
rain_sensor.when_pressed = count_tip

# Initialisation du capteur de vent
# On ajoute un petit bounce_time pour filtrer le bruit électrique potentiel
wind_sensor = Button(WIND_PIN, pull_up=True, bounce_time=0.01)
wind_sensor.when_pressed = count_wind_pulse

# Créer le fichier avec en-têtes si inexistant
try:
    with open(CSV_FILE, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_dir_str"])
except FileExistsError:
    pass

def read_sensors():
    """
    Lit les données depuis le BME280 si disponible, sinon depuis le DHT11.
    Retourne (temp, hum, pressure). Pressure est None si non disponible.
    """
    try:
        temp, hum, pressure = None, None, None
        if bme280:
            temp, hum, pressure = bme280.temperature, bme280.humidity, bme280.pressure
        elif dht_device:
            # Utilise le DHT11, pas de pression disponible
            temp, hum = dht_device.temperature, dht_device.humidity
        
        # --- Calibration de la température ---
        # Ajustez la valeur de l'offset selon vos observations.
        TEMP_OFFSET = -2.0
        if temp is not None:
            temp += TEMP_OFFSET
        
        return temp, hum, pressure
    except RuntimeError as error:
        # Erreur de lecture, on retourne None pour toutes les valeurs
        print(f"Erreur de lecture du capteur: {error.args[0]}")
        return None, None, None

def get_wind_direction(angle):
    """Convertit un angle en direction cardinale."""
    if angle is None:
        return "N/A"
    directions = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    # Chaque direction couvre 45 degrés (360 / 8). On décale de 22.5 pour centrer.
    index = int((angle + 22.5) / 45) % 8
    return directions[index]

def read_wind_vane():
    """Lit l'angle de la girouette si disponible."""
    if as5600:
        # La librairie retourne l'angle en degrés
        return as5600.angle
    return None

def sample_and_log():
    """
    Fonction exécutée toutes les SAMPLE_TIME secondes pour lire les capteurs,
    calculer les valeurs et les enregistrer.
    """
    global wind_pulse_count, tip_count
    
    # On configure le timer pour qu'il se relance à la fin de l'exécution
    threading.Timer(SAMPLE_TIME, sample_and_log).start()

    temp, hum, pressure = read_sensors()

    if temp is None or hum is None:
        print("Données capteur invalides, mesure ignorée pour cet intervalle.")
        # On ne réinitialise pas les compteurs de vent/pluie pour ne pas perdre de données
        return

    # --- Calculs et réinitialisations ---
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Vitesse du vent
    wind_hz = wind_pulse_count / SAMPLE_TIME
    wind_speed_kmh = wind_hz * WIND_SPEED_FACTOR
    wind_pulse_count = 0 # Reset

    # Direction du vent
    wind_angle = read_wind_vane()
    wind_dir_str = get_wind_direction(wind_angle)

    # Pluie
    with tip_count_lock:
        rain_since_last = tip_count * MM_PER_TIP
        tip_count = 0 # Reset

    # --- Enregistrement et affichage ---
    pressure_val = f"{pressure:.2f}" if pressure is not None else ""
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([now, f"{temp:.2f}", f"{hum:.2f}", pressure_val, f"{rain_since_last:.4f}", f"{wind_speed_kmh:.2f}", wind_dir_str])
        f.flush()

    pressure_str = f"📈 {pressure:.1f}hPa" if pressure is not None else ""
    print(f"[{now}] 🌡 {temp:.1f}°C  💧 {hum:.0f}%  {pressure_str} 🌧️ {rain_since_last:.2f}mm 💨 {wind_speed_kmh:.1f} km/h ({wind_dir_str})")

    # Variable pour alterner l'affichage sur l'écran LCD
    global lcd_display_toggle

    # --- Mise à jour de l'écran LCD ---
    if lcd:
        try:
            # On change la couleur de fond en fonction de la température
            if temp < 10:
                lcd.set_rgb(0, 0, 255) # Bleu pour le froid
            elif temp > 25:
                lcd.set_rgb(255, 100, 0) # Orange pour le chaud
            else:
                lcd.set_rgb(0, 150, 50) # Vert pour le tempéré

            lcd.clear()
            line1 = f"T:{temp:.1f}C  H:{hum:.0f}%"
            
            # On alterne l'affichage de la deuxième ligne
            if lcd_display_toggle and pressure is not None:
                line2 = f"P:{pressure:.1f}hPa"
            else:
                line2 = f"V:{wind_speed_kmh:.1f} {wind_dir_str}"
            
            lcd.write(f"{line1}\n{line2}")
            lcd_display_toggle = not lcd_display_toggle # On inverse pour la prochaine fois

        except (IOError, OSError) as e:
            print(f"Erreur lors de la mise à jour de l'écran LCD: {e}")

SAMPLE_TIME = 60.0 # Durée de l'échantillonnage en secondes
lcd_display_toggle = False # Variable pour gérer l'alternance de l'affichage LCD

def print_startup_summary():
    """Affiche un résumé de l'état des capteurs au démarrage."""
    print("\n--- Résumé de l'initialisation ---")
    if bme280:
        print("✅ Temp/Hum/Press: BME280")
    elif dht_device:
        print("⚠️ Temp/Hum: DHT11 (secours)")
    else:
        print("❌ Temp/Hum: Aucun capteur trouvé")
    
    print(f"✅ Pluviomètre: GPIO {RAIN_PIN}")
    print(f"✅ Anémomètre: GPIO {WIND_PIN}")
    print(f"✅ Girouette: {'AS5600' if as5600 else 'Non trouvée'}")
    print(f"✅ Écran LCD: {'Grove RGB LCD' if lcd else 'Non trouvé'}")
    print("-------------------------------------\n")

print_startup_summary()
sample_and_log() # On lance la première exécution de la boucle de mesure
