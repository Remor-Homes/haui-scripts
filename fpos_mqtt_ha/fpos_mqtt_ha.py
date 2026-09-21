import paho.mqtt.client as mqtt
import json
import time
import ssl
import os
import sys
import signal
from dotenv import load_dotenv
import subprocess
import threading
from evdev import InputDevice, ecodes
from percent_to_raw import *
from dotenv import set_key
import socket

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Load environment variables
load_dotenv()
BROKER = os.getenv("BROKER_IP")
PORT = int(os.getenv("BROKER_PORT"))
USERNAME = os.getenv("BROKER_USERNAME")
PASSWORD = os.getenv("BROKER_PASSWORD")
CA_CERT = "/tmp-remorh/ca.crt"
DISPLAY_NAME = os.getenv("DISPLAY_NAME", "10-0045")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# saved_brightness is the level restored on touch / HA "on".
# It is never updated by idle dimming or by turning the panel off.
saved_brightness = int(os.getenv("SAVED_BRIGHTNESS", "100"))
if saved_brightness <= 0:
    saved_brightness = 100
print(f"Loaded SAVED_BRIGHTNESS from .env: {saved_brightness}%")

DIMMING_PERCENT = int(os.getenv("DIMMING_PERCENT", "20"))
TIMEOUT_SECONDS = int(os.getenv("LAST_TIMEOUT_SET", os.getenv("TIMEOUT_SECONDS", "300")))
DIMMING_TO_OFF_SECONDS = int(os.getenv("DIMMING_TO_OFF_SECONDS", "30"))
NEVER_OFF_SECONDS = 600


def save_brightness_to_env(brightness):
    """Persist the user active brightness. Never call this for dim/off levels."""
    global saved_brightness
    saved_brightness = max(1, min(100, int(brightness)))
    set_key(ENV_PATH, "SAVED_BRIGHTNESS", str(saved_brightness))
    print(f"Saved brightness {saved_brightness}% to .env for reboot recovery")


def find_touch_device():
    display_device_name = os.getenv("DISPLAY_DEVICE_NAME", "ft5x06")
    try:
        with open("/proc/bus/input/devices", "r") as f:
            lines = f.readlines()
        event_num = None
        found = False
        for i, line in enumerate(lines):
            if 'Name="' in line and display_device_name in line:
                found = True
            if found and "Handlers=" in line:
                parts = line.split()
                for part in parts:
                    if part.startswith("event"):
                        event_num = part
                        break
                if event_num:
                    break
        if event_num:
            return f"/dev/input/{event_num}"
    except Exception as e:
        print(f"Error finding touch device: {e}")
    return os.getenv("TOUCH_DEVICE", "/dev/input/event5")


def resolve_siren_script():
    candidates = []
    env_path = os.getenv("SIREN_SCRIPT")
    if env_path:
        candidates.append(env_path)
    candidates.extend([
        os.path.join(REPO_ROOT, "play-siren.sh"),
        os.path.join(SCRIPT_DIR, "play-siren.sh"),
        "/home/haui/play-siren.sh",
    ])
    for path in candidates:
        abs_path = os.path.abspath(path)
        if os.path.isfile(abs_path):
            return abs_path
    return os.path.abspath(candidates[0])


def resolve_siren_mp3():
    candidates = []
    env_path = os.getenv("SIREN_MP3")
    if env_path:
        candidates.append(env_path)
    candidates.extend([
        os.path.join(REPO_ROOT, "siren", "siren.mp3"),
        os.path.join(SCRIPT_DIR, "siren", "siren.mp3"),
        "/home/haui/siren/siren.mp3",
    ])
    for path in candidates:
        abs_path = os.path.abspath(path)
        if os.path.isfile(abs_path):
            return abs_path
    return os.path.abspath(candidates[-1])


TOUCH_DEVICE = find_touch_device()
print(f"Found touch device: {TOUCH_DEVICE}")
SIREN_SCRIPT = resolve_siren_script()
SIREN_MP3 = resolve_siren_mp3()
SIREN_MAX_SECONDS = int(os.getenv("SIREN_MAX_SECONDS", "60"))
try:
    siren_volume = int(float(os.getenv("SIREN_VOLUME", "100")))
except (TypeError, ValueError):
    siren_volume = 100
siren_volume = max(0, min(100, siren_volume))
print(f"Siren script: {SIREN_SCRIPT}")
print(f"Siren mp3: {SIREN_MP3}")
print(f"Siren max duration: {SIREN_MAX_SECONDS}s")
print(f"Siren volume: {siren_volume}%")

