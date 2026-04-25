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
WIND_CSV_FILE = "wind_detail_log.csv" # Nouveau fichier pour le vent en temps réel (2s)
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
# Facteur de calibration après test en voiture (70 km/h réels -> 30 km/h mesurés)
# Nouveau facteur = 2.4 * (70 / 30) = 5.6
WIND_SPEED_FACTOR = 5.6 # 1 Hz (1 impulsion/sec) = 5.6 km/h
BUTTON_PIN = 26 # GPIO 26 pour le bouton de changement d'affichage

# Variable globale pour compter les impulsions du vent
wind_pulse_count = 0
# Variable pour l'affichage temps réel (indépendante du log CSV)
wind_count_lock = threading.Lock() # Verrou pour sécuriser le comptage principal
wind_pulse_count_display = 0
wind_gust_pulse_max = 0 # Pour traquer la rafale (pic sur 2s)
gust_lock = threading.Lock()
wind_display_lock = threading.Lock()

# ---- Variables globales pour l'affichage et les données ----
display_mode = 0 # 0: Vent, 1: Temp/Pres, 2: Hum/Pluie
lcd_lock = threading.Lock() # Pour éviter les conflits d'écriture sur l'écran
last_temp = None
last_hum = None
last_pressure = None
daily_rain = 0.0
current_day = datetime.now().day
last_wind_speed = 0.0

# Variables pour le calcul précis du temps écoulé (éviter la dérive des Timers)
last_sample_time = time.time()
last_realtime_time = time.time()

def count_tip():
    """Fonction appelée à chaque basculement de l'auget."""
    global tip_count
    with tip_count_lock:
        tip_count += 1
    logging.info("Pluviometer tip detected!") # Enregistre l'événement dans le log

def count_wind_pulse():
    """Fonction appelée à chaque rotation de l'anémomètre."""
    global wind_pulse_count, wind_pulse_count_display
    with wind_count_lock:
        wind_pulse_count += 1
    with wind_display_lock:
        wind_pulse_count_display += 1

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
    lcd.write("Vitesse vent\nAttente...")
    print("✅ Écran LCD Grove détecté.")
except (IOError, OSError):
    print("ℹ️ Écran LCD non trouvé. Le script continuera sans affichage local.")

# Initialisation du pluviomètre avec un temps de debounce de 100ms
# pour filtrer les rebonds mécaniques et le bruit.
rain_sensor = Button(RAIN_PIN, pull_up=True, bounce_time=0.05) # Réduction du bounce_time à 50ms
rain_sensor.when_pressed = count_tip

# Initialisation du capteur de vent
# Pour un capteur à effet Hall, on retire le bounce_time car les impulsions deviennent très courtes à haute vitesse
wind_sensor = Button(WIND_PIN, pull_up=True, bounce_time=None)
wind_sensor.when_pressed = count_wind_pulse

# Initialisation du bouton de changement d'affichage
def update_lcd_display():
    """Met à jour le contenu de l'écran LCD selon le mode actuel."""
    if not lcd:
        return

    with lcd_lock:
        try:
            # Couleur de fond basée sur la température (reste active dans tous les modes)
            if last_temp is not None:
                if last_temp < 10:
                    lcd.set_rgb(0, 0, 255) # Bleu
                elif last_temp > 25:
                    lcd.set_rgb(255, 100, 0) # Orange
                else:
                    lcd.set_rgb(0, 150, 50) # Vert

            lcd.clear()
            
            if display_mode == 0: # Mode Vent
                line1 = "Vitesse vent"
                line2 = f"{last_wind_speed:.1f} km/h"
                # On pourrait ajouter la direction ici si disponible
                
            elif display_mode == 1: # Mode Température / Pression
                t_str = f"{last_temp:.1f}C" if last_temp is not None else "--.-C"
                p_str = f"{last_pressure:.0f}hPa" if last_pressure is not None else "----hPa"
                line1 = "Temp: " + t_str
                line2 = "Pres: " + p_str
                
            elif display_mode == 2: # Mode Humidité / Pluie Jour
                h_str = f"{last_hum:.0f}%" if last_hum is not None else "--%"
                r_str = f"{daily_rain:.1f}mm"
                line1 = "Humidite: " + h_str
                line2 = "Pluie Jour: " + r_str

            lcd.set_cursor(0, 0)
            lcd.write(line1)
            lcd.set_cursor(0, 1)
            lcd.write(line2)
        except OSError:
            pass

def change_display_mode():
    """Change le mode d'affichage et rafraîchit l'écran immédiatement."""
    global display_mode
    display_mode = (display_mode + 1) % 3
    update_lcd_display()

mode_button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.2)
mode_button.when_pressed = change_display_mode

