#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
logger = logging.getLogger("omalogimouse")

ALLOWED_SETTINGS = frozenset(
    {
        "dpi",
        "hires-smooth-resolution",
        "hires-smooth-invert",
        "scroll-ratchet",
        "smart-shift",
        "scroll-ratchet-torque",
        "haptic-level",
        "change-host",
        "thumb-scroll-invert",
    }
)

WANTED = (
    "dpi",
    "hires-smooth-resolution",
    "hires-smooth-invert",
    "scroll-ratchet",
    "smart-shift",
    "scroll-ratchet-torque",
    "haptic-level",
    "change-host",
    "thumb-scroll-invert",
)


def fail(message: str, *, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": message}), flush=True)
    raise SystemExit(code)


def emit(payload: dict[str, Any]) -> None:
    global _stdout_sent
    line = json.dumps(payload, default=str)
    encoded = (line + "\n").encode("utf-8")
    if _stdout_sent + len(encoded) > LISTEN_MAX_STDOUT:
        raise SystemExit(0)
    _stdout_sent += len(encoded)
    try:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        raise SystemExit(0) from None


MAX_BINDS_BYTES = 64 * 1024
MAX_LUA_BYTES = 1024 * 1024
MAX_PROC_BYTES = 64 * 1024
PROC_TIMEOUT_SEC = 5
LISTEN_MAX_SEC = 6 * 3600
LISTEN_MAX_STDOUT = 256 * 1024
ALLOWED_BIN_ROOTS = (Path("/usr/bin"), Path("/bin"))
_stdout_sent = 0


def _closed_env() -> dict[str, str]:
    keep = (
        "HOME",
        "USER",
        "LOGNAME",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "WAYLAND_DISPLAY",
        "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP",
        "DBUS_SESSION_BUS_ADDRESS",
        "LANG",
        "LC_ALL",
    )
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env["PATH"] = "/usr/bin:/bin"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def verified_bin(name: str) -> Path:
    path = Path("/usr/bin") / name
    try:
        st = os.lstat(path)
    except OSError as exc:
        fail(f"missing {path}: {exc}")
    resolved = path
    if stat.S_ISLNK(st.st_mode):
        resolved = Path(os.path.realpath(path))
        if resolved.parent not in ALLOWED_BIN_ROOTS:
            fail(f"{path} symlink escapes /usr/bin")
        st = os.stat(resolved)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or not os.access(resolved, os.X_OK):
        fail(f"{resolved} is not a root-owned executable")
    return resolved


def verified_user_bin(name: str) -> Path | None:
    path = Path.home() / ".local" / "bin" / name
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or not stat.S_ISREG(st.st_mode):
            return None
        if not os.access(path, os.X_OK):
            return None
        return path
    finally:
        os.close(fd)


HYPRCTL = verified_bin("hyprctl")
OMARCHY = verified_bin("omarchy")
OMARCHY_SHELL = verified_bin("omarchy-shell")


def run_tool(argv: list[str], *, timeout: int = PROC_TIMEOUT_SEC) -> str:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            timeout=timeout,
            env=_closed_env(),
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        if exc.stdout:
            del exc.stdout
        return ""
    out = completed.stdout or b""
    if len(out) > MAX_PROC_BYTES:
        out = out[:MAX_PROC_BYTES]
    return out.decode("utf-8", errors="replace")


def _refuse_symlink(path: Path) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise OSError(f"refusing symlink {path}")


def _assert_safe_ancestors(path: Path) -> None:
    uid = os.getuid()
    home = Path.home()
    current = Path(os.path.abspath(path)).parent
    for ancestor in [current, *current.parents]:
        st = os.lstat(ancestor)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"unsafe ancestor {ancestor}")
        if ancestor in (Path("/"), Path("/home"), Path("/usr"), Path("/etc")):
            continue
        if ancestor == home:
            if st.st_uid != uid:
                raise OSError("HOME is not owned by the user")
            return
        if st.st_uid not in (0, uid):
            raise OSError(f"unowned ancestor {ancestor}")