mqtt_connected = False
HOSTNAME = socket.gethostname()
DEVICE_NAME = HOSTNAME
HA_NAME = HOSTNAME.replace(" ", "-").lower()
print(f"Using DEVICE_NAME='{DEVICE_NAME}' and HA_NAME='{HA_NAME}' for MQTT topics")

undervoltage_count = 0
previous_undervoltage_now = False

HA_DEVICE = {
    "identifiers": [DEVICE_NAME],
    "name": HA_NAME,
    "manufacturer": "Custom",
    "model": "Display controller",
    "sw_version": "1.1",
}
HA_SIREN_DEVICE = {
    "identifiers": [f"{DEVICE_NAME}_siren"],
    "name": f"{HA_NAME} siren",
    "manufacturer": "Custom",
    "model": "HAUI siren",
    "sw_version": "1.1",
    "via_device": DEVICE_NAME,
}

HA_DIMMING_PERCENT_DISCOVERY_PREFIX = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_dimming_percent/config"
HA_DIMMING_PERCENT_STATE_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_dimming_percent/state"
HA_DIMMING_PERCENT_COMMAND_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_dimming_percent/set"

HA_DIMMING_TIMEOUT_DISCOVERY_PREFIX = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_dimming_timeout/config"
HA_DIMMING_TIMEOUT_STATE_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_dimming_timeout/state"
HA_DIMMING_TIMEOUT_COMMAND_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_dimming_timeout/set"

HA_LIGHT_DISCOVERY_PREFIX = f"homeassistant/light/{DEVICE_NAME}/{HA_NAME}/config"
HA_LIGHT_STATE_TOPIC = f"homeassistant/light/{DEVICE_NAME}/{HA_NAME}/state"
HA_LIGHT_BRIGHTNESS_STATE_TOPIC = f"homeassistant/light/{DEVICE_NAME}/{HA_NAME}/brightness"
HA_LIGHT_BRIGHTNESS_COMMAND_TOPIC = f"homeassistant/light/{DEVICE_NAME}/{HA_NAME}/brightness/set"
HA_LIGHT_COMMAND_TOPIC = f"homeassistant/light/{DEVICE_NAME}/{HA_NAME}/set"

HA_TIMEOUT_NUMBER_DISCOVERY_PREFIX = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_timeout/config"
HA_TIMEOUT_NUMBER_STATE_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_timeout/state"
HA_TIMEOUT_NUMBER_COMMAND_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_timeout/set"

HA_UNDERVOLTAGE_DISCOVERY_PREFIX = f"homeassistant/sensor/{DEVICE_NAME}/{HA_NAME}_undervoltage/config"
HA_UNDERVOLTAGE_STATE_TOPIC = f"homeassistant/sensor/{DEVICE_NAME}/{HA_NAME}_undervoltage/state"

HA_BACKLIGHT_LEVEL_DISCOVERY_PREFIX = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_backlight_level/config"
HA_BACKLIGHT_LEVEL_STATE_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_backlight_level/state"
HA_BACKLIGHT_LEVEL_COMMAND_TOPIC = f"homeassistant/number/{DEVICE_NAME}/{HA_NAME}_backlight_level/set"

HA_SIREN_DISCOVERY_PREFIX = f"homeassistant/siren/{DEVICE_NAME}_siren/{HA_NAME}_siren/config"
HA_SIREN_STATE_TOPIC = f"homeassistant/siren/{DEVICE_NAME}_siren/{HA_NAME}_siren/state"
HA_SIREN_COMMAND_TOPIC = f"homeassistant/siren/{DEVICE_NAME}_siren/{HA_NAME}_siren/set"

HA_SIREN_VOLUME_DISCOVERY_PREFIX = f"homeassistant/number/{DEVICE_NAME}_siren/{HA_NAME}_siren_volume/config"
HA_SIREN_VOLUME_STATE_TOPIC = f"homeassistant/number/{DEVICE_NAME}_siren/{HA_NAME}_siren_volume/state"
HA_SIREN_VOLUME_COMMAND_TOPIC = f"homeassistant/number/{DEVICE_NAME}_siren/{HA_NAME}_siren_volume/set"

# Panel states:
#   ON     — user-active, panel at saved_brightness
#   DIMMED — idle, panel at DIMMING_PERCENT (never writes saved_brightness)
#   OFF    — panel at 0; only used when dimming-to-off is not 600
current_state = "ON"
current_brightness = saved_brightness
last_activity = time.time()
dim_start_time = None
state_lock = threading.Lock()
last_panel_reapply = 0

siren_proc = None
siren_lock = threading.Lock()
siren_on = False
siren_stop = threading.Event()
siren_generation = 0


def never_turn_off():
    return DIMMING_TO_OFF_SECONDS >= NEVER_OFF_SECONDS


