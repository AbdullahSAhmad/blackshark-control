#!/bin/sh
# Removes BlackShark Control and everything install.sh added.
# Leaves EasyEffects and the Calf plugins installed (other apps may use them).
set -e

pkill -f "python3 -m blackshark.gui" 2>/dev/null || true

# Hand the system default input back to the raw headset mic.
SRC=$(pactl list short sources 2>/dev/null | awk '/BlackShark/ && !/monitor/ {print $2; exit}')
[ -n "$SRC" ] && pactl set-default-source "$SRC" || true

rm -rf "$HOME/.local/share/blackshark-control" "$HOME/.config/blackshark-control"
rm -f "$HOME/.local/bin/blackshark-ctl" "$HOME/.local/bin/blackshark-control" \
      "$HOME/.local/share/applications/blackshark-control.desktop" \
      "$HOME/.config/autostart/blackshark-control.desktop" \
      "$HOME/.config/autostart/easyeffects-service.desktop" \
      "$HOME/.config/wireplumber/main.lua.d/51-blackshark-softvol.lua" \
      "$HOME/.config/wireplumber/wireplumber.conf.d/51-blackshark-softvol.conf" \
      "$HOME/.config/easyeffects/input/BlackShark Clear Mic.json" \
      "$HOME/.config/easyeffects/input/BlackShark Natural Mic.json" \
      "$HOME/.config/easyeffects/input/BlackShark Off.json"

if [ -f /etc/udev/rules.d/70-razer-blackshark.rules ]; then
    echo "Removing udev rule (needs sudo)..."
    sudo rm -f /etc/udev/rules.d/70-razer-blackshark.rules
    sudo udevadm control --reload-rules
fi
echo "Uninstalled. Log out and back in to drop the volume rule."
