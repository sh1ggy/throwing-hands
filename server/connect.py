"""Find and open the HuskyLens, whichever way it's plugged in (design doc D1).

``open_link()`` tries, in order:
  1. ``bridge``: the XIAO bridge (D1-A, the rig link), camera on the Gravity cable. Used if the
     camera knocks back through it.
  2. ``usb``:    the camera's own micro-USB port (CP210x, firmware >= 0.5.1). One cable, no XIAO.
  If the XIAO answers but the camera doesn't (and there's no camera USB), the XIAO link is
  returned anyway so the caller can say "XIAO OK, camera not answering".
``serial`` (a USB-TTL adapter, D1-B) needs an explicit port. ``fake`` is a simulated XIAO
(``bridge.FakeBridge``) with a simulated camera (``FakeCamera``) behind it that speaks the real frame
protocol, already trained on the gestures and three faces, for working without hardware (NFR-08).

    link, desc = open_link()                 # auto
    link, desc = open_link("usb")
    link, desc = open_link("bridge", "/dev/cu.usbmodem103", baud=9600)
    hl = HuskyLens(link)
"""

from __future__ import annotations

import random
import time
from collections import Counter

import huskylens as H

KINDS = ("auto", "usb", "bridge", "serial", "fake")


class NoCamera(RuntimeError):
    pass


def _knocks(link) -> bool:
    for _ in range(3):
        frames = H.parse_frames(link.husky(H.build_frame(H.KNOCK)))
        if any(f.cmd == H.RETURN_OK for f in frames):
            return True
    return False


