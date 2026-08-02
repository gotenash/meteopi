#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import getpass
from werkzeug.security import generate_password_hash

CONFIG_FILE = "config.json"

def main():
    print("--- MeteoPi Password Reset Tool ---")
    
    # 1. Load existing config
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print("Loaded config.json successfully.")
        except Exception as e:
            print(f"Error loading config.json: {e}")
            print("Creating a new configuration structure.")
    else:
        print("config.json not found. A new one will be created.")

    # 2. Get new password
    while True:
        password = getpass.getpass("Enter new administrator password: ")
        confirm = getpass.getpass("Confirm new administrator password: ")
        
        if len(password) < 4:
            print("Password must be at least 4 characters long.")
            continue
            
        if password == confirm:
            break
        print("Passwords do not match. Please try again.\n")

    # 3. Hash and update
    try:
        password_hash = generate_password_hash(password)
        config["admin_password_hash"] = password_hash
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
            
        print("\nPassword successfully updated in config.json!")
        print("Please restart the web service to apply the change:")
        print("  sudo systemctl restart meteo-web.service")
    except Exception as e:
        print(f"Error saving new password: {e}")

if __name__ == "__main__":
    main()
