# RPS-Bot XIAO RP2040 bridge (design doc D1-A, D2-A, D3-B, §9.2).
#
# Laptop <-> XIAO: newline-delimited JSON on usb_cdc.data, one request per line,
# every reply is {"id": n, "ok": true|false, ...}.
# XIAO <-> HuskyLens: UART on D6 (TX -> HuskyLens R) / D7 (RX <- HuskyLens T).
#
# Implements every §9.2 command: ping, husky, uart, servo, pose, relax, led, countdown,
# relay, voice. Pins per §8.2: PCA9685 on I2C D4(SDA)/D5(SCL) at 50 Hz. pose drives CH0 upwards, one value per
# channel (1..5 values); the hand as built is CH0 index, CH1 middle, CH2 ring+little (no thumb).
# NeoPixel on D0, relay on D1, ISD1820 PLAYL on D2 and REC on D3.
# The PCA9685 and the NeoPixels are initialised lazily, so the board always boots and
# keeps serving ping/husky even when they are not wired; those commands then reply
# ok:false with a clear error. Timed actions (pose ramps, countdown, voice holds, relay
# is level) are a non-blocking state machine run by tick() from the main loop.
import binascii
import json
import time

import board
import busio
import digitalio
import usb_cdc

VERSION = "cpy-bridge 0.6"
HEADER = b"\x55\xaa\x11"
RETURN_INFO = 0x29
DEFAULT_TIMEOUT_MS = 500

PCA_ADDR = 0x40
PCA_FREQ = 50
NUM_CHANNELS = 5         # CH0-CH4; pose sets the first len(us) of them
US_MIN, US_MAX = 500, 2500   # clamp for servo pulse widths (us)
LED_COUNT = 16           # WS2812 pixels (§8.3)
LED_DEFAULT_BRIGHTNESS = 0.2
VOICE_MAX_MS = 30000
COUNTDOWN_MAX_BEATS = 32

data = usb_cdc.data
uart = busio.UART(board.TX, board.RX, baudrate=9600, timeout=0, receiver_buffer_size=2048)
t0 = time.monotonic()


def out_pin(pin):
    p = digitalio.DigitalInOut(pin)
    p.direction = digitalio.Direction.OUTPUT
    p.value = False
    return p


relay_pin = out_pin(board.D1)
playl_pin = out_pin(board.D2)
rec_pin = out_pin(board.D3)

i2c = None
pca = None
pixels = None
last_us = [None] * NUM_CHANNELS   # last commanded pulse per channel (us)
pose_job = None                   # dict while a ramp is running
count_job = None
voice_job = None
board_px = None                   # the XIAO's own RGB LED (status_led)
blink_job = None


class CmdError(Exception):
    pass


def get_pca():
    global i2c, pca
    if pca is not None:
        return pca
    try:
        from adafruit_pca9685 import PCA9685
        if i2c is None:
            i2c = busio.I2C(board.D5, board.D4)  # SCL, SDA
        pca = PCA9685(i2c, address=PCA_ADDR)
        pca.frequency = PCA_FREQ
    except Exception as e:
        pca = None
        if i2c is not None:
            try:
                i2c.deinit()
            except Exception:
                pass
            i2c = None
        raise CmdError("PCA9685 unavailable (I2C D4/D5, addr 0x%02x): %s: %s" % (PCA_ADDR, type(e).__name__, e))
    return pca


def get_pixels():
    global pixels
    if pixels is None:
        try:
            import neopixel
            pixels = neopixel.NeoPixel(board.D0, LED_COUNT, brightness=LED_DEFAULT_BRIGHTNESS, auto_write=False)
        except Exception as e:
            pixels = None
            raise CmdError("NeoPixel unavailable (D0): %s: %s" % (type(e).__name__, e))
    return pixels


def get_board_px():
    """The XIAO RP2040's onboard NeoPixel (data GP12, power GP11)."""
    global board_px
    if board_px is None:
        try:
            import neopixel
            if hasattr(board, "NEOPIXEL_POWER"):
                pwr = digitalio.DigitalInOut(board.NEOPIXEL_POWER)
                pwr.direction = digitalio.Direction.OUTPUT
                pwr.value = True
                globals()["board_px_power"] = pwr  # keep a reference so it stays on
            board_px = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.3, auto_write=True)
        except Exception as e:
            board_px = None
            raise CmdError("onboard LED unavailable: %s: %s" % (type(e).__name__, e))
    return board_px


def clamp_us(us):
    return max(US_MIN, min(US_MAX, int(us)))


def write_us(ch, us):
    p = get_pca()
    p.channels[ch].duty_cycle = int(us * PCA_FREQ * 65535 / 1000000)
    last_us[ch] = us


def check_ch(ch):
    ch = int(ch)
    if not 0 <= ch < NUM_CHANNELS:
        raise CmdError("ch must be 0..%d" % (NUM_CHANNELS - 1))
    return ch


