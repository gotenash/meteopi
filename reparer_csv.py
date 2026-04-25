#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import shutil
import os

CSV_FILE = "meteo_log.csv"
BACKUP_FILE = "meteo_log.csv.bak"
TEMP_FILE = "meteo_log_fixed.csv"

def repair_csv():
    print(f"--- Démarrage de la réparation de {CSV_FILE} ---")
    
    if not os.path.exists(CSV_FILE):
        print(f"Erreur : Le fichier {CSV_FILE} n'existe pas.")
        return

    # 1. Création d'une sauvegarde
    try:
        shutil.copy(CSV_FILE, BACKUP_FILE)
        print(f"✅ Sauvegarde de sécurité créée : {BACKUP_FILE}")
    except Exception as e:
        print(f"❌ Erreur lors de la sauvegarde : {e}")
        return

    # 2. Lecture et correction
    lines_fixed = 0
    lines_ok = 0
    
    try:
        with open(CSV_FILE, 'r', newline='', encoding='utf-8') as f_in, \
             open(TEMP_FILE, 'w', newline='', encoding='utf-8') as f_out:
            
            reader = csv.reader(f_in)
            writer = csv.writer(f_out)
            
            # Lecture de l'ancien en-tête (on l'ignore)
            try:
                old_header = next(reader)
            except StopIteration:
                print("Fichier vide.")
                return

            # On écrit le NOUVEL en-tête correct
            new_header = ["time", "temp", "hum", "pressure", "rain", "wind_speed", "wind_gust", "wind_dir_str"]
            writer.writerow(new_header)
            
            for row in reader:
                if not row: continue # Ignore les lignes vides
                
                # Cas 1 : Ligne à 7 colonnes (Anciennes données)
                if len(row) == 7:
                    # On insère une valeur vide pour la rafale (index 6) avant la direction (index 6 devenant 7)
                    # Row original : [time, temp, hum, press, rain, speed, dir]
                    # Row corrigé  : [time, temp, hum, press, rain, speed, "", dir]
                    new_row = row[:6] + [""] + [row[6]]
                    writer.writerow(new_row)
                    lines_fixed += 1
                
                # Cas 2 : Ligne à 8 colonnes (Déjà correct)
                elif len(row) >= 8:
                    # On ne garde que les 8 premières colonnes au cas où
                    writer.writerow(row[:8])
                    lines_ok += 1

        # 3. Remplacement du fichier original
        os.replace(TEMP_FILE, CSV_FILE)
        print(f"\n✅ Réparation terminée avec succès !")
        print(f"- Anciennes lignes converties (ajout colonne vide) : {lines_fixed}")
        print(f"- Lignes récentes conservées : {lines_ok}")
        print("\nVous pouvez redémarrer l'interface web si elle était arrêtée.")

    except Exception as e:
        print(f"❌ Une erreur est survenue : {e}")
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)

if __name__ == "__main__":
    repair_csv()