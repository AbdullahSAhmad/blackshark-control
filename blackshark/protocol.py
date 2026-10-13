"""Razer BlackShark V2 Pro (2.4 GHz dongle, USB 1532:0555) control protocol.

Talks to the headset over its vendor HID interface through /dev/hidraw, so no
kernel driver is needed. The protocol was reverse-engineered by OpenRazer
contributors from Razer Synapse USB captures of this exact device (openrazer
PR #2862); the framing, command ids and apply sequences below mirror their
hardware-verified driver.

Frame layout (64 bytes, report id 0x02 first):
  request : [1]=0x80 [2]=8+len [5]='P' [6]='A' [7]=0x08 [9]=type
            [10]=cmd [11]=flag [12]=len [13..]=payload
  reply   : [12]=cmd [13]=0x01 ack [14]=len [15..]=payload
"""

import glob
import os
import select
import threading
import time

VENDOR_ID = 0x1532
PRODUCT_IDS = (0x0555,)

REPORT_ID = 0x02
REPORT_LEN = 64

TYPE_REMOTE = 0x02
TYPE_QUERY = 0x03
TYPE_AUDIO = 0x04
TYPE_EQ = 0x0D

CMD_REMOTE = 0xE1
CMD_SERIAL = 0x00
CMD_FW_VER = 0x02
CMD_HW_MODEL = 0x03
CMD_PRESET_GET = 0x13
CMD_EQ_GET = 0x15
CMD_SIDETONE_GET = 0x18
CMD_SIDETONE_LVL_GET = 0x19
CMD_PREP = 0x1E
CMD_LINK = 0x20
CMD_BATTERY = 0x21
CMD_DND_GET = 0x27
CMD_CHARGING = 0x2A
CMD_IDLE_GET = 0x2C
CMD_MIC_MUTE = 0x55

CMD_PRESET = 0x93
CMD_EQ = 0x95
CMD_SIDETONE = 0x98
CMD_SIDETONE_LVL = 0x99
CMD_ENHANCE = 0x9D
CMD_DND = 0xA7
CMD_IDLE_SET = 0xAC

PRESET_GAME = 0x07
PRESET_MUSIC = 0x08
PRESET_MOVIE = 0x09
PRESET_CUSTOM = 0xFF

# Ordered as Razer Synapse lists them.
PRESETS = {
    "game": PRESET_GAME,
    "music": PRESET_MUSIC,
    "movie": PRESET_MOVIE,
    "custom": PRESET_CUSTOM,
    "esports1": 0xFA,
    "esports2": 0xFE,
    "esports3": 0xFB,
    "esports4": 0xFD,
    "esports5": 0xFC,
}
PRESET_NAMES = {v: k for k, v in PRESETS.items()}

