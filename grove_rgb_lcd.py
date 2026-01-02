# -*- coding: utf-8 -*-
#
# Pilote pour le Grove - LCD RGB Backlight
# Ce code est une adaptation pour smbus2 et est conçu pour être simple d'utilisation.
#

import time
import smbus2

# Adresses I2C par défaut du module
LCD_ADDRESS = 0x3e
RGB_ADDRESS = 0x62

# Commandes pour le contrôleur LCD
LCD_CLEARDISPLAY = 0x01
LCD_RETURNHOME = 0x02
LCD_ENTRYMODESET = 0x04
LCD_DISPLAYCONTROL = 0x08
LCD_FUNCTIONSET = 0x20
LCD_SETCGRAMADDR = 0x40
LCD_SETDDRAMADDR = 0x80

# Flags pour le mode d'entrée
LCD_ENTRYLEFT = 0x02
LCD_ENTRYSHIFTDECREMENT = 0x00

# Flags pour le contrôle de l'affichage
LCD_DISPLAYON = 0x04
LCD_DISPLAYOFF = 0x00
LCD_CURSORON = 0x02
LCD_CURSOROFF = 0x00
LCD_BLINKON = 0x01
LCD_BLINKOFF = 0x00

# Flags pour le paramétrage des fonctions
LCD_8BITMODE = 0x10
LCD_4BITMODE = 0x00
LCD_2LINE = 0x08
LCD_1LINE = 0x00
LCD_5x10DOTS = 0x04
LCD_5x8DOTS = 0x00

class RgbLcd:
    """Classe pour contrôler l'écran LCD RGB Grove."""
    def __init__(self, bus=1):
        self.bus = smbus2.SMBus(bus)
        
        # Initialisation de l'écran LCD
        self._command(LCD_FUNCTIONSET | LCD_2LINE | LCD_5x8DOTS)
        self._command(LCD_DISPLAYCONTROL | LCD_DISPLAYON)
        self._command(LCD_CLEARDISPLAY)
        time.sleep(0.002)
        self._command(LCD_ENTRYMODESET | LCD_ENTRYLEFT)

        # Initialisation du rétroéclairage RGB
        self.set_rgb(0, 0, 0)

    def _command(self, cmd):
        """Envoie une commande à l'écran."""
        self.bus.write_byte_data(LCD_ADDRESS, 0x80, cmd)

    def _write(self, data):
        """Écrit un caractère sur l'écran."""
        self.bus.write_byte_data(LCD_ADDRESS, 0x40, data)

    def set_cursor(self, col, row):
        """Positionne le curseur."""
        if row == 0:
            col |= 0x80
        else:
            col |= 0xc0
        self._command(col)

    def write(self, text):
        """Écrit une chaîne de caractères sur l'écran."""
        # S'assure que le texte est bien une chaîne
        if not isinstance(text, str):
            text = str(text)
            
        for char in text:
            self._write(ord(char))

    def clear(self):
        """Efface l'écran."""
        self._command(LCD_CLEARDISPLAY)
        time.sleep(0.002)

    def set_rgb(self, r, g, b):
        """Définit la couleur du rétroéclairage."""
        try:
            self.bus.write_byte_data(RGB_ADDRESS, 0, 0)
            self.bus.write_byte_data(RGB_ADDRESS, 1, 0)
            self.bus.write_byte_data(RGB_ADDRESS, 0x08, 0xaa)
            self.bus.write_byte_data(RGB_ADDRESS, 4, r)
            self.bus.write_byte_data(RGB_ADDRESS, 3, g)
            self.bus.write_byte_data(RGB_ADDRESS, 2, b)
        except IOError:
            # Si le rétroéclairage n'est pas trouvé, on continue sans.
            print("Avertissement : Le contrôleur de rétroéclairage RGB n'a pas été trouvé.")
            pass