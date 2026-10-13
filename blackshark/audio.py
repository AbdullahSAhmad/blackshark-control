"""PipeWire / EasyEffects side: card profile, volumes and the mic processing chain."""

import json
import os
import re
import shutil
import subprocess
import time

CARD_MATCH = "BlackShark_V2_Pro"
EE_SOURCE = "easyeffects_source"
EE_INPUT_DIR = os.path.expanduser("~/.config/easyeffects/input")

MIC_PRESETS = {
    "off": "Mic processing off",
    "BlackShark Clear Mic": "AI noise removal + broadcast voice",
    "BlackShark Natural Mic": "Light cleanup, no noise removal",
}


def _run(*args, check=False):
    try:
        r = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError:
        if check:
            raise RuntimeError(f"{args[0]} is not installed")
        return ""
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or f"{args[0]} failed")
    return r.stdout


def _pactl_list(kind):
    """Parse `pactl list short <kind>` into (index, name) tuples."""
    out = []
    for line in _run("pactl", "list", "short", kind).splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            out.append((cols[0], cols[1]))
    return out


def card_name():
    for _, name in _pactl_list("cards"):
        if CARD_MATCH in name:
            return name
    return None


def sink_name():
    for _, name in _pactl_list("sinks"):
        if CARD_MATCH in name:
            return name
    return None


def source_name():
    for _, name in _pactl_list("sources"):
        if CARD_MATCH in name and not name.endswith(".monitor"):
            return name
    return None


def active_profile():
    card = card_name()
    if not card:
        return None
    block = _run("pactl", "list", "cards").split("Name: " + card, 1)
    if len(block) < 2:
        return None
    m = re.search(r"Active Profile: (\S+)", block[1])
    return m.group(1) if m else None


def profiles():
    """[(name, description)] of the headset card's available profiles."""
    card = card_name()
    if not card:
        return []
    block = _run("pactl", "list", "cards").split("Name: " + card, 1)
    if len(block) < 2:
        return []
    block = block[1].split("\nCard #", 1)[0]
    section = block.split("Profiles:", 1)[-1].split("Active Profile:", 1)[0]
    out = []
    for m in re.finditer(r"^\s+(\S+): (.+?) \(sinks: (\d+), sources: (\d+).*available: (\w+)\)",
                         section, re.M):
        name, desc, sinks, sources, avail = m.groups()
        if avail == "yes" and name != "off":
            out.append((name, desc))
    return out


def set_profile(name):
    card = card_name()
    if card:
        _run("pactl", "set-card-profile", card, name)
        # The headset's mic gets a new node name after a profile switch.
        for _ in range(20):
            time.sleep(0.1)
            if source_name():
                break
        sync_easyeffects_input()


def use_best_profile():
    """Analog stereo output + mic: the USB stream is identical, but the
    IEC958 (digital) profile is meant for S/PDIF and confuses some apps."""
    card = card_name()
    if card:
        set_profile("output:analog-stereo+input:mono-fallback")


def _volume_pct(kind, name):
    out = _run("pactl", f"get-{kind}-volume", name)
    m = re.search(r"(\d+)%", out)
    return int(m.group(1)) if m else None


def mic_volume():
    src = source_name()
    return _volume_pct("source", src) if src else None


def set_mic_volume(pct):
    src = source_name()
    if src:
        _run("pactl", "set-source-volume", src, f"{int(pct)}%")


def output_volume():
    sink = sink_name()
    return _volume_pct("sink", sink) if sink else None


def set_output_volume(pct):
    sink = sink_name()
    if sink:
        _run("pactl", "set-sink-volume", sink, f"{int(pct)}%")


def make_default():
    """Route system audio to the headset."""
    sink = sink_name()
    if sink:
        _run("pactl", "set-default-sink", sink)


# -- EasyEffects mic chain ---------------------------------------------------

def easyeffects_available():
    return shutil.which("easyeffects") is not None


def easyeffects_running():
    return EE_SOURCE in (n for _, n in _pactl_list("sources"))


def _wait_for_easyeffects(seconds):
    for _ in range(int(seconds * 10)):
        if easyeffects_running():
            return True
        time.sleep(0.1)
    return False