def brightness_matches(a, b):
    if a is None or b is None:
        return False
    return abs(int(a) - int(b)) <= 1


def get_backlight_brightness_in_percent():
    path = f"/sys/class/backlight/{DISPLAY_NAME}/brightness"
    try:
        with open(path, "r") as f:
            raw_brightness = int(f.read().strip())
            percent_brightness = correlate_percent(raw=raw_brightness)
            if percent_brightness is None:
                return 0
            return percent_brightness
    except Exception as e:
        print(f"Error reading brightness: {e}")
        return 0


def set_backlight_brightness_in_percent(value):
    """Write panel brightness only. Does not touch activity, saved level, or state."""
    path = f"/sys/class/backlight/{DISPLAY_NAME}/brightness"
    value = max(0, min(100, int(value)))
    mapped = correlate_percent(percent=value)
    if mapped is None:
        new_value = 0 if value <= 0 else 3
    else:
        new_value = int(mapped)
    if value <= 0:
        new_value = 0
    cmd = f"echo {new_value} | sudo tee {path} > /dev/null"
    print(f"Set brightness to {value}% (raw: {new_value})")
    try:
        subprocess.call(cmd, shell=True)
    except Exception as e:
        print(f"Error setting brightness: {e}")


def apply_panel(level):
    global current_brightness
    level = max(0, min(100, int(level)))
    set_backlight_brightness_in_percent(level)
    current_brightness = level


def intended_panel_level():
    if current_state == "OFF":
        return 0
    if current_state == "DIMMED":
        return max(1, int(DIMMING_PERCENT))
    return saved_brightness


def get_and_update_undervoltage_count():
    global undervoltage_count, previous_undervoltage_now
    try:
        result = subprocess.check_output(["vcgencmd", "get_throttled"]).decode("utf-8").strip()
        hex_val = result.split("=")[1]
        val = int(hex_val, 16)
        current_undervoltage_now = bool(val & 0x1)

        if current_undervoltage_now and not previous_undervoltage_now:
            undervoltage_count += 1
            print(f"Undervoltage event detected! New count: {undervoltage_count}")

        previous_undervoltage_now = current_undervoltage_now
        return str(undervoltage_count)
    except Exception as e:
        print(f"Error reading undervoltage: {e}")
        return str(undervoltage_count)


def enter_on(reason, new_saved=None, persist=False):
    """Wake to the user active level. Optional new_saved is an explicit user brightness."""
    global current_state, last_activity, dim_start_time, saved_brightness
    if new_saved is not None:
        level = max(1, min(100, int(new_saved)))
        saved_brightness = level
        if persist:
            save_brightness_to_env(level)
    apply_panel(saved_brightness)
    current_state = "ON"
    last_activity = time.time()
    dim_start_time = None
    print(f"Panel ON at {saved_brightness}% ({reason})")


def enter_dimmed(reason):
    """Idle dim. Never copies the dim level into saved_brightness."""
    global current_state, dim_start_time
    dim_percent = max(1, min(100, int(DIMMING_PERCENT)))
    apply_panel(dim_percent)
    current_state = "DIMMED"
    dim_start_time = time.time()
    print(f"Panel DIMMED at {dim_percent}% ({reason}); saved stays {saved_brightness}%")


def enter_off(reason):
    global current_state, dim_start_time
    if never_turn_off():
        enter_dimmed(f"{reason}; never-off so dim instead")
        return
    apply_panel(0)
    current_state = "OFF"
    dim_start_time = None
    print(f"Panel OFF ({reason}); saved stays {saved_brightness}%")


def on_user_touch():
    """
    OFF or DIMMED → saved brightness (active).
    Already ON → keep saved brightness, only reset the idle timer.
    """
    global last_activity
    if current_state == "ON":
        last_activity = time.time()
        if not brightness_matches(current_brightness, saved_brightness):
            apply_panel(saved_brightness)
        print(f"Touch while ON — timer reset, saved {saved_brightness}%")
    else:
        enter_on(f"touch from {current_state}")


def parse_on_off_payload(payload_str):
    try:
        data = json.loads(payload_str)
        if isinstance(data, dict):
            state = data.get("state")
            if state is None:
                return None, data
            return str(state).strip().upper(), data
        return str(data).strip().upper(), {}
    except json.JSONDecodeError:
        return payload_str.strip().upper(), {}