def read_bounded(path: Path, max_bytes: int) -> bytes:
    _assert_safe_ancestors(path)
    _refuse_symlink(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if st.st_uid not in (0, os.getuid()) or not stat.S_ISREG(st.st_mode):
            raise OSError(f"refusing {path}")
        if st.st_size > max_bytes:
            raise OSError(f"{path} exceeds {max_bytes} bytes")
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError(f"{path} exceeds {max_bytes} bytes")
        return data
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    if len(data) > MAX_LUA_BYTES:
        raise OSError("write too large")
    path = Path(os.path.abspath(path))
    _assert_safe_ancestors(path)
    _refuse_symlink(path)
    parent = path.parent
    dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    tmp_name = f".{path.name}.{os.getpid()}.tmp"
    fd = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(tmp_name, flags, mode, dir_fd=dir_fd)
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)


# Wireless PID / Bluetooth ID -> marketing name. Name matching still wins for
# newer SKUs that Solaar has not catalogued yet (3S, 4, for Mac, for Business).
FAMILY_IDS: dict[str, str] = {
    "4041": "MX Master",
    "B012": "MX Master",
    "4069": "MX Master 2S",
    "B019": "MX Master 2S",
    "4082": "MX Master 3",
    "B023": "MX Master 3",
    "B034": "MX Master 3S",
    "B035": "MX Master 3S",
    "B042": "MX Master 4",
}


def iter_devices() -> list[Any]:
    from logitech_receiver import base
    from logitech_receiver import device as device_mod
    from logitech_receiver import receiver as receiver_mod

    found: list[Any] = []
    for info in base.receivers_and_devices():
        try:
            if info.isDevice:
                handle = device_mod.create_device(base, info)
            else:
                handle = receiver_mod.create_receiver(base, info)
        except Exception:
            logger.exception("opening %s", getattr(info, "path", "?"))
            continue
        if handle is None:
            continue
        if getattr(handle, "isDevice", False):
            found.append(handle)
            continue
        try:
            for child in handle:
                if child is not None:
                    found.append(child)
        except Exception:
            logger.exception("listing paired devices")
    return found


def _ids_of(dev: Any) -> set[str]:
    ids: set[str] = set()
    for attr in ("wpid", "unitId", "serial", "codename", "name", "product_id"):
        raw = getattr(dev, attr, None)
        if raw is None:
            continue
        text = str(raw).upper().replace("0X", "")
        ids.add(text)
        if len(text) >= 4:
            ids.add(text[-4:])
    model = getattr(dev, "modelId", None) or getattr(dev, "model_id", None)
    if model:
        text = str(model).upper()
        ids.add(text)
        ids.add(text[:4])
    return ids


def _display_name(dev: Any) -> str:
    return str(getattr(dev, "name", None) or getattr(dev, "codename", None) or "MX Master")


def family_label(dev: Any) -> str | None:
    for ident in _ids_of(dev):
        if ident in FAMILY_IDS:
            return FAMILY_IDS[ident]
    blob = f"{getattr(dev, 'name', '')} {getattr(dev, 'codename', '')}".lower()
    blob = blob.replace("-", " ")
    if "mx master" not in blob:
        return None
    if "4" in blob:
        return "MX Master 4"
    if "3s" in blob:
        return "MX Master 3S"
    if "3" in blob:
        return "MX Master 3"
    if "2s" in blob:
        return "MX Master 2S"
    if "2" in blob:
        return "MX Master 2S"
    return _display_name(dev)


def generation_of(label: str) -> str:
    n = label.lower()
    if "4" in n:
        return "4"
    if "3s" in n:
        return "3s"
    if "3" in n:
        return "3"
    if "2s" in n or n.endswith("2"):
        return "2s"
    return "1"


def device_serial(dev: Any) -> str:
    return str(getattr(dev, "serial", None) or getattr(dev, "unitId", None) or "")


def family_members(devices: list[Any]) -> list[Any]:
    return [dev for dev in devices if family_label(dev)]


def pick_mouse(devices: list[Any], serial: str | None = None) -> Any | None:
    members = family_members(devices)
    if not members:
        return None
    if serial:
        wanted = serial.lower()
        for dev in members:
            if device_serial(dev).lower() == wanted:
                return dev

    ranked: list[tuple[int, int, str, Any]] = []
    for dev in members:
        label = family_label(dev) or ""
        online = 1 if dev.ping() else 0
        gen = generation_of(label)
        gen_rank = {"4": 4, "3s": 3, "3": 2, "2s": 1, "1": 0}.get(gen, 0)
        ranked.append((online, gen_rank, label, dev))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return ranked[0][3]


