"""Command-line interface: `blackshark-ctl status`, `blackshark-ctl sidetone 5`, ..."""

import argparse
import sys

from . import audio
from .protocol import (BlackShark, DeviceError, EQ_BANDS, PRESETS, PRESET_NAMES,
                       SIDETONE_MAX)


def cmd_status(dev, _):
    s = dev.status()
    print(f"Firmware     : {dev.firmware()}")
    print(f"Battery      : {s['battery']}%{' (charging)' if s['charging'] else ''}")
    print(f"Mic          : {'MUTED' if s['mic_muted'] else 'live'}")
    print(f"Mic monitor  : {s['sidetone'] or 'off'}")
    print(f"EQ preset    : {PRESET_NAMES.get(s['preset'], hex(s['preset']))}")
    print("EQ curve     : " + "  ".join(f"{b}:{v:+d}" for b, v in zip(EQ_BANDS, s["eq"])))
    print(f"Auto off     : {s['idle_minutes'] or 'never'}{' min' if s['idle_minutes'] else ''}")
    print(f"BT DND       : {'on' if s['dnd'] else 'off'}")
    print(f"Audio profile: {audio.active_profile()}")
    print(f"Mic gain     : {audio.mic_volume()}%")
    print(f"Mic chain    : {audio.current_mic_preset()}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="blackshark-ctl",
                                description="Control a Razer BlackShark V2 Pro on Linux")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show everything")
    sub.add_parser("battery", help="print battery percent")
    sp = sub.add_parser("sidetone", help="mic monitoring: 0 = off, 1-10")
    sp.add_argument("level", type=int, choices=range(0, SIDETONE_MAX + 1), metavar="0-10")
    sp = sub.add_parser("preset", help="EQ preset")
    sp.add_argument("name", choices=list(PRESETS))
    sp = sub.add_parser("eq", help="custom EQ, 10 values in dB (31Hz..16kHz)")
    sp.add_argument("bands", type=int, nargs=10)
    sp = sub.add_parser("autooff", help="auto power-off minutes (0 = never, 15-60)")
    sp.add_argument("minutes", type=int)
    sp = sub.add_parser("dnd", help="Bluetooth do-not-disturb")
    sp.add_argument("state", choices=["on", "off"])
    sp = sub.add_parser("mic", help="mic processing chain (EasyEffects)")
    sp.add_argument("preset", choices=["clear", "natural", "off"])
    sp = sub.add_parser("micgain", help="mic hardware gain, percent")
    sp.add_argument("pct", type=int)
    sub.add_parser("fix-profile", help="switch to analog stereo + mic profile")
    a = p.parse_args(argv)

    if a.cmd == "mic":
        try:
            audio.apply_mic_preset({"clear": "BlackShark Clear Mic",
                                    "natural": "BlackShark Natural Mic", "off": "off"}[a.preset])
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    if a.cmd == "micgain":
        audio.set_mic_volume(a.pct)
        return 0
    if a.cmd == "fix-profile":
        audio.use_best_profile()
        return 0

    try:
        with BlackShark() as dev:
            if a.cmd == "status":
                cmd_status(dev, a)
            elif a.cmd == "battery":
                print(dev.battery())
            elif a.cmd == "sidetone":
                dev.set_sidetone(a.level)
            elif a.cmd == "preset":
                dev.set_preset(a.name)
            elif a.cmd == "eq":
                dev.set_eq(a.bands)
            elif a.cmd == "autooff":
                dev.set_idle_minutes(a.minutes)
            elif a.cmd == "dnd":
                dev.set_dnd(a.state == "on")
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