def on_connect(client, userdata, flags, rc, properties=None):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        print(f"Connected with result code {rc}")
        print("Publishing initial retained states...")
        publish_ha_light_discovery()
        publish_ha_light_state()
        publish_siren_state()
        client.subscribe(HA_LIGHT_COMMAND_TOPIC)
        client.subscribe(HA_LIGHT_BRIGHTNESS_COMMAND_TOPIC)
        client.subscribe(HA_TIMEOUT_NUMBER_COMMAND_TOPIC)
        client.subscribe(HA_DIMMING_PERCENT_COMMAND_TOPIC)
        client.subscribe(HA_DIMMING_TIMEOUT_COMMAND_TOPIC)
        client.subscribe(HA_BACKLIGHT_LEVEL_COMMAND_TOPIC)
        client.subscribe(HA_SIREN_COMMAND_TOPIC)
        client.subscribe(HA_SIREN_VOLUME_COMMAND_TOPIC)
    else:
        mqtt_connected = False
        print(f"Connection failed with code {rc}: {mqtt.error_string(rc)}")


def on_disconnect(client, userdata, rc, properties=None):
    print(f"Disconnected with result code {rc}")
    global mqtt_connected
    mqtt_connected = False
    if rc != 0:
        print("Unexpected disconnection. Will attempt reconnect...")


def on_message(client, userdata, msg):
    topic = msg.topic
    payload_str = msg.payload.decode("utf-8").strip()
    try:
        if topic == HA_SIREN_COMMAND_TOPIC:
            handle_siren_command(payload_str, retained=bool(msg.retain))
            return
        if topic == HA_SIREN_VOLUME_COMMAND_TOPIC:
            set_siren_volume(payload_str)
            return
        with state_lock:
            if topic == HA_LIGHT_BRIGHTNESS_COMMAND_TOPIC:
                brightness = int(float(payload_str))
                print(f"Received brightness command: {brightness}%")
                process_command({"brightness": brightness})
            elif topic == HA_LIGHT_COMMAND_TOPIC:
                process_light_payload(payload_str)
            elif topic == HA_TIMEOUT_NUMBER_COMMAND_TOPIC:
                set_timeout_seconds(int(float(payload_str)))
            elif topic == HA_DIMMING_TIMEOUT_COMMAND_TOPIC:
                set_dimming_timeout_seconds(int(float(payload_str)))
            elif topic == HA_DIMMING_PERCENT_COMMAND_TOPIC:
                set_dimming_percent(int(float(payload_str)))
            elif topic == HA_BACKLIGHT_LEVEL_COMMAND_TOPIC:
                set_backlight_level_from_ha(int(float(payload_str)))
        publish_ha_light_state()
    except Exception as e:
        print(f"Error processing message on {topic}: {e} (payload: {payload_str})")


def process_light_payload(payload_str):
    state, data = parse_on_off_payload(payload_str)
    command = {}
    if isinstance(data, dict):
        command.update(data)
    if state is not None:
        command["state"] = state
    process_command(command)


def set_dimming_percent(new_percent):
    global DIMMING_PERCENT
    DIMMING_PERCENT = max(1, min(100, int(new_percent)))
    set_key(ENV_PATH, "DIMMING_PERCENT", str(DIMMING_PERCENT))
    client.publish(HA_DIMMING_PERCENT_STATE_TOPIC, str(DIMMING_PERCENT), retain=True)
    print(f"Dimming percent updated to {DIMMING_PERCENT}%")
    if current_state == "DIMMED":
        apply_panel(DIMMING_PERCENT)


def set_backlight_level_from_ha(level):
    """HA backlight-level number is the user active (saved) brightness."""
    level = max(0, min(100, int(level)))
    if level <= 0:
        enter_off("HA backlight level 0")
        return
    enter_on("HA backlight level", new_saved=level, persist=True)


def set_dimming_timeout_seconds(new_timeout):
    global DIMMING_TO_OFF_SECONDS, dim_start_time
    DIMMING_TO_OFF_SECONDS = max(1, min(NEVER_OFF_SECONDS, int(new_timeout)))
    set_key(ENV_PATH, "DIMMING_TO_OFF_SECONDS", str(DIMMING_TO_OFF_SECONDS))
    client.publish(HA_DIMMING_TIMEOUT_STATE_TOPIC, str(DIMMING_TO_OFF_SECONDS), retain=True)
    print(f"Dimming timeout updated to {DIMMING_TO_OFF_SECONDS}s")
    if never_turn_off():
        if current_state == "OFF":
            enter_dimmed("dim-to-off disabled while panel was OFF")
    elif current_state == "DIMMED":
        dim_start_time = time.time()


def set_timeout_seconds(new_timeout):
    global TIMEOUT_SECONDS
    TIMEOUT_SECONDS = max(10, min(3600, int(new_timeout)))
    set_key(ENV_PATH, "LAST_TIMEOUT_SET", str(TIMEOUT_SECONDS))
    client.publish(HA_TIMEOUT_NUMBER_STATE_TOPIC, str(TIMEOUT_SECONDS), retain=True)
    print(f"Timeout updated to {TIMEOUT_SECONDS}s")


