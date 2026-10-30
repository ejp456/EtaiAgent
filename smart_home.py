import os
import json
import httpx
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from home_assistant import HomeAssistantClient

CONFIG_FILE = "smart_devices.json"
HUE_CONFIG_FILE = "hue_config.json"

NAMED_RGB_COLORS: Dict[str, List[int]] = {
    "red": [255, 40, 40],
    "green": [50, 220, 80],
    "blue": [40, 100, 255],
    "yellow": [255, 220, 50],
    "orange": [255, 140, 30],
    "purple": [170, 60, 255],
    "pink": [255, 105, 180],
    "cyan": [40, 220, 255],
    "white": [255, 255, 255],
    "candlelight": [255, 147, 41],
    "candelight": [255, 147, 41],
    "amber": [255, 140, 30],
}

COLOR_TEMPS: Dict[str, int] = {
    "candlelight": 500,
    "candelight": 500,
    "candle": 500,
    "warmest": 500,
    "warm": 454,        # ~2200K Relax
    "warm_white": 370,  # ~2700K Soft White
    "soft_white": 370,
    "neutral": 300,     # ~3300K
    "reading": 346,     # ~2900K Reading
    "cool": 233,        # ~4300K Concentrate
    "cool_white": 233,
    "daylight": 153,    # ~6500K Energize
    "cold": 153,
    "cold_white": 153,
}

NAMED_COLORS: Dict[str, List[float]] = {
    "red": [0.675, 0.322],
    "green": [0.409, 0.518],
    "blue": [0.167, 0.04],
    "yellow": [0.432, 0.501],
    "orange": [0.556, 0.409],
    "purple": [0.272, 0.109],
    "pink": [0.415, 0.235],
    "cyan": [0.171, 0.354],
    "white": [0.323, 0.329],
    "candlelight": [0.5267, 0.4133],
    "candelight": [0.5267, 0.4133],
    "amber": [0.556, 0.409],
}