# Créer le fichier avec en-têtes si inexistant
try:
    with open(CSV_FILE, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_gust", "wind_dir_str"])
except FileExistsError:
    pass

# Création du fichier de log détaillé pour le vent
try:
    with open(WIND_CSV_FILE, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "wind_speed", "wind_dir"])
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
    global wind_pulse_count, tip_count, last_temp, last_hum, last_pressure, daily_rain, current_day, wind_gust_pulse_max, last_sample_time
    
    # On configure le timer pour qu'il se relance à la fin de l'exécution
    threading.Timer(SAMPLE_TIME, sample_and_log).start()

    temp, hum, pressure = read_sensors()
    
    # Mise à jour des variables globales pour l'affichage LCD
    last_temp = temp
    last_hum = hum
    last_pressure = pressure

    # Modification : On ne bloque plus l'enregistrement si la température manque.
    # Cela permet de sauver les données de pluie/vent même si le BME280 déconne.
    if temp is None or hum is None:
        print("⚠️ Attention : Lecture Temp/Hum échouée, mais enregistrement Pluie/Vent maintenu.")

    # --- Calculs et réinitialisations ---
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Calcul du temps réel écoulé depuis la dernière mesure pour une précision parfaite
    current_time = time.time()
    elapsed = current_time - last_sample_time
    last_sample_time = current_time
    if elapsed <= 0: elapsed = SAMPLE_TIME # Sécurité

    # Vitesse du vent
    with wind_count_lock:
        wind_hz = wind_pulse_count / elapsed
        wind_pulse_count = 0 # Reset

    # Calcul de la rafale (basé sur le max observé dans update_lcd_realtime)
    with gust_lock:
        gust_hz = wind_gust_pulse_max / 2.0 # Fréquence sur le créneau de 2s le plus rapide
        wind_gust_pulse_max = 0 # Reset pour la minute suivante
    
    wind_gust_kmh = gust_hz * WIND_SPEED_FACTOR
    wind_speed_kmh = wind_hz * WIND_SPEED_FACTOR

    # Direction du vent
    wind_angle = read_wind_vane()
    wind_dir_str = get_wind_direction(wind_angle)

    # Pluie
    # Gestion du cumul journalier
    now_day = datetime.now().day
    if now_day != current_day:
        daily_rain = 0.0
        current_day = now_day

    with tip_count_lock:
        rain_since_last = tip_count * MM_PER_TIP
        daily_rain += rain_since_last
        tip_count = 0 # Reset

    # --- Enregistrement et affichage ---
    pressure_val = f"{pressure:.2f}" if pressure is not None else ""
    temp_val = f"{temp:.2f}" if temp is not None else ""
    hum_val = f"{hum:.2f}" if hum is not None else ""
    
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([now, temp_val, hum_val, pressure_val, f"{rain_since_last:.4f}", f"{wind_speed_kmh:.2f}", f"{wind_gust_kmh:.2f}", wind_dir_str])
        f.flush()

    pressure_str = f"📈 {pressure:.1f}hPa" if pressure is not None else ""
    temp_disp = f"{temp:.1f}°C" if temp is not None else "--.-°C"
    hum_disp = f"{hum:.0f}%" if hum is not None else "--%"
    print(f"[{now}] 🌡 {temp_disp}  💧 {hum_disp}  {pressure_str} 🌧️ {rain_since_last:.2f}mm 💨 {wind_speed_kmh:.1f} km/h ({wind_dir_str})")

    # La mise à jour de l'écran LCD est maintenant gérée par update_lcd_realtime()

def update_lcd_realtime():
    """Met à jour l'écran LCD toutes les 2s pour une réactivité temps réel."""
    global wind_pulse_count_display, last_wind_speed, wind_gust_pulse_max, last_realtime_time
    
    # Relance le timer pour 2 secondes
    threading.Timer(2.0, update_lcd_realtime).start()
    
    # Calcul du temps réel écoulé (ex: 2.01s au lieu de 2.0s)
    current_time = time.time()
    elapsed = current_time - last_realtime_time
    last_realtime_time = current_time
    if elapsed <= 0: elapsed = 2.0

    # --- 1. Calculs (Exécutés même sans écran LCD) ---
    with wind_display_lock:
        wind_hz = wind_pulse_count_display / elapsed
        wind_pulse_count_display = 0
    
    # Mise à jour de la rafale max pour la minute en cours
    with gust_lock:
        # On normalise sur 2s pour garder la compatibilité avec la logique de l'historique
        current_gust_pulses = wind_hz * 2.0
        if current_gust_pulses > wind_gust_pulse_max:
            wind_gust_pulse_max = current_gust_pulses

    last_wind_speed = wind_hz * WIND_SPEED_FACTOR

    # --- 2. Enregistrement haute fréquence (toutes les 2s) ---
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Lecture de la direction (si dispo)
        wind_angle_rt = read_wind_vane()
        wind_dir_rt = get_wind_direction(wind_angle_rt)
        
        with open(WIND_CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([now_str, f"{last_wind_speed:.2f}", wind_dir_rt])
    except Exception as e:
        print(f"Erreur log vent détaillé: {e}")

    # --- 3. Mise à jour de l'écran LCD (si présent) ---
    if lcd:
        try:
            update_lcd_display()
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
    print(f"✅ Bouton LCD: GPIO {BUTTON_PIN}")
    print(f"✅ Girouette: {'AS5600' if as5600 else 'Non trouvée'}")
    print(f"✅ Écran LCD: {'Grove RGB LCD' if lcd else 'Non trouvé'}")
    print("-------------------------------------\n")

print_startup_summary()
sample_and_log() # On lance la première exécution de la boucle de mesure
update_lcd_realtime() # On lance la boucle d'affichage temps réel