def process_command(command):
    brightness = command.get("brightness")
    state = command.get("state")
    if state is not None:
        state = str(state).strip().upper()

    if brightness is not None:
        level = max(0, min(100, int(brightness)))
        if level <= 0 or state == "OFF":
            enter_off("HA brightness 0 / OFF")
            return
        enter_on("HA brightness", new_saved=level, persist=True)
        return

    if state == "ON":
        enter_on("HA ON")
    elif state == "OFF":
        enter_off("HA OFF")
    else:
        print(f"Invalid command: {command}")


def publish_ha_light_discovery():
    dimming_percent_config = {
        "name": "HAUI Dimming Percent",
        "unique_id": f"{HA_NAME}_dimming_percent",
        "device": HA_DEVICE,
        "state_topic": HA_DIMMING_PERCENT_STATE_TOPIC,
        "command_topic": HA_DIMMING_PERCENT_COMMAND_TOPIC,
        "unit_of_measurement": "%",
        "icon": "mdi:brightness-6",
        "entity_category": "config",
        "min": 1, "max": 100, "step": 1, "mode": "box",
    }
    client.publish(HA_DIMMING_PERCENT_DISCOVERY_PREFIX, json.dumps(dimming_percent_config), retain=True)

    dimming_timeout_config = {
        "name": "HAUI Dimming Timeout (Set to 600 to disable)",
        "unique_id": f"{HA_NAME}_dimming_timeout",
        "device": HA_DEVICE,
        "state_topic": HA_DIMMING_TIMEOUT_STATE_TOPIC,
        "command_topic": HA_DIMMING_TIMEOUT_COMMAND_TOPIC,
        "unit_of_measurement": "s",
        "icon": "mdi:timer-sand",
        "entity_category": "config",
        "min": 1, "max": NEVER_OFF_SECONDS, "step": 1, "mode": "box",
    }
    client.publish(HA_DIMMING_TIMEOUT_DISCOVERY_PREFIX, json.dumps(dimming_timeout_config), retain=True)

    timeout_number_config = {
        "name": "HAUI Backlight Timeout",
        "unique_id": f"{HA_NAME}_backlight_timeout",
        "device": HA_DEVICE,
        "state_topic": HA_TIMEOUT_NUMBER_STATE_TOPIC,
        "command_topic": HA_TIMEOUT_NUMBER_COMMAND_TOPIC,
        "unit_of_measurement": "s",
        "icon": "mdi:timer",
        "entity_category": "config",
        "min": 10, "max": 3600, "step": 1, "mode": "box",
    }
    client.publish(HA_TIMEOUT_NUMBER_DISCOVERY_PREFIX, json.dumps(timeout_number_config), retain=True)

    undervoltage_config = {
        "name": "HAUI Undervoltage",
        "unique_id": f"{HA_NAME}_undervoltage",
        "device": HA_DEVICE,
        "state_topic": HA_UNDERVOLTAGE_STATE_TOPIC,
        "icon": "mdi:flash-alert",
        "entity_category": "diagnostic",
        "device_class": "problem",
        "payload_on": "1",
        "payload_off": "0",
    }
    client.publish(HA_UNDERVOLTAGE_DISCOVERY_PREFIX, json.dumps(undervoltage_config), retain=True)

    light_config = {
        "name": "HAUI Backlight",
        "unique_id": f"{HA_NAME}_backlight",
        "device": HA_DEVICE,
        "state_topic": HA_LIGHT_STATE_TOPIC,
        "command_topic": HA_LIGHT_COMMAND_TOPIC,
        "brightness_state_topic": HA_LIGHT_BRIGHTNESS_STATE_TOPIC,
        "brightness_command_topic": HA_LIGHT_BRIGHTNESS_COMMAND_TOPIC,
        "state_value_template": "{{ value_json.state }}",
        "brightness_scale": 100,
        "icon": "mdi:monitor",
        "supported_color_modes": ["brightness"],
        "color_mode": "brightness",
    }
    client.publish(HA_LIGHT_DISCOVERY_PREFIX, json.dumps(light_config), retain=True)

    backlight_level_config = {
        "name": "HAUI Backlight Level",
        "unique_id": f"{HA_NAME}_backlight_level",
        "device": HA_DEVICE,
        "state_topic": HA_BACKLIGHT_LEVEL_STATE_TOPIC,
        "command_topic": HA_BACKLIGHT_LEVEL_COMMAND_TOPIC,
        "unit_of_measurement": "%",
        "icon": "mdi:brightness-5",
        "min": 0, "max": 100, "step": 1, "mode": "box",
    }
    client.publish(HA_BACKLIGHT_LEVEL_DISCOVERY_PREFIX, json.dumps(backlight_level_config), retain=True)

    siren_config = {
        "name": "HAUI Siren",
        "unique_id": f"{HA_NAME}_siren",
        "device": HA_SIREN_DEVICE,
        "state_topic": HA_SIREN_STATE_TOPIC,
        "command_topic": HA_SIREN_COMMAND_TOPIC,
        "payload_on": "ON",
        "payload_off": "OFF",
        "state_on": "ON",
        "state_off": "OFF",
        "device_class": "siren",
        "icon": "mdi:bullhorn",
        "optimistic": False,
        "qos": 0,
        "retain": False,
    }
    client.publish(HA_SIREN_DISCOVERY_PREFIX, json.dumps(siren_config), retain=True)

    siren_volume_config = {
        "name": "HAUI Siren Volume",
        "unique_id": f"{HA_NAME}_siren_volume",
        "device": HA_SIREN_DEVICE,
        "state_topic": HA_SIREN_VOLUME_STATE_TOPIC,
        "command_topic": HA_SIREN_VOLUME_COMMAND_TOPIC,
        "unit_of_measurement": "%",
        "icon": "mdi:volume-high",
        "min": 0, "max": 100, "step": 1, "mode": "slider",
    }
    client.publish(HA_SIREN_VOLUME_DISCOVERY_PREFIX, json.dumps(siren_volume_config), retain=True)
    print("Published MQTT discovery (display + siren)")