def setting_payload(setting: Any) -> dict[str, Any]:
    raw = setting.read(cached=True)
    payload: dict[str, Any] = {
        "name": setting.name,
        "label": getattr(setting, "label", setting.name),
        "value": None if raw is None else setting.val_to_string(raw),
        "raw": raw if isinstance(raw, (bool, int, float, str)) else str(raw) if raw is not None else None,
    }
    kind = str(getattr(setting, "kind", ""))
    if "CHOICE" in kind or (hasattr(setting, "choices") and setting.choices):
        choices = [str(v) for v in setting.choices]
        if len(choices) <= 16:
            payload["choices"] = choices
        ints = []
        for item in choices:
            try:
                ints.append(int(item.split(":")[0]))
            except ValueError:
                ints = []
                break
        if ints:
            payload["min"] = min(ints)
            payload["max"] = max(ints)
            if len(ints) > 1:
                payload["step"] = ints[1] - ints[0]
    if hasattr(setting, "range") and setting.range:
        payload["min"] = int(setting.range[0])
        payload["max"] = int(setting.range[1])
    return payload


def snapshot(dev: Any) -> dict[str, Any]:
    online = bool(dev.ping())
    battery_level: int | None = None
    battery_status = ""
    if online:
        try:
            battery = dev.battery()
        except Exception:
            battery = None
        if battery is not None:
            level = getattr(battery, "level", None)
            if isinstance(level, int):
                battery_level = level
            elif level is not None:
                try:
                    battery_level = int(level)
                except (TypeError, ValueError):
                    battery_level = None
            battery_status = str(getattr(battery, "status", "") or "")
            if battery_status.startswith("BatteryStatus."):
                battery_status = battery_status.split(".", 1)[1].lower()
            else:
                battery_status = battery_status.lower()

    settings: dict[str, Any] = {}
    if online:
        try:
            for setting in dev.settings:
                if setting.name in WANTED:
                    try:
                        settings[setting.name] = setting_payload(setting)
                    except Exception as exc:
                        settings[setting.name] = {"name": setting.name, "error": str(exc)}
        except Exception as exc:
            settings["_error"] = str(exc)

    label = family_label(dev) or _display_name(dev)
    payload = {
        "ok": True,
        "connected": online,
        "name": _display_name(dev),
        "family": label,
        "generation": generation_of(label),
        "codename": getattr(dev, "codename", None) or "",
        "kind": str(getattr(dev, "kind", "")),
        "serial": device_serial(dev),
        "wpid": getattr(dev, "wpid", None) or "",
        "protocol": getattr(dev, "protocol", None),
        "battery": battery_level,
        "batteryStatus": battery_status,
        "hasDpi": "dpi" in settings,
        "hasHires": "hires-smooth-resolution" in settings,
        "hasRatchet": "scroll-ratchet" in settings,
        "hasSmartShift": "smart-shift" in settings,
        "hasHaptic": "haptic-level" in settings,
        "hasButtons": False,
        "buttons": [],
        "actionCatalog": ACTION_CATALOG,
        "settings": settings,
    }
    if online:
        buttons = buttons_payload(dev)
        payload["buttons"] = buttons
        payload["hasButtons"] = len(buttons) > 0
    payload["acceleration"] = accel_enabled()
    return payload


def summarize(dev: Any) -> dict[str, Any]:
    label = family_label(dev) or _display_name(dev)
    online = False
    try:
        online = bool(dev.ping())
    except Exception:
        online = False
    return {
        "name": _display_name(dev),
        "family": label,
        "generation": generation_of(label),
        "serial": device_serial(dev),
        "wpid": str(getattr(dev, "wpid", None) or ""),
        "online": online,
    }


def with_roster(payload: dict[str, Any], devices: list[Any]) -> dict[str, Any]:
    payload["devices"] = [summarize(dev) for dev in family_members(devices)]
    return payload


BINDS_PATH = Path.home() / ".local/state/omarchy/omalogimouse-binds.json"
PROTECTED_BUTTONS = frozenset({"Left Button", "Right Button"})

HW_ACTIONS: dict[str, str] = {
    "mouse-back": "Mouse Back Button",
    "mouse-forward": "Mouse Forward Button",
    "mouse-middle": "Mouse Middle Button",
    "gesture": "Gesture Button",
    "smart-shift": "Smart Shift",
    "haptic-hw": "unknown:0109",
}
HW_BY_LABEL = {label.lower(): action for action, label in HW_ACTIONS.items()}
HW_BY_LABEL["haptic"] = "haptic-hw"