def start_easyeffects():
    if not easyeffects_available():
        raise RuntimeError("EasyEffects is not installed")
    already = subprocess.run(["pgrep", "-x", "easyeffects"], capture_output=True).returncode == 0
    if already:
        # It may still be starting up, or be left over from a PipeWire restart
        # (running, but without its virtual devices). Only replace it in the
        # second case: a new launch would just hand off to the stale instance.
        if _wait_for_easyeffects(5):
            return
        subprocess.run(["pkill", "-x", "easyeffects"])
        time.sleep(1)
    subprocess.Popen(["easyeffects", "--gapplication-service"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    _wait_for_easyeffects(5)


def sync_easyeffects_input(force=False):
    """Point EasyEffects' mic chain at the headset mic's current node name.

    The node is renamed whenever the card profile changes or the dongle is
    replugged, which otherwise leaves the processed mic silent."""
    src = source_name()
    # Leave EasyEffects alone unless our mic chain is in use: it may be
    # processing some other microphone.
    if not src or (not force and current_mic_preset() == "off"):
        return
    base = "com.github.wwmm.easyeffects.streaminputs"
    if _run("gsettings", "get", base, "input-device").strip().strip("'") != src:
        _run("gsettings", "set", base, "use-default-input-device", "false")
        _run("gsettings", "set", base, "input-device", src)


def _compressor(threshold, ratio, makeup):
    return {
        "bypass": False, "mode": "Downward", "attack": 5.0, "release": 120.0,
        "threshold": threshold, "ratio": ratio, "knee": -6.0, "makeup": makeup,
        "input-gain": 0.0, "output-gain": 0.0, "dry": -80.01, "wet": 0.0,
        "hpf-mode": "off", "lpf-mode": "off",
        "sidechain": {"type": "Feed-forward", "mode": "RMS", "source": "Middle",
                      "stereo-split-source": "Left/Right", "preamp": 0.0,
                      "reactivity": 10.0, "lookahead": 0.0},
    }


def _preset_json(name):
    hpf = {"bypass": False, "type": "High-pass", "mode": "RLC (BT)", "slope": "x2",
           "frequency": 90.0, "input-gain": 0.0, "output-gain": 0.0}
    limiter = {"bypass": False, "mode": "Herm Thin", "threshold": -1.5,
               "lookahead": 5.0, "attack": 5.0, "release": 20.0,
               "input-gain": 0.0, "output-gain": 0.0, "gain-boost": True}
    # The headset mic is mono and EasyEffects feeds it to the left channel
    # only; copy it to both so stereo recorders aren't one-sided.
    mono = {"bypass": False, "mode": "LR > LL (Mono Left Channel)",
            "input-gain": 0.0, "output-gain": 0.0}
    if name == "BlackShark Clear Mic":
        chain = {
            "stereo_tools#0": mono,
            "filter#0": hpf,
            "rnnoise#0": {"bypass": False, "model-path": "", "enable-vad": True,
                          "vad-thres": 60.0, "release": 30.0, "wet": 0.0,
                          "input-gain": 0.0, "output-gain": 0.0},
            "deesser#0": {"bypass": False, "detection": "RMS", "mode": "Wide",
                          "threshold": -24.0, "ratio": 3.0, "makeup": 0.0,
                          "f1-freq": 6000.0, "f2-freq": 4500.0, "f2-level": 12.0,
                          "input-gain": 0.0, "output-gain": 0.0},
            "compressor#0": _compressor(-22.0, 3.5, 5.0),
            "limiter#0": limiter,
        }
    else:
        chain = {
            "stereo_tools#0": mono,
            "filter#0": hpf,
            "compressor#0": _compressor(-18.0, 2.0, 2.0),
            "limiter#0": limiter,
        }
    return {"input": {"blocklist": [], "plugins_order": list(chain), **chain}}


def install_mic_presets():
    os.makedirs(EE_INPUT_DIR, exist_ok=True)
    for name in MIC_PRESETS:
        if name == "off":
            continue
        with open(os.path.join(EE_INPUT_DIR, name + ".json"), "w") as f:
            json.dump(_preset_json(name), f, indent=4)


def _empty_preset():
    path = os.path.join(EE_INPUT_DIR, "BlackShark Off.json")
    with open(path, "w") as f:
        json.dump({"input": {"blocklist": [], "plugins_order": []}}, f, indent=4)
    return "BlackShark Off"


def apply_mic_preset(name):
    """Load a mic preset into EasyEffects and make the processed mic the
    system default input, or restore the raw headset mic for "off"."""
    if name == "off":
        src = source_name()
        if src:
            _run("pactl", "set-default-source", src)
        if easyeffects_running():
            _run("easyeffects", "-l", _empty_preset())
        return
    install_mic_presets()
    if not easyeffects_running():
        start_easyeffects()
    sync_easyeffects_input(force=True)
    _run("easyeffects", "-l", name, check=True)
    _run("pactl", "set-default-source", EE_SOURCE)


def current_mic_preset():
    out = _run("gsettings", "get", "com.github.wwmm.easyeffects", "last-used-input-preset").strip().strip("'")
    default = _run("pactl", "get-default-source").strip()
    if out in MIC_PRESETS and default == EE_SOURCE:
        return out
    return "off"