def publish_ha_light_state():
    try:
        # HA light does not have DIMMED. Report ON while the panel is lit
        # (active or idle-dim) and report saved_brightness so HA never
        # writes the dim level back into the user setting.
        ha_state = "OFF" if current_state == "OFF" else "ON"
        state_data = {"state": ha_state, "brightness": saved_brightness}
        client.publish(HA_LIGHT_STATE_TOPIC, json.dumps(state_data), retain=True)
        client.publish(HA_LIGHT_BRIGHTNESS_STATE_TOPIC, str(saved_brightness), retain=True)
        client.publish(HA_TIMEOUT_NUMBER_STATE_TOPIC, str(TIMEOUT_SECONDS), retain=True)
        client.publish(HA_DIMMING_PERCENT_STATE_TOPIC, str(DIMMING_PERCENT), retain=True)
        client.publish(HA_DIMMING_TIMEOUT_STATE_TOPIC, str(DIMMING_TO_OFF_SECONDS), retain=True)

        undervoltage = get_undervoltage_status()
        client.publish(HA_UNDERVOLTAGE_STATE_TOPIC, undervoltage, retain=True)
        client.publish(HA_BACKLIGHT_LEVEL_STATE_TOPIC, str(saved_brightness), retain=True)

        print(
            f"Published states: panel={current_state}@{current_brightness}%, "
            f"saved={saved_brightness}%, timeout={TIMEOUT_SECONDS}s, "
            f"dim %={DIMMING_PERCENT}%, dim to off={DIMMING_TO_OFF_SECONDS}s, "
            f"undervoltage={undervoltage}"
        )
    except Exception as e:
        print(f"Error publishing state: {e}")


def get_undervoltage_status():
    try:
        result = subprocess.check_output(["vcgencmd", "get_throttled"]).decode("utf-8").strip()
        hex_val = result.split("=")[1]
        val = int(hex_val, 16)
        return "1" if (val & 0x1) else "0"
    except Exception:
        return "error"


def publish_siren_state():
    try:
        payload = "ON" if siren_on else "OFF"
        client.publish(HA_SIREN_STATE_TOPIC, payload, retain=True)
        client.publish(HA_SIREN_VOLUME_STATE_TOPIC, str(siren_volume), retain=True)
    except Exception as e:
        print(f"Error publishing siren state: {e}")


def _siren_env():
    env = os.environ.copy()
    uid = os.getuid()
    runtime = f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime
    env["PULSE_RUNTIME_PATH"] = f"{runtime}/pulse"
    env["PULSE_SERVER"] = f"unix:{runtime}/pulse/native"
    return env


def _pactl(*args):
    try:
        subprocess.call(
            ["pactl", *args],
            env=_siren_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"pactl {args} failed: {e}")


def _clamp_siren_volume(value):
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        return siren_volume
    return max(0, min(100, percent))


def _apply_siren_volume():
    """Set the Bluetooth/Pulse sink volume used by the siren speaker."""
    _pactl("set-sink-mute", "@DEFAULT_SINK@", "0")
    _pactl("set-sink-volume", "@DEFAULT_SINK@", f"{siren_volume}%")
    print(f"Applied siren sink volume {siren_volume}%")