def software_actions() -> dict[str, list[str]]:
    actions = {
        "workspace-next": [str(HYPRCTL), "dispatch", "workspace", "e+1"],
        "workspace-prev": [str(HYPRCTL), "dispatch", "workspace", "e-1"],
        "menu": [str(OMARCHY), "menu", "summon", "root"],
        "expose": [str(OMARCHY_SHELL), "expose", "toggle"],
        "volume-up": [str(OMARCHY), "audio", "output", "volume", "raise"],
        "volume-down": [str(OMARCHY), "audio", "output", "volume", "lower"],
        "mute": [str(OMARCHY), "audio", "output", "volume", "mute-toggle"],
        "play-pause": [str(OMARCHY_SHELL), "media", "playPause"],
    }
    shot = verified_user_bin("screenshot-region-clipboard")
    if shot is not None:
        actions["screenshot"] = [str(shot)]
    return actions


SOFTWARE_ACTIONS: dict[str, list[str]] = software_actions()

ACTION_CATALOG: list[dict[str, str]] = [
    {"value": "default", "label": "Default for this button", "kind": "hardware"},
    {"value": "mouse-back", "label": "Back", "kind": "hardware"},
    {"value": "mouse-forward", "label": "Forward", "kind": "hardware"},
    {"value": "mouse-middle", "label": "Middle click", "kind": "hardware"},
    {"value": "gesture", "label": "Gesture", "kind": "hardware"},
    {"value": "smart-shift", "label": "SmartShift (ratchet)", "kind": "hardware"},
    {"value": "workspace-next", "label": "Next workspace", "kind": "software"},
    {"value": "workspace-prev", "label": "Previous workspace", "kind": "software"},
    {"value": "menu", "label": "Omarchy menu", "kind": "software"},
    {"value": "expose", "label": "Exposé", "kind": "software"},
    {"value": "volume-up", "label": "Volume up", "kind": "software"},
    {"value": "volume-down", "label": "Volume down", "kind": "software"},
    {"value": "mute", "label": "Mute", "kind": "software"},
    {"value": "play-pause", "label": "Play / pause", "kind": "software"},
    {"value": "screenshot", "label": "Screenshot region", "kind": "software"},
]


def binds_file() -> dict[str, Any]:
    try:
        raw = read_bounded(BINDS_PATH, MAX_BINDS_BYTES)
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_binds(data: dict[str, Any]) -> None:
    BINDS_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_BINDS_BYTES:
        raise OSError("binds file too large")
    atomic_write(BINDS_PATH, payload)


def find_setting(dev: Any, name: str) -> Any | None:
    try:
        for setting in dev.settings:
            if setting.name == name:
                return setting
    except Exception:
        pass
    from logitech_receiver import settings_templates

    return settings_templates.check_feature_setting(dev, name)


def map_choice_key(setting: Any, name: str) -> Any | None:
    if setting is None or not getattr(setting, "choices", None):
        return None
    for key in setting.choices.keys():
        if str(key) == name:
            return key
    return None


def infer_action(button: str, hardware: str, divert: str, saved: str | None, factory: str) -> str:
    if saved:
        return saved
    if divert.lower() == "diverted":
        return "default"
    if factory and hardware == factory:
        return "default"
    label = hardware.lower()
    if label in HW_BY_LABEL:
        return HW_BY_LABEL[label]
    return "default"


