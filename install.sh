#!/bin/sh
# Installs BlackShark Control for the current user.
# The only root step is the udev rule giving your login session access to the
# headset's control interface (no kernel driver is installed).
set -e

if [ "$(id -u)" = 0 ]; then
    echo "Run this as your normal user, not with sudo; it asks for sudo itself when needed." >&2
    exit 1
fi

HERE=$(cd "$(dirname "$0")" && pwd)
RULE=/etc/udev/rules.d/70-razer-blackshark.rules

if [ ! -f "$RULE" ] || ! cmp -s "$HERE/data/70-razer-blackshark.rules" "$RULE"; then
    echo "Installing udev rule (needs sudo)..."
    sudo install -m 644 "$HERE/data/70-razer-blackshark.rules" "$RULE"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=hidraw
fi

# The mic chain's de-esser and mono->stereo stage are Calf plugins; without
# them EasyEffects silently skips those stages and your voice is left-only.
have_calf() {
    for d in /usr/lib/lv2 /usr/lib64/lv2 /usr/lib/*/lv2 /usr/local/lib/lv2; do
        [ -d "$d/calf.lv2" ] && return 0
    done
    return 1
}
if command -v easyeffects >/dev/null && ! have_calf; then
    if command -v apt-get >/dev/null; then
        echo "Installing calf-plugins (needs sudo)..."
        sudo apt-get install -y calf-plugins || echo "Couldn't install calf-plugins; install it manually." >&2
    else
        echo "Please install the Calf LV2 plugins (calf / calf-plugins) with your package manager." >&2
    fi
fi

APP="$HOME/.local/share/blackshark-control"
BIN="$HOME/.local/bin"
mkdir -p "$APP" "$BIN" "$HOME/.local/share/applications" "$HOME/.config/autostart"
rm -rf "$APP/blackshark"
cp -r "$HERE/blackshark" "$APP/"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +

cat > "$BIN/blackshark-ctl" <<EOF
#!/bin/sh
cd "$APP" && exec python3 -m blackshark.cli "\$@"
EOF
cat > "$BIN/blackshark-control" <<EOF
#!/bin/sh
cd "$APP" && exec python3 -m blackshark.gui "\$@"
EOF
chmod +x "$BIN/blackshark-ctl" "$BIN/blackshark-control"

desktop_entry() {
    cat <<EOF
[Desktop Entry]
Type=Application
Name=BlackShark Control
Comment=Razer BlackShark V2 Pro headset settings
Exec="$BIN/blackshark-control" $1
Icon=audio-headset
Categories=AudioVideo;Audio;Settings;HardwareSettings;
EOF
}
desktop_entry "" > "$HOME/.local/share/applications/blackshark-control.desktop"
{ desktop_entry "--tray"; echo "X-GNOME-Autostart-enabled=true"; } > "$HOME/.config/autostart/blackshark-control.desktop"

# The processed mic only exists while EasyEffects runs, so start it (windowless) at login.
if command -v easyeffects >/dev/null && ! grep -qs easyeffects "$HOME"/.config/autostart/*easyeffects*.desktop; then
    cat > "$HOME/.config/autostart/easyeffects-service.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Easy Effects (background)
Exec=easyeffects --gapplication-service
Icon=com.github.wwmm.easyeffects
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
fi

# Software volume for the headset (WirePlumber 0.4 reads the .lua, 0.5+ the .conf).
mkdir -p "$HOME/.config/wireplumber/main.lua.d" "$HOME/.config/wireplumber/wireplumber.conf.d"
cp "$HERE/data/51-blackshark-softvol.lua" "$HOME/.config/wireplumber/main.lua.d/"
cp "$HERE/data/51-blackshark-softvol.conf" "$HOME/.config/wireplumber/wireplumber.conf.d/"

cd "$APP" && python3 -c "from blackshark import audio; audio.install_mic_presets()"
echo "Done. Launch 'BlackShark Control' from the menu, or run: $BIN/blackshark-ctl status"
echo "Log out and back in (or restart PipeWire) for the volume rule to take effect."