def set_siren_volume(value, persist=True):
    global siren_volume
    siren_volume = _clamp_siren_volume(value)
    if persist:
        set_key(ENV_PATH, "SIREN_VOLUME", str(siren_volume))
    _apply_siren_volume()
    try:
        client.publish(HA_SIREN_VOLUME_STATE_TOPIC, str(siren_volume), retain=True)
    except Exception:
        pass
    print(f"Siren volume set to {siren_volume}%")


def _kill_pid_tree(pid):
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _silence_bluetooth_sink():
    """A2DP speakers keep playing after paplay dies unless the sink is cut."""
    _pactl("set-sink-mute", "@DEFAULT_SINK@", "1")
    _pactl("suspend-sink", "@DEFAULT_SINK@", "1")
    time.sleep(0.25)
    _pactl("suspend-sink", "@DEFAULT_SINK@", "0")
    _pactl("set-sink-mute", "@DEFAULT_SINK@", "0")


def _kill_siren_audio():
    """Stop paplay and cut the Bluetooth sink so the speaker goes quiet."""
    global siren_proc
    proc = siren_proc
    siren_proc = None
    if proc is not None:
        _kill_pid_tree(proc.pid)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
    extra_cmds = [
        ["killall", "-9", "paplay"],
        ["killall", "-9", "timeout"],
    ]
    for args in extra_cmds:
        try:
            subprocess.call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    _silence_bluetooth_sink()


def stop_siren():
    global siren_on, siren_generation
    siren_stop.set()
    with siren_lock:
        siren_on = False
        siren_generation += 1
        _kill_siren_audio()
    publish_siren_state()
    print("Siren stopped")