def colour(c):
    if isinstance(c, str):
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    if len(c) != 3:
        raise CmdError("colour must be [r,g,b] or '#rrggbb'")
    return tuple(max(0, min(255, int(v))) for v in c)


def set_brightness(px, req):
    if req.get("brightness") is not None:
        px.brightness = max(0.0, min(1.0, float(req["brightness"])))


def tick():
    """Advance pose ramps, countdown and voice holds. Never blocks."""
    global pose_job, count_job, voice_job, blink_job
    now = time.monotonic()
    if pose_job is not None:
        j = pose_job
        f = (now - j["t0"]) / j["dur"]
        try:
            if f >= 1:
                pose_job = None
                f = 1
            for ch in range(len(j["b"])):
                write_us(ch, j["a"][ch] + (j["b"][ch] - j["a"][ch]) * f)
        except Exception:
            pose_job = None
    if count_job is not None:
        j = count_job
        k = int((now - j["t0"]) / j["period"])
        if k >= j["beats"]:
            count_job = None
            try:
                get_pixels().fill((0, 0, 0))
                pixels.show()
            except Exception:
                pass
        elif k != j["k"]:
            j["k"] = k
            try:
                px = get_pixels()
                px.fill(j["cols"][k % len(j["cols"])])
                px.show()
            except Exception:
                count_job = None
    if blink_job is not None:
        j = blink_job
        on = int((now - j["t0"]) * j["hz"] * 2) % 2 == 0
        if on != j["on"]:
            j["on"] = on
            try:
                board_px[0] = j["col"] if on else (0, 0, 0)
            except Exception:
                blink_job = None
    if voice_job is not None and now >= voice_job["end"]:
        voice_job["pin"].value = False
        voice_job = None


def hx(b):
    # CircuitPython's bytes.hex() returns bytes, which json can't encode
    return binascii.hexlify(b).decode()


def split_frames(buf):
    """Return (frames, rest): complete checksum-valid frames and leftover bytes."""
    frames = []
    i = 0
    n = len(buf)
    while True:
        j = buf.find(HEADER, i)
        if j < 0:
            # keep a possible partial header at the tail
            keep = 2 if buf[n - 2:] == HEADER[:2] else (1 if buf[n - 1:] == HEADER[:1] else 0)
            return frames, buf[n - keep:] if keep else b""
        if n - j < 6:
            return frames, buf[j:]
        total = buf[j + 3] + 6
        if n - j < total:
            return frames, buf[j:]
        frame = buf[j:j + total]
        if sum(frame[:-1]) & 0xFF != frame[-1]:
            i = j + 1
            continue
        frames.append(bytes(frame))
        i = j + total


def done(frames, expect):
    if not frames:
        return False
    if expect is not None:
        return len(frames) >= expect
    first = frames[0]
    if first[4] == RETURN_INFO and len(first) >= 7:
        count = first[5] | (first[6] << 8)
        return len(frames) >= 1 + count
    return True


def husky(req):
    frame = binascii.unhexlify(req["hex"])
    expect = req.get("expect")
    timeout = (req.get("timeout_ms") or DEFAULT_TIMEOUT_MS) / 1000
    uart.reset_input_buffer()
    uart.write(frame)
    deadline = time.monotonic() + timeout
    buf = b""
    frames = []
    rx = 0
    while time.monotonic() < deadline:
        tick()
        chunk = uart.read(64)
        if chunk:
            rx += len(chunk)
            new, buf = split_frames(buf + chunk)
            frames.extend(new)
            if done(frames, expect):
                break
    return {"ok": bool(frames), "frames": [hx(f) for f in frames],
            "rx_bytes": rx, "rest": hx(buf[:32]),
            "error": None if frames else ("no reply" if not rx else "no valid frame")}


def cmd_servo(req):
    global pose_job
    ch = check_ch(req["ch"])
    us = clamp_us(req["us"])
    write_us(ch, us)
    pose_job = None
    return {"ok": True, "ch": ch, "us": us}


def cmd_pose(req):
    global pose_job
    targets = req["us"]
    n = len(targets)
    if not 1 <= n <= NUM_CHANNELS:
        raise CmdError("us must have 1..%d values (CH0 upwards)" % NUM_CHANNELS)
    targets = [clamp_us(u) for u in targets]
    ms = max(0, int(req.get("ms") or 0))
    get_pca()
    if ms == 0:
        pose_job = None
        for ch in range(n):
            write_us(ch, targets[ch])
    else:
        # unknown start position: jump straight to the target for that finger
        start = [last_us[ch] if last_us[ch] is not None else targets[ch] for ch in range(n)]
        pose_job = {"t0": time.monotonic(), "dur": ms / 1000, "a": start, "b": targets}
        tick()
    return {"ok": True, "us": targets, "ms": ms}


def cmd_relax(req):
    global pose_job
    pose_job = None
    p = get_pca()
    for ch in range(NUM_CHANNELS):
        p.channels[ch].duty_cycle = 0
        last_us[ch] = None
    return {"ok": True}