EQ_BANDS = ("31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
EQ_MIN, EQ_MAX = -12, 12
SIDETONE_MAX = 10
IDLE_MIN, IDLE_MAX = 15, 60

REMOTE_SETTLE = 0.035
REPLY_TIMEOUT = 0.15
QUERY_ATTEMPTS = 3


class DeviceError(Exception):
    pass


class DeviceNotFound(DeviceError):
    pass


class HeadsetUnreachable(DeviceError):
    """The dongle is present but the headset is off or out of range."""


def is_game_eq(preset):
    return 0xFA <= preset <= 0xFE


def find_hidraw():
    """Return the /dev/hidrawN node of the dongle's vendor control interface."""
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(node, "device", "uevent")) as f:
                uevent = f.read()
        except OSError:
            continue
        hid_id = next((l.split("=", 1)[1] for l in uevent.splitlines() if l.startswith("HID_ID=")), "")
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        vid, pid = int(parts[1], 16), int(parts[2], 16)
        if vid != VENDOR_ID or pid not in PRODUCT_IDS:
            continue
        # Only the vendor interface carries report id 2.
        try:
            with open(os.path.join(node, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if b"\x06\x00\xff" in desc and b"\x85\x02" in desc:
            return "/dev/" + os.path.basename(node)
    raise DeviceNotFound("BlackShark V2 Pro dongle not found")


class BlackShark:
    def __init__(self, path=None):
        self.path = path or find_hidraw()
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as e:
            raise DeviceError(
                f"No permission to open {self.path}. Run install.sh to add the udev rule."
            ) from e
        self.lock = threading.RLock()
        self.on_event = None  # callback(cmd, payload) for unsolicited frames

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- framing -----------------------------------------------------------

    def _write(self, frame):
        os.write(self.fd, bytes(frame))

    def _frame(self, cmd_type, cmd, payload=b""):
        buf = bytearray(REPORT_LEN)
        buf[0] = REPORT_ID
        buf[1] = 0x80
        buf[2] = 8 + len(payload)
        buf[5], buf[6] = ord("P"), ord("A")
        buf[7] = 0x08
        buf[9] = cmd_type
        buf[10] = cmd
        buf[12] = len(payload)
        buf[13:13 + len(payload)] = payload
        return buf

    def _send(self, cmd_type, cmd, payload=b""):
        self._write(self._frame(cmd_type, cmd, payload))

    def _remote(self, on):
        buf = bytearray(REPORT_LEN)
        buf[0] = REPORT_ID
        buf[1] = 0x80
        buf[2] = 0x07
        buf[5], buf[6] = ord("P"), ord("A")
        buf[7] = 0x0E
        buf[9] = TYPE_REMOTE
        buf[10] = CMD_REMOTE
        buf[11] = 1 if on else 0
        self._write(buf)
        time.sleep(REMOTE_SETTLE)

    def _drain(self):
        while True:
            try:
                data = os.read(self.fd, REPORT_LEN)
            except BlockingIOError:
                return
            self._dispatch_event(data)

    def _dispatch_event(self, data):
        if self.on_event and len(data) > 15 and data[0] == REPORT_ID and data[13] == 0x01:
            n = min(data[14], len(data) - 15)
            self.on_event(data[12], bytes(data[15:15 + n]))

    def _await_reply(self, cmd, timeout):
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                return None
            try:
                data = os.read(self.fd, REPORT_LEN)
            except BlockingIOError:
                continue
            if len(data) > 15 and data[0] == REPORT_ID and data[12] == cmd and data[13] == 0x01:
                n = min(data[14], len(data) - 15)
                return bytes(data[15:15 + n])
            self._dispatch_event(data)

    # -- primitives --------------------------------------------------------

    def query(self, cmd):
        """Read a register. Raises HeadsetUnreachable if the headset is off."""
        with self.lock:
            self._drain()
            self._remote(True)  # sacrificial: wakes a dozing link
            try:
                for _ in range(QUERY_ATTEMPTS):
                    self._remote(True)
                    self._send(TYPE_QUERY, cmd)
                    reply = self._await_reply(cmd, REPLY_TIMEOUT)
                    if reply is not None:
                        return reply
                raise HeadsetUnreachable("headset did not answer (off or out of range?)")
            finally:
                self._remote(False)

    def query_byte(self, cmd):
        reply = self.query(cmd)
        if not reply:
            raise DeviceError(f"empty reply to 0x{cmd:02x}")
        return reply[0]

    def write_value(self, cmd, value):
        with self.lock:
            self._remote(True)
            self._remote(True)
            self._send(TYPE_AUDIO, cmd, bytes([value & 0xFF]))
            self._remote(False)

    def poll_events(self):
        """Process unsolicited frames (e.g. EQ button presses) without blocking."""
        if self.lock.acquire(blocking=False):
            try:
                self._drain()
            finally:
                self.lock.release()

    # -- info --------------------------------------------------------------

    def battery(self):
        return self.query_byte(CMD_BATTERY)

    def charging(self):
        return bool(self.query_byte(CMD_CHARGING))

    def mic_muted(self):
        return bool(self.query_byte(CMD_MIC_MUTE))

    def serial(self):
        return self.query(CMD_SERIAL).rstrip(b"\x00").decode("ascii", "replace")

    def firmware(self):
        fw = self.query(CMD_FW_VER)
        return f"{fw[0]}.{fw[1]}" if len(fw) >= 2 else "?"

    # -- sidetone (mic monitoring) ------------------------------------------

    def sidetone(self):
        """0 = off, 1..10 = level."""
        if not self.query_byte(CMD_SIDETONE_GET):
            return 0
        return self.query_byte(CMD_SIDETONE_LVL_GET)

    def set_sidetone(self, level):
        level = max(0, min(SIDETONE_MAX, int(level)))
        self.write_value(CMD_SIDETONE, 1 if level else 0)
        if level:
            self.write_value(CMD_SIDETONE_LVL, level)

    # -- auto power-off ------------------------------------------------------

    def idle_minutes(self):
        return self.query_byte(CMD_IDLE_GET)

    def set_idle_minutes(self, minutes):
        minutes = int(minutes)
        if minutes:
            minutes = max(IDLE_MIN, min(IDLE_MAX, minutes))
        self.write_value(CMD_IDLE_SET, minutes)

    # -- do not disturb (Bluetooth) ----------------------------------------

    def dnd(self):
        return bool(self.query_byte(CMD_DND_GET))

    def set_dnd(self, on):
        self.write_value(CMD_DND, 1 if on else 0)

    # -- equalizer ---------------------------------------------------------

    def preset(self):
        return self.query_byte(CMD_PRESET_GET)

    def _apply_preset(self, preset, bands=None):
        flag = 0x02 if is_game_eq(preset) else 0x01
        with self.lock:
            self._remote(True)
            self._remote(True)
            self._send(TYPE_QUERY, CMD_PREP)
            self._remote(True)
            self._send(TYPE_AUDIO, CMD_PRESET, bytes([preset]))
            self._remote(True)
            self._send(TYPE_AUDIO, CMD_ENHANCE, bytes([flag]))
            if bands is not None:
                self._remote(True)
                self._send(TYPE_EQ, CMD_EQ, bytes(b & 0xFF for b in bands))
            # Synapse ends an apply sequence with remote *on* (not off); the
            # OpenRazer driver mirrors that capture, and so do we.
            self._remote(True)

    def set_preset(self, preset):
        """Select an EQ preset, verified by read-back.

        Switching between the classic and esports families only flips the
        family on the first try, so repeat until the device confirms it.
        """
        if isinstance(preset, str):
            preset = PRESETS[preset]
        for _ in range(4):
            self._apply_preset(preset)
            time.sleep(0.1)
            if self.preset() == preset:
                return
        raise DeviceError("headset did not accept the EQ preset")

    def eq(self):
        """Bands (dB) of the active preset's curve slot."""
        raw = self.query(CMD_EQ_GET)[:len(EQ_BANDS)]
        return [b - 256 if b > 127 else b for b in raw]

    def set_eq(self, bands):
        """Store a custom 10-band curve and make it the active preset."""
        if len(bands) != len(EQ_BANDS):
            raise ValueError(f"need {len(EQ_BANDS)} bands")
        bands = [max(EQ_MIN, min(EQ_MAX, int(round(b)))) for b in bands]
        changed = self.preset() != PRESET_CUSTOM
        if changed:
            # The device writes the curve into whichever slot is active, so
            # custom must be confirmed active first or another preset is overwritten.
            self.set_preset(PRESET_CUSTOM)
        # The bands frame stores the curve; a following selector-only apply
        # latches it into the audio path.
        self._apply_preset(PRESET_CUSTOM, bands)
        time.sleep(0.1)
        self._apply_preset(PRESET_CUSTOM)

    # -- snapshot ------------------------------------------------------------

    def status(self):
        s = {
            "battery": self.battery(),
            "charging": self.charging(),
            "mic_muted": self.mic_muted(),
            "sidetone": self.sidetone(),
            "idle_minutes": self.idle_minutes(),
            "preset": self.preset(),
            "dnd": self.dnd(),
        }
        s["eq"] = self.eq()
        return s