def open_link(kind: str = "auto", port: str | None = None, baud: int | None = None):
    """Return ``(link, description)``. ``link`` implements ``huskylens.Transport``.

    ``baud`` is the HuskyLens UART speed for bridge/serial (default 9600, §8.2), or a fixed
    USB speed for usb (default: probe 3 M / 115200 / 9600).
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "fake":
        from bridge import FakeBridge

        return FakeBridge(FakeCamera(pretrained=True)), "fake bridge + camera (simulated)"
    tried: list[str] = []
    fallback = None  # a bridge that pings but whose camera didn't knock

    if kind in ("auto", "bridge"):
        from bridge import Bridge, find_bridge_port

        p = port if kind == "bridge" and port else find_bridge_port()
        if p:
            link = Bridge(p)
            version = link.ping().get("version", "?")
            link.set_husky_baud(baud or 9600)
            desc = f"bridge {p} ({version}) @ {baud or 9600}"
            if kind == "bridge" or _knocks(link):
                return link, desc
            fallback = (link, desc)
            tried.append(f"XIAO {p}: camera not answering through it")
        else:
            tried.append("XIAO bridge: nothing answered a ping (unplugged, or another tool such as "
                         "train_ui.py has the port open?)")
            if kind == "bridge":
                raise NoCamera("; ".join(tried))

    if kind in ("auto", "usb"):
        from serial_link import UsbLink, find_husky_usb_port

        p = port if kind == "usb" and port else find_husky_usb_port()
        if p:
            link = UsbLink(p, baud if kind == "usb" else None)
            if link.baud is not None:
                if fallback:
                    fallback[0].close()
                return link, f"usb {p} @ {link.baud}"
            link.close()
            tried.append(f"camera USB {p}: no knock reply (firmware < 0.5.1, or a charge-only cable?)")
        else:
            tried.append("camera USB: no CP210x port")
        if fallback:  # let the caller report "XIAO OK, camera not answering"
            return fallback
        raise NoCamera("; ".join(tried))

    if not port:
        raise NoCamera("serial needs a port (USB-TTL adapter)")
    from serial_link import SerialLink

    return SerialLink(port, baud or 9600), f"serial {port} @ {baud or 9600}"


class FakeCamera:
    """Simulated HuskyLens 0.5.1 at the frame level: implements ``huskylens.Transport``.

    Learn/forget/names/text/SD all answer OK and change state the way the camera would. A request
    returns INFO plus one block for whatever "hand" is in view: ``pose`` if set, else a learned ID
    picked at random about once a second (ID 0 = seen but not learned).
    """

    def __init__(self, seed: int | None = None, pretrained: bool = False):
        self.rng = random.Random(seed)
        self.algorithm = 0
        self.learned: dict[int, Counter] = {}  # algorithm -> samples per ID
        self.names: dict[int, str] = {}
        self.pose: int | None = None  # ID in view for the current algorithm; None = pick at random
        self.frame = 0
        self._auto = (0.0, None)
        self.models: dict[tuple[int, int], Counter] = {}  # (algorithm, SD slot) -> saved samples
        if pretrained:  # D9 gestures 1-4 in object classification, faces 1-3 in face recognition
            self.learned[H.ALGORITHMS["object_classification"]] = Counter({1: 15, 2: 15, 3: 15, 4: 15})
            self.learned[H.ALGORITHMS["face_recognition"]] = Counter({1: 5, 2: 5, 3: 5})

    def close(self) -> None:
        pass

    @staticmethod
    def _ok() -> bytes:
        return H.build_frame(H.RETURN_OK)

    def _in_view(self) -> int | None:
        if self.pose is not None:
            return self.pose
        t, pose = self._auto
        if time.monotonic() - t > 1.0:
            ids = sorted(self.learned.get(self.algorithm, {}))
            pose = self.rng.choice(ids + [None]) if ids else None
            self._auto = (time.monotonic(), pose)
        return pose

    def husky(self, frame: bytes, expect: int | None = None, timeout_ms: int | None = None) -> list[bytes]:
        time.sleep(0.01)  # roughly a real round trip, so polling loops behave
        frames = H.split_frames(frame).frames
        if not frames:
            return []
        f = frames[0]
        w = [v & 0xFFFF for v in f.words()]
        learned = self.learned.setdefault(self.algorithm, Counter())
        self.frame = (self.frame + 3) & 0xFFFF
        if f.cmd == H.ALGORITHM:
            self.algorithm = w[0]
        elif f.cmd == H.LEARN:
            learned[w[0]] += 1
        elif f.cmd == H.FORGET:
            learned.clear()
            self.names.clear()
        elif f.cmd == H.SAVE_MODEL:
            self.models[(self.algorithm, w[0])] = Counter(learned)
        elif f.cmd == H.LOAD_MODEL:
            if (self.algorithm, w[0]) not in self.models:
                return [H.build_frame(H.RETURN_UNSUPPORTED)]
            learned.clear()
            learned.update(self.models[(self.algorithm, w[0])])
        elif f.cmd == H.CUSTOM_NAME:
            self.names[f.data[0]] = f.data[2:].split(b"\0")[0].decode("ascii", "replace")
        elif f.cmd == H.FIRMWARE_VERSION:
            return [H.build_frame(H.FIRMWARE_VERSION, b"0.5.1bNorm (fake)")]
        elif H.REQUEST <= f.cmd <= H.REQUEST_ARROWS_BY_ID:
            seen = self._in_view()
            blocks = []
            if seen is not None:
                bid = seen if seen in learned else 0
                want = w[0] if f.cmd in (H.REQUEST_BY_ID, H.REQUEST_BLOCKS_BY_ID) else None
                learned_only = f.cmd in (H.REQUEST_LEARNED, H.REQUEST_BLOCKS_LEARNED)
                if (want is None or want == bid) and not (learned_only and bid == 0):
                    blocks.append(H.build_frame(H.RETURN_BLOCK, H.u16(160, 120, 90, 110, bid)))
            info = H.build_frame(H.RETURN_INFO, H.u16(len(blocks), len(learned), self.frame, 0, 0))
            return [info, *blocks]
        elif f.cmd not in (H.KNOCK, H.PHOTO, H.CUSTOM_TEXT,
                           H.CLEAR_TEXT, H.SCREENSHOT, H.IS_PRO):
            return [H.build_frame(H.RETURN_UNSUPPORTED)]
        return [self._ok()]