def cmd_led(req):
    global count_job
    px = get_pixels()
    count_job = None
    set_brightness(px, req)
    if req.get("rgb") is not None:
        rgb = req["rgb"]
        if len(rgb) > LED_COUNT:
            raise CmdError("rgb has %d pixels, strip has %d" % (len(rgb), LED_COUNT))
        px.fill((0, 0, 0))
        for i, c in enumerate(rgb):
            px[i] = colour(c)
    elif req.get("fill") is not None:
        px.fill(colour(req["fill"]))
    elif req.get("brightness") is None:
        raise CmdError("led needs rgb, fill or brightness")
    px.show()
    return {"ok": True, "count": LED_COUNT, "brightness": px.brightness}


def cmd_countdown(req):
    global count_job
    px = get_pixels()
    beats = int(req["beats"])
    period = int(req["period_ms"])
    if not 1 <= beats <= COUNTDOWN_MAX_BEATS or period < 20:
        raise CmdError("beats must be 1..%d and period_ms >= 20" % COUNTDOWN_MAX_BEATS)
    cols = [colour(c) for c in (req.get("colours") or [(255, 255, 255)])]
    if not cols:
        raise CmdError("colours is empty")
    set_brightness(px, req)
    # beat k fills the strip with colours[k % len] and holds it for period_ms; strip off after the last beat
    count_job = {"t0": time.monotonic(), "period": period / 1000, "beats": beats, "cols": cols, "k": -1}
    tick()
    return {"ok": True, "beats": beats, "period_ms": period, "total_ms": beats * period}


def cmd_relay(req):
    relay_pin.value = bool(req["on"])  # active-high assumed
    return {"ok": True, "on": relay_pin.value}


def cmd_voice(req):
    global voice_job
    if voice_job is not None:
        voice_job["pin"].value = False
        voice_job = None
    play, rec = req.get("play_ms"), req.get("rec_ms")
    if play and rec:
        raise CmdError("give play_ms or rec_ms, not both")
    ms = int(play or rec or 0)
    if ms < 0 or ms > VOICE_MAX_MS:
        raise CmdError("ms must be 0..%d" % VOICE_MAX_MS)
    if ms == 0:  # 0 = stop
        return {"ok": True, "ms": 0}
    pin = playl_pin if play else rec_pin
    pin.value = True
    voice_job = {"pin": pin, "end": time.monotonic() + ms / 1000}
    return {"ok": True, "mode": "play" if play else "rec", "ms": ms}


def cmd_status_led(req):
    """Not in §9.2: the XIAO's onboard RGB LED, used as an operator cue (e.g. gesture training).
    Args: rgb ([r,g,b] or '#rrggbb'; omit or black = off), blink_hz (optional, 0 = steady), brightness."""
    global blink_job
    px = get_board_px()
    set_brightness(px, req)
    col = colour(req.get("rgb") or (0, 0, 0))
    hz = float(req.get("blink_hz") or 0)
    if hz < 0 or hz > 20:
        raise CmdError("blink_hz must be 0..20")
    blink_job = None
    px[0] = col
    if hz and col != (0, 0, 0):
        blink_job = {"t0": time.monotonic(), "hz": hz, "col": col, "on": True}
    return {"ok": True, "rgb": list(col), "blink_hz": hz, "brightness": px.brightness}


HANDLERS = {"servo": cmd_servo, "pose": cmd_pose, "relax": cmd_relax, "led": cmd_led,
            "countdown": cmd_countdown, "relay": cmd_relay, "voice": cmd_voice,
            "status_led": cmd_status_led}


def handle(req):
    cmd = req.get("cmd")
    if cmd in HANDLERS:
        try:
            return HANDLERS[cmd](req)
        except CmdError as e:
            return {"ok": False, "error": str(e)}
        except KeyError as e:
            return {"ok": False, "error": "missing argument: %s" % e}
    if cmd == "ping":
        return {"ok": True, "version": VERSION, "uptime_s": round(time.monotonic() - t0, 1)}
    if cmd == "husky":
        return husky(req)
    if cmd == "uart":
        uart.baudrate = int(req["baud"])
        return {"ok": True, "baud": uart.baudrate}
    return {"ok": False, "error": "not implemented: %s" % cmd}


line = b""
while True:
    if data is None:  # boot.py not applied yet (needs a hard reset)
        time.sleep(1)
        continue
    tick()
    n = data.in_waiting
    if not n:
        continue
    line += data.read(n)
    while b"\n" in line:
        raw, line = line.split(b"\n", 1)
        raw = raw.strip()
        if not raw:
            continue
        rid = None
        try:
            req = json.loads(raw)
            rid = req.get("id")
            reply = handle(req)
        except Exception as e:  # never die on a bad request
            reply = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        reply["id"] = rid
        try:
            out = json.dumps(reply)
        except Exception as e:
            out = json.dumps({"id": rid, "ok": False, "error": "reply encode: %s" % e})
        data.write((out + "\n").encode())