class HueClient:
    """Direct local controller for Philips Hue Bridge."""

    def __init__(self, bridge_ip: Optional[str] = None, api_key: Optional[str] = None):
        self.bridge_ip = bridge_ip or os.environ.get("HUE_BRIDGE_IP") or "192.168.86.46"
        self.api_key = api_key or os.environ.get("HUE_API_KEY")
        self._load_config()

        if not self.bridge_ip:
            self.discover_bridge()

    def _load_config(self) -> None:
        if os.path.exists(HUE_CONFIG_FILE):
            try:
                with open(HUE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not self.bridge_ip or self.bridge_ip == "192.168.86.46":
                        self.bridge_ip = data.get("bridge_ip", self.bridge_ip)
                    if not self.api_key:
                        self.api_key = data.get("api_key")
            except Exception:
                pass

    def _save_config(self) -> None:
        with open(HUE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"bridge_ip": self.bridge_ip, "api_key": self.api_key}, f, indent=2)

    def discover_bridge(self) -> Optional[str]:
        """Discover Hue bridge IP via local reachability or cloud discovery."""
        for candidate in ["192.168.86.46"]:
            try:
                r = httpx.get(f"http://{candidate}/api/config", timeout=2.0)
                if r.status_code == 200 and "swversion" in r.json():
                    self.bridge_ip = candidate
                    self._save_config()
                    return self.bridge_ip
            except Exception:
                pass

        try:
            r = httpx.get("https://discovery.meethue.com", timeout=4.0)
            bridges = r.json()
            if bridges and isinstance(bridges, list) and len(bridges) > 0:
                self.bridge_ip = bridges[0].get("internalipaddress")
                self._save_config()
                return self.bridge_ip
        except Exception:
            pass
        return self.bridge_ip

    def pair(self) -> Dict[str, Any]:
        """Pair with Hue Bridge. Requires the physical link button to be pressed."""
        if not self.bridge_ip:
            self.discover_bridge()
        if not self.bridge_ip:
            return {"success": False, "error": "Could not discover Philips Hue Bridge on local network."}

        try:
            r = httpx.post(
                f"http://{self.bridge_ip}/api",
                json={"devicetype": "EtaiAgent#MacBook"},
                timeout=5.0
            )
            res = r.json()
            if isinstance(res, list) and len(res) > 0:
                first = res[0]
                if "success" in first and "username" in first["success"]:
                    self.api_key = first["success"]["username"]
                    self._save_config()
                    return {
                        "success": True,
                        "bridge_ip": self.bridge_ip,
                        "api_key": self.api_key,
                        "message": "Successfully paired with Philips Hue Bridge! Connection is now active."
                    }
                elif "error" in first:
                    desc = first["error"].get("description", "Unknown error")
                    return {
                        "success": False,
                        "bridge_ip": self.bridge_ip,
                        "error": desc,
                        "instruction": "Please press the physical circular button on top of your Philips Hue Bridge, then try pairing again within 30 seconds."
                    }
            return {"success": False, "error": f"Unexpected response from bridge: {res}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to connect to Hue bridge at {self.bridge_ip}: {str(e)}"}

    @property
    def is_paired(self) -> bool:
        return bool(self.bridge_ip and self.api_key)

    def get_lights(self) -> Dict[str, Any]:
        """Fetch all physical lights from the Hue bridge."""
        if not self.is_paired:
            return {}
        try:
            r = httpx.get(f"http://{self.bridge_ip}/api/{self.api_key}/lights", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
            return {}
        except Exception:
            return {}

    def get_groups(self) -> Dict[str, Any]:
        """Fetch all groups/rooms from the Hue bridge."""
        if not self.is_paired:
            return {}
        try:
            r = httpx.get(f"http://{self.bridge_ip}/api/{self.api_key}/groups", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
            return {}
        except Exception:
            return {}

    def set_light_state(
        self,
        light_id: str,
        on: Optional[bool] = None,
        brightness_pct: Optional[int] = None,
        ct: Optional[int] = None,
        xy: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """Set physical light state, brightness, color temperature (mireds), or color coordinates."""
        if not self.is_paired:
            return {"error": "Philips Hue Bridge not paired yet."}

        payload: Dict[str, Any] = {}
        if on is not None:
            payload["on"] = on
        if brightness_pct is not None:
            bri = max(1, min(254, int(brightness_pct * 2.54)))
            payload["bri"] = bri
            if on is None and bri > 0:
                payload["on"] = True
        if ct is not None:
            payload["ct"] = max(153, min(500, int(ct)))
            if on is None:
                payload["on"] = True
        if xy is not None and len(xy) == 2:
            payload["xy"] = [float(xy[0]), float(xy[1])]
            if on is None:
                payload["on"] = True

        try:
            r = httpx.put(
                f"http://{self.bridge_ip}/api/{self.api_key}/lights/{light_id}/state",
                json=payload,
                timeout=5.0
            )
            return {"status_code": r.status_code, "response": r.json()}
        except Exception as e:
            return {"error": str(e)}

    def set_group_state(
        self,
        group_id: str,
        on: Optional[bool] = None,
        brightness_pct: Optional[int] = None,
        ct: Optional[int] = None,
        xy: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """Set state for an entire room/group of lights."""
        if not self.is_paired:
            return {"error": "Philips Hue Bridge not paired yet."}

        payload: Dict[str, Any] = {}
        if on is not None:
            payload["on"] = on
        if brightness_pct is not None:
            bri = max(1, min(254, int(brightness_pct * 2.54)))
            payload["bri"] = bri
            if on is None and bri > 0:
                payload["on"] = True
        if ct is not None:
            payload["ct"] = max(153, min(500, int(ct)))
            if on is None:
                payload["on"] = True
        if xy is not None and len(xy) == 2:
            payload["xy"] = [float(xy[0]), float(xy[1])]
            if on is None:
                payload["on"] = True

        try:
            r = httpx.put(
                f"http://{self.bridge_ip}/api/{self.api_key}/groups/{group_id}/action",
                json=payload,
                timeout=5.0
            )
            return {"status_code": r.status_code, "response": r.json()}
        except Exception as e:
            return {"error": str(e)}


@dataclass
class SmartDevice:
    id: str
    name: str
    device_type: str  # "light", "blinds"
    room: str
    state: str        # "on", "off", "open", "closed"
    brightness: Optional[int] = 100
    position: Optional[int] = 100
    color_temp: Optional[int] = 366
    color_name: Optional[str] = None
    hue_id: Optional[str] = None
    protocol: Optional[str] = "hue"


class SmartHomeController:
    """Unified Smart Home Controller bridging Home Assistant, local devices, and Philips Hue lights."""

    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.hue = HueClient()
        self.ha = HomeAssistantClient()
        self.devices: Dict[str, SmartDevice] = {}
        self._load_devices()

    def _load_devices(self) -> None:
        """Load local devices and sync with physical Philips Hue lights if paired."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.values():
                        # Filter unrecognized keys
                        valid_keys = SmartDevice.__dataclass_fields__.keys()
                        filtered = {k: v for k, v in item.items() if k in valid_keys}
                        self.devices[item["id"]] = SmartDevice(**filtered)
            except Exception:
                pass

        if self.hue.is_paired:
            self.sync_hue_lights()
        elif not self.devices:
            self.devices["living_room_blinds"] = SmartDevice(
                id="living_room_blinds",
                name="Living Room Blinds",
                device_type="blinds",
                room="Living Room",
                state="open",
                position=100,
                protocol="local"
            )
            self._save_devices()

        # Ensure PS5 and Samsung Frame TV are represented in the smart device registry
        if "ps5" not in self.devices:
            self.devices["ps5"] = SmartDevice(
                id="ps5",
                name="PlayStation 5",
                device_type="console",
                room="Living Room",
                state="standby",
                protocol="ps5_ddp"
            )
        if "samsung_frame_tv" not in self.devices:
            self.devices["samsung_frame_tv"] = SmartDevice(
                id="samsung_frame_tv",
                name="Samsung Frame TV",
                device_type="tv",
                room="Living Room",
                state="on",
                protocol="samsung_ws"
            )

    def sync_hue_lights(self) -> None:
        """Fetch real physical lights from Hue Bridge and update device registry."""
        lights = self.hue.get_lights()
        groups = self.hue.get_groups()

        light_room_map = {}
        for gid, grp in groups.items():
            gname = grp.get("name", "Home")
            for lid in grp.get("lights", []):
                light_room_map[lid] = gname

        for lid, info in lights.items():
            name = info.get("name", f"Hue Light {lid}")
            dev_id = f"hue_{lid}"
            room = light_room_map.get(lid, "Hue Room")
            state_info = info.get("state", {})
            is_on = state_info.get("on", False)
            bri_raw = state_info.get("bri", 254)
            bri_pct = int(round((bri_raw / 254.0) * 100))
            ct_val = state_info.get("ct", 366)

            self.devices[dev_id] = SmartDevice(
                id=dev_id,
                name=name,
                device_type="light",
                room=room,
                state="on" if is_on else "off",
                brightness=bri_pct,
                color_temp=ct_val,
                hue_id=lid,
                protocol="hue"
            )

        self._save_devices()

    def _save_devices(self) -> None:
        data = {dev_id: asdict(dev) for dev_id, dev in self.devices.items()}
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def pair_hue(self) -> Dict[str, Any]:
        """Attempt to pair with the local Philips Hue Bridge."""
        res = self.hue.pair()
        if res.get("success"):
            self.sync_hue_lights()
        return res

    def list_devices(self) -> List[Dict[str, Any]]:
        """List all devices from Home Assistant if connected, falling back to local cache."""
        if self.ha.is_configured():
            try:
                states = self.ha.get_states()
                ha_devices = []
                for s in states:
                    eid = str(s.get("entity_id", ""))
                    if eid.startswith(("light.", "media_player.", "remote.", "switch.")):
                        attrs = s.get("attributes", {})
                        ha_devices.append({
                            "entity_id": eid,
                            "name": attrs.get("friendly_name", eid),
                            "domain": eid.split(".", 1)[0],
                            "state": s.get("state"),
                            "brightness": round((attrs.get("brightness", 0) / 255) * 100) if attrs.get("brightness") is not None else None,
                            "color_temp_kelvin": attrs.get("color_temp_kelvin"),
                        })
                if ha_devices:
                    return ha_devices
            except Exception:
                pass
        if self.hue.is_paired:
            self.sync_hue_lights()
        return [asdict(dev) for dev in self.devices.values()]

    def _find_device(self, query: str, device_type: Optional[str] = None) -> Optional[SmartDevice]:
        q = query.lower().replace(" ", "_").replace("-", "_").replace("'", "")

        # 1. Exact ID match
        if q in self.devices:
            dev = self.devices[q]
            if not device_type or dev.device_type == device_type:
                return dev

        # 2. Match by partial ID, name, or room
        for dev in self.devices.values():
            if device_type and dev.device_type != device_type:
                continue
            name_norm = dev.name.lower().replace(" ", "_").replace("-", "_").replace("'", "")
            room_norm = dev.room.lower().replace(" ", "_").replace("-", "_").replace("'", "")
            if q == name_norm or q in dev.id or q in name_norm or (q in room_norm and dev.device_type == device_type):
                return dev

        return None

    def control_light(
        self,
        device: str,
        state: str = "on",
        brightness: Optional[int] = None,
        color_temperature: Optional[Any] = None,
        color: Optional[str] = None
    ) -> Dict[str, Any]:
        """Control smart light state, brightness, color temperature, and color via Home Assistant."""
        # Parse color temperature
        parsed_ct: Optional[int] = None
        ct_label: str = ""
        if color_temperature:
            str_ct = str(color_temperature).lower().strip().replace(" ", "_")
            if str_ct in COLOR_TEMPS:
                parsed_ct = COLOR_TEMPS[str_ct]
                ct_label = str_ct
            elif str_ct.isdigit():
                val = int(str_ct)
                if val >= 1000:
                    parsed_ct = max(153, min(500, 1000000 // val))
                    ct_label = f"{val}K"
                else:
                    parsed_ct = max(153, min(500, val))
                    ct_label = f"{parsed_ct} mireds"

        # Parse color
        parsed_xy: Optional[List[float]] = None
        parsed_rgb: Optional[List[int]] = None
        color_label: str = ""
        if color:
            str_col = str(color).lower().strip().replace(" ", "_")
            if str_col in NAMED_COLORS:
                parsed_xy = NAMED_COLORS[str_col]
                parsed_rgb = NAMED_RGB_COLORS.get(str_col)
                color_label = str_col
            elif str_col in COLOR_TEMPS:
                parsed_ct = COLOR_TEMPS[str_col]
                ct_label = str_col

        norm_state = state.lower().strip() if state else "on"
        is_on = norm_state not in ("off", "disable", "deactivate", "false", "0")

        # Primary Path: Route light control through Home Assistant REST API if configured & connected
        if self.ha.is_configured():
            ha_res = self.ha.control_light(
                device_query=device,
                is_on=is_on,
                brightness_pct=brightness if is_on else None,
                color_temp_mireds=parsed_ct if is_on else None,
                rgb_color=parsed_rgb if is_on else None,
            )
            if ha_res.get("success"):
                return ha_res

        target = self._find_device(device, device_type="light")
        if not target:
            # Check room / group match
            if self.hue.is_paired:
                groups = self.hue.get_groups()
                for gid, grp in groups.items():
                    gname = grp.get("name", "").lower()
                    if gname in device.lower() or device.lower() in gname:
                        is_on = state.lower() in ("on", "enable", "activate") or bool(parsed_ct or parsed_xy or brightness)
                        self.hue.set_group_state(
                            gid,
                            on=is_on,
                            brightness_pct=brightness,
                            ct=parsed_ct,
                            xy=parsed_xy
                        )
                        self.sync_hue_lights()
                        details = []
                        if brightness: details.append(f"brightness: {brightness}%")
                        if ct_label: details.append(f"color temp: '{ct_label}'")
                        if color_label: details.append(f"color: '{color_label}'")
                        detail_str = f" ({', '.join(details)})" if details else ""
                        return {
                            "success": True,
                            "room": grp.get("name"),
                            "state": "on" if is_on else "off",
                            "brightness": brightness,
                            "message": f"Real Philips Hue Room '{grp.get('name')}' set to {'on' if is_on else 'off'}{detail_str}."
                        }

            available = [f"{d.name} ({d.id})" for d in self.devices.values() if d.device_type == "light"]
            return {"error": f"Light '{device}' not found. Available lights: {available}"}

        norm_state = state.lower().strip() if state else "on"
        is_on = norm_state in ("on", "enable", "activate")
        # If user asked for color temp, color, or brightness adjustment, default light to on
        if parsed_ct is not None or parsed_xy is not None or (brightness is not None and brightness > 0):
            if norm_state not in ("off", "disable", "deactivate"):
                is_on = True

        target.state = "on" if is_on else "off"
        if brightness is not None:
            target.brightness = max(0, min(100, int(brightness)))
        if parsed_ct is not None:
            target.color_temp = parsed_ct
        if color_label:
            target.color_name = color_label

        # Execute physical Hue command
        if target.protocol == "hue" and target.hue_id and self.hue.is_paired:
            hue_res = self.hue.set_light_state(
                light_id=target.hue_id,
                on=is_on,
                brightness_pct=target.brightness if brightness is not None else None,
                ct=parsed_ct,
                xy=parsed_xy
            )
            self._save_devices()
            details = []
            if brightness is not None: details.append(f"brightness: {target.brightness}%")
            if ct_label: details.append(f"color temperature: '{ct_label}'")
            if color_label: details.append(f"color: '{color_label}'")
            detail_str = f" ({', '.join(details)})" if details else ""
            return {
                "success": True,
                "device": target.id,
                "name": target.name,
                "room": target.room,
                "state": target.state,
                "brightness": target.brightness,
                "color_temperature": ct_label or target.color_temp,
                "color": color_label or target.color_name,
                "hardware": "Philips Hue Bridge",
                "bridge_ip": self.hue.bridge_ip,
                "message": f"Physical Philips Hue light '{target.name}' set to {target.state}{detail_str}."
            }

        self._save_devices()
        return {
            "success": True,
            "device": target.id,
            "name": target.name,
            "room": target.room,
            "state": target.state,
            "brightness": target.brightness,
            "color_temperature": ct_label,
            "message": f"Light '{target.name}' set to {target.state}."
        }

    def control_blinds(self, device: str, action: str, position: Optional[int] = None) -> Dict[str, Any]:
        """Control smart blinds."""
        target = self._find_device(device, device_type="blinds")
        if not target:
            available = [d.id for d in self.devices.values() if d.device_type == "blinds"]
            return {"error": f"Blinds '{device}' not found. Available blinds: {available}"}

        norm_action = action.lower().strip()
        if norm_action in ("open", "raise", "up"):
            target.state = "open"
            target.position = 100
        elif norm_action in ("close", "lower", "down"):
            target.state = "closed"
            target.position = 0
        elif norm_action in ("set_position", "position", "adjust"):
            if position is None:
                return {"error": "Parameter 'position' (0-100) is required for 'set_position'."}
            pos = max(0, min(100, int(position)))
            target.position = pos
            target.state = "closed" if pos == 0 else "open"
        else:
            return {"error": f"Invalid action '{action}'. Expected 'open', 'close', or 'set_position'."}

        self._save_devices()
        return {
            "success": True,
            "device": target.id,
            "name": target.name,
            "room": target.room,
            "state": target.state,
            "position": target.position,
            "message": f"Blinds '{target.name}' {norm_action}ed (position: {target.position}%)."
        }