def buttons_payload(dev: Any) -> list[dict[str, Any]]:
    remap = find_setting(dev, "reprogrammable-keys")
    divert = find_setting(dev, "divert-keys")
    if remap is None:
        return []
    try:
        remap_raw = remap.read(cached=True) or {}
    except Exception:
        remap_raw = {}
    try:
        divert_raw = divert.read(cached=True) or {} if divert is not None else {}
    except Exception:
        divert_raw = {}
    saved = binds_file().get(device_serial(dev), {})
    rows: list[dict[str, Any]] = []

    def choice_label(choices: list[Any], raw: Any) -> str:
        if raw is None:
            return str(choices[0]) if choices else ""
        for choice in choices:
            try:
                if int(choice) == int(raw):
                    return str(choice)
            except (TypeError, ValueError):
                pass
            if str(choice) == str(raw):
                return str(choice)
        return str(raw)

    for key in remap.choices.keys():
        name = str(key)
        if name in PROTECTED_BUTTONS:
            continue
        cid = int(key)
        hw_objs = list(remap.choices[key])
        hw_choices = [str(v) for v in hw_objs]
        mapped = remap_raw.get(cid, remap_raw.get(str(cid)))
        hardware = choice_label(hw_objs, mapped)
        divert_val = "Regular"
        divert_choices = ["Regular"]
        if divert is not None and key in divert.choices:
            div_objs = list(divert.choices[key])
            divert_choices = [str(v) for v in div_objs]
            raw_div = divert_raw.get(cid, divert_raw.get(str(cid)))
            divert_val = choice_label(div_objs, raw_div) if raw_div is not None else "Regular"
        factory = hw_choices[0] if hw_choices else ""
        action = infer_action(name, hardware, divert_val, saved.get(name), factory)
        rows.append(
            {
                "id": cid,
                "name": name,
                "hardware": hardware,
                "hardwareChoices": hw_choices,
                "divert": divert_val,
                "divertChoices": divert_choices,
                "action": action,
            }
        )
    return rows


def write_map_choice(setting: Any, button: str, value: str) -> None:
    from solaar.cli.config import select_choice

    key = map_choice_key(setting, button)
    if key is None:
        raise ValueError(f"button '{button}' is not remappable")
    parsed = select_choice(value, setting.choices[key], setting, str(key))
    result = setting.write_key_value(int(key), parsed, save=True)
    if result is None:
        raise ValueError(f"failed to set {setting.name} {button} to {value}")


def cmd_bind(button: str, action: str, serial: str | None) -> None:
    if button in PROTECTED_BUTTONS:
        fail("left and right buttons cannot be remapped")
    known = {row["value"] for row in ACTION_CATALOG}
    if action not in known:
        fail(f"unknown action '{action}'")
    devices = iter_devices()
    mouse = pick_mouse(devices, serial)
    if mouse is None or not mouse.ping():
        fail("mouse is offline")
    remap = find_setting(mouse, "reprogrammable-keys")
    divert = find_setting(mouse, "divert-keys")
    if remap is None:
        fail("this mouse has no remappable buttons")
    serial_id = device_serial(mouse)
    stored = binds_file()
    per_device = stored.setdefault(serial_id, {})
    if action == "default":
        key = map_choice_key(remap, button)
        factory = str(list(remap.choices[key])[0]) if key is not None else ""
        if factory:
            write_map_choice(remap, button, factory)
        if divert is not None and map_choice_key(divert, button) is not None:
            write_map_choice(divert, button, "Regular")
        per_device.pop(button, None)
    elif action in HW_ACTIONS:
        write_map_choice(remap, button, HW_ACTIONS[action])
        if divert is not None and map_choice_key(divert, button) is not None:
            write_map_choice(divert, button, "Regular")
        per_device[button] = action
    else:
        if divert is None or map_choice_key(divert, button) is None:
            fail(f"button '{button}' cannot be diverted for software actions")
        write_map_choice(divert, button, "Diverted")
        per_device[button] = action
    if per_device:
        stored[serial_id] = per_device
    elif serial_id in stored:
        del stored[serial_id]
    save_binds(stored)
    payload = with_roster(snapshot(mouse), devices)
    payload["updated"] = button
    emit(payload)


