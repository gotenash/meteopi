#!/usr/bin/env bash

# Script de surveillance et reconnexion automatique du Wifi (Wifi Watchdog)
# Ce script ping la passerelle par défaut (le point d'accès Wifi).
# Si le ping échoue, il tente de réinitialiser la connexion Wifi.

INTERFACE="wlan0"

# Attente au démarrage du système pour laisser le temps au réseau de s'établir
sleep 60

echo "🔍 Démarrage du Wifi Watchdog pour l'interface $INTERFACE..."

while true; do
    # Récupérer l'IP de la passerelle par défaut pour l'interface Wifi
    GATEWAY=$(ip route show dev "$INTERFACE" | grep default | awk '{print $3}' | head -n 1)
    
    # Si pas de passerelle, on a perdu la connexion Wifi
    if [ -z "$GATEWAY" ]; then
        echo "[$(date)] ⚠️ Pas de route par défaut pour $INTERFACE. Reconnexion nécessaire."
        # Tenter la reconnexion
        RECONNECT=true
    else
        # Si la passerelle existe, on la ping.
        ping -c 2 -W 2 "$GATEWAY" > /dev/null 2>&1
        if [ $? -eq 0 ]; then
            # Passerelle accessible, le Wifi est connecté
            RECONNECT=false
        else
            echo "[$(date)] ⚠️ Passerelle $GATEWAY injoignable pour $INTERFACE."
            # Attendre 5 secondes et réessayer une fois pour éviter les micro-coupures
            sleep 5
            ping -c 2 -W 2 "$GATEWAY" > /dev/null 2>&1
            if [ $? -eq 0 ]; then
                RECONNECT=false
            else
                echo "[$(date)] ⚠️ Passerelle $GATEWAY toujours injoignable. Tentative de reconnexion..."
                RECONNECT=true
            fi
        fi
    fi

    if [ "$RECONNECT" = true ]; then
        # Détection et utilisation du bon utilitaire réseau
        if command -v nmcli > /dev/null 2>&1; then
            echo "[$(date)] 🔧 NetworkManager détecté. Redémarrage de l'interface..."
            sudo nmcli device disconnect "$INTERFACE" > /dev/null 2>&1
            sleep 2
            sudo nmcli device connect "$INTERFACE" > /dev/null 2>&1
        elif command -v wpa_cli > /dev/null 2>&1; then
            echo "[$(date)] 🔧 wpa_supplicant détecté. Reconnexion..."
            sudo wpa_cli -i "$INTERFACE" reassociate > /dev/null 2>&1
            sleep 5
            sudo dhclient "$INTERFACE" > /dev/null 2>&1 || sudo dhcpcd -n "$INTERFACE" > /dev/null 2>&1
        else
            echo "[$(date)] 🔧 Commande de base ip link. Redémarrage de l'interface..."
            sudo ip link set "$INTERFACE" down
            sleep 2
            sudo ip link set "$INTERFACE" up
        fi
        
        # Attendre 30 secondes pour laisser le temps à la reconnexion de s'établir
        sleep 30
    else
        # Wifi ok, on revérifie dans 60 secondes
        sleep 60
    fi
done
