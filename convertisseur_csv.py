# -*- coding: utf-8 -*-
import csv
import os

# --- Configuration ---
# Fichier CSV original qui contient peut-être des erreurs de format
FICHIER_SOURCE = "meteo_log.csv"
# Fichier de destination où les données corrigées seront écrites
FICHIER_DESTINATION = "meteo_log_corrige.csv"

def corriger_decimales_csv(source, destination):
    """
    Lit un fichier CSV, remplace les virgules décimales par des points dans les
    colonnes numériques et écrit le résultat dans un nouveau fichier.
    """
    lignes_corrigees = 0
    lignes_total = 0
    
    try:
        with open(source, 'r', newline='', encoding='utf-8') as f_source, \
             open(destination, 'w', newline='', encoding='utf-8') as f_dest:
            
            lecteur = csv.reader(f_source)
            ecrivain = csv.writer(f_dest)

            # Lecture de l'en-tête (la première ligne)
            try:
                en_tete = next(lecteur)
                ecrivain.writerow(en_tete)
                print(f"En-tête copié : {en_tete}")
            except StopIteration:
                print("Le fichier source est vide.")
                return

            # Traitement de chaque ligne de données
            for ligne in lecteur:
                lignes_total += 1
                ligne_corrigee = list(ligne) # On crée une copie modifiable
                a_ete_corrige = False

                # On parcourt les colonnes qui doivent être numériques (de l'index 1 à 5)
                # 0: time, 1: temp, 2: hum, 3: pressure, 4: rain, 5: wind_speed
                for i in range(1, 6):
                    if i < len(ligne_corrigee) and isinstance(ligne_corrigee[i], str) and ',' in ligne_corrigee[i]:
                        ligne_corrigee[i] = ligne_corrigee[i].replace(',', '.', 1)
                        a_ete_corrige = True
                
                if a_ete_corrige:
                    lignes_corrigees += 1

                ecrivain.writerow(ligne_corrigee)

        print("\n--- Conversion terminée ! ---")
        print(f"Total de lignes traitées : {lignes_total}")
        print(f"Lignes corrigées : {lignes_corrigees}")
        print(f"Le fichier corrigé a été sauvegardé sous : '{destination}'")

    except FileNotFoundError:
        print(f"Erreur : Le fichier source '{source}' n'a pas été trouvé.")
    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")

# --- Exécution du script ---
if __name__ == "__main__":
    corriger_decimales_csv(FICHIER_SOURCE, FICHIER_DESTINATION)
    
    # Instructions pour l'utilisateur
    print("\n--- Prochaines étapes ---")
    print(f"1. Fermez votre application web si elle est en cours d'exécution.")
    print(f"2. Supprimez ou renommez l'ancien fichier '{FICHIER_SOURCE}'.")
    print(f"3. Renommez le nouveau fichier '{FICHIER_DESTINATION}' en '{FICHIER_SOURCE}'.")
    print(f"4. Redémarrez votre application web. Les graphiques devraient maintenant fonctionner.")