def fire_action(action: str) -> None:
    cmd = SOFTWARE_ACTIONS.get(action)
    if not cmd:
        return
    try:
        subprocess.Popen(
            cmd,
            env=_closed_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.error("action %s failed: %s", action, exc)


def cmd_listen(serial: str | None) -> None:
    from logitech_receiver import base
    from logitech_receiver.hidpp20_constants import SupportedFeature

    try:
        os.setpgrp()
    except OSError:
        pass
    deadline = time.monotonic() + LISTEN_MAX_SEC
    devices = iter_devices()
    mouse = pick_mouse(devices, serial)
    if mouse is None or not mouse.ping():
        fail("mouse is offline")
    remap = find_setting(mouse, "reprogrammable-keys")
    divert = find_setting(mouse, "divert-keys")
    serial_id = device_serial(mouse)
    stored = binds_file().get(serial_id, {})
    if divert is not None:
        for button, action in stored.items():
            if action in SOFTWARE_ACTIONS and map_choice_key(divert, button) is not None:
                try:
                    write_map_choice(divert, button, "Diverted")
                except Exception:
                    logger.exception("could not divert %s", button)
    cid_to_button: dict[int, str] = {}
    if remap is not None:
        for key in remap.choices.keys():
            cid_to_button[int(key)] = str(key)
    receiver = getattr(mouse, "receiver", None) or mouse
    handle = getattr(receiver, "handle", None)
    if handle is None:
        fail("no HID++ handle for listener")
    feat_index = None
    try:
        feat_index = int(mouse.features[SupportedFeature.REPROG_CONTROLS_V4])
    except Exception:
        feat_index = None
    emit({"ok": True, "listening": True, "serial": serial_id})
    pressed: set[int] = set()
    events = 0
    while time.monotonic() < deadline and _stdout_sent < LISTEN_MAX_STDOUT:
        try:
            packet = base.read(handle, 1.0)
        except Exception:
            logger.exception("listener read failed")
            break
        if not packet:
            continue
        report_id, _devnumber, data = packet
        note = base.make_notification(report_id, _devnumber, data)
        if note is None:
            continue
        if feat_index is not None and note.sub_id != feat_index:
            continue
        if note.address != 0x00:
            continue
        if len(note.data) < 8:
            continue
        cids = {cid for cid in struct.unpack("!HHHH", note.data[:8]) if cid}
        for cid in cids - pressed:
            button = cid_to_button.get(cid, str(cid))
            action = stored.get(button)
            if action in SOFTWARE_ACTIONS:
                fire_action(action)
                events += 1
                emit({"event": "press", "button": button, "action": action})
                if events >= 10_000:
                    raise SystemExit(0)
        pressed = cids
    try:
        os.killpg(os.getpgrp(), 15)
    except OSError:
        pass


def coerce_value(setting: Any, text: str) -> Any:
    from solaar.cli.config import select_choice, select_range, select_toggle
    from logitech_receiver import settings as setting_kinds

    kind = setting.kind
    if kind == setting_kinds.Kind.TOGGLE:
        return select_toggle(text, setting)
    if kind == setting_kinds.Kind.CHOICE:
        return select_choice(text, setting.choices, setting, None)
    if kind == setting_kinds.Kind.RANGE:
        return select_range(text, setting)
    raise ValueError(f"unsupported setting kind {kind}")


INPUT_LUA = Path.home() / ".config/hypr/input.lua"
ACCEL_BEGIN = "-- omalogimouse:begin"
ACCEL_END = "-- omalogimouse:end"


def hypr_mouse_names() -> list[str]:
    try:
        raw = run_tool([str(HYPRCTL), "devices", "-j"])
        data = json.loads(raw) if raw else {}
    except (OSError, json.JSONDecodeError):
        return []
    names: list[str] = []
    for mouse in data.get("mice") or []:
        name = str(mouse.get("name") or "")
        lower = name.lower()
        if not name or not all(ch.isalnum() or ch in "-_" for ch in name):
            continue
        if "consumer-control" in lower or "keyboard" in lower:
            continue
        if "mx-master" in lower or "mx_master" in lower or ("logi" in lower and "mouse" in lower):
            names.append(name)
    return names


def accel_enabled() -> bool:
    try:
        text = read_bounded(INPUT_LUA, MAX_LUA_BYTES).decode("utf-8")
    except OSError:
        return True
    start = text.find(ACCEL_BEGIN)
    end = text.find(ACCEL_END)
    if start < 0 or end < 0 or end <= start:
        return True
    block = text[start:end]
    return 'accel_profile = "flat"' not in block


def write_accel_block(enabled: bool, names: list[str]) -> None:
    original = read_bounded(INPUT_LUA, MAX_LUA_BYTES)
    profile = "adaptive" if enabled else "flat"
    lines = [
        ACCEL_BEGIN,
        "-- Generated by Omalogimouse. On = adaptive, off = flat.",
    ]
    for name in names:
        lines.append(f'hl.device({{ name = "{name}", accel_profile = "{profile}" }})')
    lines.append(ACCEL_END)
    block = "\n".join(lines) + "\n"
    text = original.decode("utf-8")
    start = text.find(ACCEL_BEGIN)
    end = text.find(ACCEL_END)
    if start >= 0 and end > start:
        end = end + len(ACCEL_END)
        while end < len(text) and text[end] == "\n":
            end += 1
        text = text[:start] + block + text[end:]
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    backup = INPUT_LUA.with_name(INPUT_LUA.name + ".bak.omalogimouse")
    try:
        os.lstat(backup)
    except FileNotFoundError:
        atomic_write(backup, original)
    atomic_write(INPUT_LUA, text.encode("utf-8"), mode=0o644)
    errors = reload_hypr()
    if errors:
        atomic_write(INPUT_LUA, original, mode=0o644)
        reload_hypr()
        raise OSError(errors)


def reload_hypr() -> str:
    run_tool([str(HYPRCTL), "reload"], timeout=8)
    return run_tool([str(HYPRCTL), "configerrors"], timeout=5).strip()


def cmd_accel(value: str, serial: str | None) -> None:
    enabled = value.lower() in {"on", "true", "1", "adaptive", "yes"}
    names = hypr_mouse_names()
    if not names:
        fail("no Logitech mouse in Hyprland devices")
    try:
        write_accel_block(enabled, names)
    except OSError as exc:
        fail(str(exc))
    cmd_status(serial)


def cmd_status(serial: str | None) -> None:
    devices = iter_devices()
    mouse = pick_mouse(devices, serial)
    if mouse is None:
        emit(
            {
                "ok": True,
                "connected": False,
                "devices": [],
                "acceleration": accel_enabled(),
                "error": "No Logitech MX Master found",
            }
        )
        return
    emit(with_roster(snapshot(mouse), devices))


def cmd_set(name: str, value: str, serial: str | None) -> None:
    if name not in ALLOWED_SETTINGS:
        fail(f"setting '{name}' is not allowed")
    from logitech_receiver import settings_templates

    devices = iter_devices()
    mouse = pick_mouse(devices, serial)
    if mouse is None or not mouse.ping():
        fail("mouse is offline")
    setting = settings_templates.check_feature_setting(mouse, name)
    if setting is None:
        for known in mouse.settings:
            if known.name == name:
                setting = known
                break
    if setting is None:
        fail(f"no setting '{name}' on {_display_name(mouse)}")
    parsed = coerce_value(setting, value)
    result = setting.write(parsed, save=True)
    if result is None:
        fail(f"failed to set {name} to {value}")
    payload = with_roster(snapshot(mouse), devices)
    payload["updated"] = name
    emit(payload)


def cmd_play(waveform: str, serial: str | None) -> None:
    from logitech_receiver import settings_templates

    devices = iter_devices()
    mouse = pick_mouse(devices, serial)
    if mouse is None or not mouse.ping():
        fail("mouse is offline")
    setting = settings_templates.check_feature_setting(mouse, "haptic-play")
    if setting is None:
        fail("this mouse has no haptic-play setting")
    parsed = coerce_value(setting, waveform)
    result = setting.write(parsed, save=False)
    if result is None:
        fail(f"failed to play haptic '{waveform}'")
    payload = with_roster(snapshot(mouse), devices)
    payload["played"] = str(parsed)
    emit(payload)


def parse_args(argv: list[str]) -> tuple[list[str], str | None]:
    serial: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--device" and i + 1 < len(argv):
            serial = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return rest, serial


def main(argv: list[str]) -> None:
    args, serial = parse_args(argv[1:])
    if not args or args[0] in {"-h", "--help"}:
        fail(
            "usage: mx.py [--device SERIAL] status | set <setting> <value> | "
            "play <waveform> | bind <button> <action> | accel <on|off> | listen",
            code=2,
        )
    action = args[0]
    try:
        if action == "status":
            cmd_status(serial)
        elif action == "set":
            if len(args) < 3:
                fail("usage: mx.py set <setting> <value>")
            cmd_set(args[1], args[2], serial)
        elif action == "play":
            if len(args) < 2:
                fail("usage: mx.py play <waveform>")
            cmd_play(" ".join(args[1:]), serial)
        elif action == "bind":
            if len(args) < 3:
                fail("usage: mx.py bind <button> <action>")
            cmd_bind(args[1], args[2], serial)
        elif action == "listen":
            cmd_listen(serial)
        elif action == "accel":
            if len(args) < 2:
                fail("usage: mx.py accel <on|off>")
            cmd_accel(args[1], serial)
        else:
            fail(f"unknown action '{action}'")
    except SystemExit:
        raise
    except Exception as occ:
        logger.exception("mx helper failed")
        fail(str(occ))


if __name__ == "__main__":
    main(sys.argv)