def _spawn_paplay(remaining_s):
    global siren_proc
    paplay = "/usr/bin/paplay" if os.path.isfile("/usr/bin/paplay") else "paplay"
    remaining_s = max(1, int(remaining_s))
    # timeout(1) guarantees the OS kills paplay even if our thread misses it.
    siren_proc = subprocess.Popen(
        ["timeout", "--signal=KILL", str(remaining_s), paplay, SIREN_MP3],
        env=_siren_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    return siren_proc


def _play_siren_worker(generation):
    global siren_proc, siren_on
    if not os.path.isfile(SIREN_MP3):
        print(f"Siren mp3 not found: {SIREN_MP3}")
        with siren_lock:
            if generation == siren_generation:
                siren_on = False
                siren_proc = None
        publish_siren_state()
        return
    _apply_siren_volume()
    publish_siren_state()
    deadline = time.time() + max(1, SIREN_MAX_SECONDS)
    print(f"Siren looping for up to {SIREN_MAX_SECONDS}s (gen={generation})")
    while not siren_stop.is_set() and time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        with siren_lock:
            if generation != siren_generation or not siren_on or siren_stop.is_set():
                break
            try:
                proc = _spawn_paplay(remaining)
            except Exception as e:
                print(f"Failed to start paplay: {e}")
                siren_proc = None
                if generation == siren_generation:
                    siren_on = False
                proc = None
        if proc is None:
            break
        print(f"Siren paplay pid={proc.pid}")
        while proc.poll() is None:
            if siren_stop.wait(0.2) or generation != siren_generation or time.time() >= deadline:
                _kill_pid_tree(proc.pid)
                try:
                    proc.kill()
                except Exception:
                    pass
                break
        with siren_lock:
            if siren_proc is proc:
                siren_proc = None
            if generation != siren_generation or not siren_on or siren_stop.is_set():
                break
        if time.time() >= deadline:
            break
        print("Siren clip ended, still ON — playing again")
    timed_out = time.time() >= deadline and not siren_stop.is_set()
    with siren_lock:
        if generation == siren_generation:
            siren_on = False
            _kill_siren_audio()
            stale = False
        else:
            stale = True
    if not stale:
        publish_siren_state()
    print(f"Siren loop ended (gen={generation}, timed_out={timed_out})")


def start_siren():
    global siren_on, siren_generation
    with siren_lock:
        if siren_on and not siren_stop.is_set():
            print("Siren already on — keep looping")
            return
        siren_on = True
        siren_stop.clear()
        siren_generation += 1
        generation = siren_generation
    threading.Thread(target=_play_siren_worker, args=(generation,), daemon=True).start()


def _payload_means_off(state, payload_str):
    if state in ("OFF", "0", "FALSE", "STOP", "N", "NO"):
        return True
    text = (payload_str or "").strip().upper().replace(" ", "")
    if text in ("OFF", "0", "FALSE", "STOP"):
        return True
    compact = text.replace("'", '"')
    if '"STATE":"OFF"' in compact or '"STATE":FALSE' in compact or '"STATE":0' in compact:
        return True
    return False


def _payload_means_on(state, payload_str):
    if state in ("ON", "1", "TRUE", "PLAY", "Y", "YES"):
        return True
    text = (payload_str or "").strip().upper().replace(" ", "")
    if text in ("ON", "1", "TRUE", "PLAY"):
        return True
    compact = text.replace("'", '"')
    if '"STATE":"ON"' in compact or '"STATE":TRUE' in compact:
        return True
    return False


def _volume_from_siren_payload(data):
    if not isinstance(data, dict):
        return None
    if data.get("volume_level") is not None:
        try:
            return float(data["volume_level"]) * 100.0
        except (TypeError, ValueError):
            return None
    if data.get("volume") is not None:
        return data["volume"]
    return None


def handle_siren_command(payload_str, retained=False):
    state, data = parse_on_off_payload(payload_str)
    print(f"Siren command retained={retained}: {payload_str!r} -> {state}")
    volume = _volume_from_siren_payload(data)
    if volume is not None:
        set_siren_volume(volume)
    if _payload_means_off(state, payload_str):
        stop_siren()
        return
    if _payload_means_on(state, payload_str):
        if retained:
            print("Ignoring retained siren ON (prevents replay after reboot)")
            return
        start_siren()
        return
    if volume is not None:
        return
    print(f"Unknown siren payload: {payload_str!r}")


def touch_monitor():
    try:
        device = InputDevice(TOUCH_DEVICE)
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH and event.value == 1:
                with state_lock:
                    on_user_touch()
                publish_ha_light_state()
    except Exception as e:
        print(f"Touch monitor error: {e}")


def idle_tick(now):
    """Advance ON → DIMMED → OFF. Never writes dim/off into saved_brightness."""
    global last_panel_reapply
    intended = intended_panel_level()
    hw = get_backlight_brightness_in_percent()
    if not brightness_matches(hw, intended) and now - last_panel_reapply > 5:
        print(f"Panel drifted to {hw}%, re-applying {intended}% (state={current_state})")
        apply_panel(intended)
        last_panel_reapply = now

    if current_state == "ON" and now - last_activity > TIMEOUT_SECONDS:
        enter_dimmed("inactivity timeout")
        publish_ha_light_state()
        return

    if (
        current_state == "DIMMED"
        and dim_start_time is not None
        and not never_turn_off()
        and now - dim_start_time > DIMMING_TO_OFF_SECONDS
    ):
        enter_off("dim period over")
        publish_ha_light_state()


client = mqtt.Client(protocol=mqtt.MQTTv311)
if os.path.isfile(CA_CERT):
    client.tls_set(ca_certs=CA_CERT, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    print(f"Using CA certificate at {CA_CERT} for TLS connection.")
else:
    print(f"CA certificate not found at {CA_CERT}. Connecting without TLS (username/password only).")
if USERNAME and PASSWORD:
    client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

# Local backlight works even if MQTT is down. Do not wait for on_connect.
print(f"Restoring panel to saved brightness {saved_brightness}%")
apply_panel(saved_brightness)
current_state = "ON" if saved_brightness > 0 else "OFF"
last_activity = time.time()

try:
    client.connect(BROKER, PORT)
except Exception as e:
    print(f"Connection error: {e}")

client.loop_start()

time.sleep(2)
print("Initial state publish (safety)...")
publish_ha_light_state()
publish_siren_state()

threading.Thread(target=touch_monitor, daemon=True).start()

last_republish = 0
last_mqtt_attempt = 0
MQTT_RECONNECT_INTERVAL = 30
last_backlight_publish = 0
last_undervoltage_check = 0
last_undervoltage_status = "0"

try:
    while True:
        now = time.time()

        if not mqtt_connected and now - last_mqtt_attempt > MQTT_RECONNECT_INTERVAL:
            try:
                client.reconnect()
                print("MQTT reconnect attempt...")
            except Exception as e:
                print(f"Reconnect failed: {e}")
            last_mqtt_attempt = now

        if mqtt_connected and now - last_republish > 600:
            publish_ha_light_discovery()
            last_republish = now

        if mqtt_connected and now - last_backlight_publish > 30:
            client.publish(HA_BACKLIGHT_LEVEL_STATE_TOPIC, str(saved_brightness), retain=True)
            last_backlight_publish = now

        with state_lock:
            idle_tick(now)

        time.sleep(1)

except KeyboardInterrupt:
    print("Stopping...")
finally:
    stop_siren()
    client.loop_stop()
    client.disconnect()
