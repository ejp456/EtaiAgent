import os
import re
import json
import time
import urllib.parse
from typing import Dict, Any, List, Optional
import httpx

HA_CONFIG_FILE = "ha_config.json"
DEFAULT_HA_URL = "http://127.0.0.1:8123"


def resolve_youtube_video_id(query_or_url: str, timeout: float = 5.0) -> Optional[str]:
    """Extract a 11-char YouTube video ID from a URL/ID or search YouTube for the top matching video ID."""
    q = (query_or_url or "").strip()
    if not q:
        return None
    # 1. Direct 11-char video ID
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", q):
        return q
    # 2. Extract from youtube.com/watch?v=... or youtu.be/...
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})", q)
    if m:
        return m.group(1)
    # 3. Search YouTube results page for top videoId
    try:
        encoded = urllib.parse.quote_plus(q)
        r = httpx.get(
            f"https://www.youtube.com/results?search_query={encoded}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            follow_redirects=True,
            timeout=timeout,
        )
        if r.status_code == 200:
            ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text)
            if ids:
                return ids[0]
    except Exception:
        pass
    return None


class HomeAssistantClient:
    """Unified Home Assistant REST API client for controlling Google Cast, PS5, Samsung Frame TV, and smart home entities.

    Designed to connect to the local Docker stack (`http://127.0.0.1:8123`) or a remote server
    (e.g. `http://192.168.86.39:8123`) using standard HTTP REST requests that work seamlessly
    inside the `srt` sandbox.
    """

    def __init__(
        self,
        ha_url: Optional[str] = None,
        ha_token: Optional[str] = None,
        config_path: str = HA_CONFIG_FILE
    ):
        self.config_path = config_path
        self.ha_url = (ha_url or os.environ.get("HA_URL") or DEFAULT_HA_URL).rstrip("/")
        self.ha_token = ha_token or os.environ.get("HA_TOKEN") or ""
        self.ps5_entity_id: Optional[str] = None
        self.tv_entity_id: Optional[str] = None
        self.tv_remote_entity_id: Optional[str] = None
        self.cast_entity_id: Optional[str] = "media_player.living_room_tv"
        self._load_config()

    def _load_config(self) -> None:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("ha_url"):
                        self.ha_url = str(data["ha_url"]).rstrip("/")
                    if data.get("ha_token"):
                        self.ha_token = str(data["ha_token"]).strip()
                    self.ps5_entity_id = data.get("ps5_entity_id") or self.ps5_entity_id
                    self.tv_entity_id = data.get("tv_entity_id") or self.tv_entity_id
                    self.tv_remote_entity_id = data.get("tv_remote_entity_id") or self.tv_remote_entity_id
                    self.cast_entity_id = data.get("cast_entity_id") or self.cast_entity_id
            except Exception:
                pass

    def save_config(self) -> None:
        data = {
            "ha_url": self.ha_url,
            "ha_token": self.ha_token,
            "ps5_entity_id": self.ps5_entity_id,
            "tv_entity_id": self.tv_entity_id,
            "tv_remote_entity_id": self.tv_remote_entity_id,
            "cast_entity_id": self.cast_entity_id,
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Ensure the Home Assistant host:port is whitelisted in srt-settings.json
        srt_file = "srt-settings.json"
        if self.config_path == HA_CONFIG_FILE and os.path.exists(srt_file):
            try:
                host_port = self.ha_url.replace("http://", "").replace("https://", "").split("/")[0]
                with open(srt_file, "r", encoding="utf-8") as f:
                    srt_data = json.load(f)
                allowed = srt_data.setdefault("network", {}).setdefault("allowedDomains", [])
                if host_port and host_port not in allowed:
                    allowed.append(host_port)
                    with open(srt_file, "w", encoding="utf-8") as f:
                        json.dump(srt_data, f, indent=2)
            except Exception:
                pass

    def is_configured(self) -> bool:
        """Return True if a Home Assistant Long-Lived Access Token is configured."""
        return bool(self.ha_url and self.ha_token)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.ha_token:
            headers["Authorization"] = f"Bearer {self.ha_token}"
        return headers

    def check_connection(self, timeout: float = 3.0) -> Dict[str, Any]:
        """Establish and verify connection to the Home Assistant API before issuing commands."""
        if not self.ha_url:
            return {"connected": False, "error": "Home Assistant URL is not configured."}
        if not self.ha_token:
            # Check if the Home Assistant web UI is at least reachable even without a token yet
            try:
                r = httpx.get(f"{self.ha_url}/manifest.json", timeout=timeout)
                ui_reachable = r.status_code == 200
            except Exception:
                ui_reachable = False
            return {
                "connected": False,
                "ui_reachable": ui_reachable,
                "ha_url": self.ha_url,
                "error": (
                    "Home Assistant Long-Lived Access Token is not configured yet. "
                    f"Open {self.ha_url} -> Profile -> Security -> Long-Lived Access Tokens, "
                    "create a token, and call configure_home_assistant(ha_token='...')."
                )
            }

        try:
            r = httpx.get(f"{self.ha_url}/api/", headers=self._headers(), timeout=timeout)
            if r.status_code == 200:
                body = r.json()
                return {
                    "connected": True,
                    "ha_url": self.ha_url,
                    "message": body.get("message", "API running."),
                }
            return {
                "connected": False,
                "ha_url": self.ha_url,
                "status_code": r.status_code,
                "error": f"Home Assistant API returned HTTP {r.status_code}: {r.text[:200]}",
            }
        except Exception as e:
            return {
                "connected": False,
                "ha_url": self.ha_url,
                "error": f"Could not connect to Home Assistant at {self.ha_url}: {str(e)}",
            }

    def get_states(self, domain_filter: Optional[str] = None, timeout: float = 4.0) -> List[Dict[str, Any]]:
        """Retrieve entity states from Home Assistant, optionally filtered by domain (e.g. 'media_player', 'remote', 'light')."""
        r = httpx.get(f"{self.ha_url}/api/states", headers=self._headers(), timeout=timeout)
        r.raise_for_status()
        states = r.json()
        if domain_filter:
            prefix = f"{domain_filter.lower().rstrip('.')}."
            states = [s for s in states if str(s.get("entity_id", "")).startswith(prefix)]
        return states

    def discover_media_entities(self) -> Dict[str, Any]:
        """Auto-detect the PS5 and Samsung Frame TV entities registered in Home Assistant."""
        conn = self.check_connection()
        if not conn.get("connected"):
            return conn

        try:
            states = self.get_states()
        except Exception as e:
            return {"connected": False, "error": f"Failed to query Home Assistant states: {str(e)}"}

        media_players: List[Dict[str, Any]] = []
        remotes: List[Dict[str, Any]] = []
        switches: List[Dict[str, Any]] = []

        for s in states:
            eid = str(s.get("entity_id", ""))
            friendly = str(s.get("attributes", {}).get("friendly_name", "")).lower()
            target_str = f"{eid} {friendly}".lower()

            if eid.startswith("media_player."):
                media_players.append({
                    "entity_id": eid,
                    "state": s.get("state"),
                    "friendly_name": s.get("attributes", {}).get("friendly_name"),
                    "app_name": s.get("attributes", {}).get("app_name"),
                    "media_title": s.get("attributes", {}).get("media_title"),
                    "source": s.get("attributes", {}).get("source"),
                    "source_list": s.get("attributes", {}).get("source_list"),
                })
                if any(k in target_str for k in ("ps5", "playstation")):
                    self.ps5_entity_id = eid
                elif any(k in target_str for k in ("samsung", "frame", "qn65")):
                    self.tv_entity_id = eid
                elif any(k in target_str for k in ("living_room_tv", "chromecast", "cast")):
                    self.cast_entity_id = eid

            elif eid.startswith("remote."):
                remotes.append({
                    "entity_id": eid,
                    "state": s.get("state"),
                    "friendly_name": s.get("attributes", {}).get("friendly_name"),
                })
                if any(k in target_str for k in ("samsung", "frame", "qn65", "tv")):
                    self.tv_remote_entity_id = eid

            elif eid.startswith("switch.") and any(k in target_str for k in ("ps5", "playstation")):
                switches.append({
                    "entity_id": eid,
                    "state": s.get("state"),
                    "friendly_name": s.get("attributes", {}).get("friendly_name"),
                })
                if not self.ps5_entity_id:
                    self.ps5_entity_id = eid

        self.save_config()
        return {
            "connected": True,
            "ha_url": self.ha_url,
            "ps5_entity_id": self.ps5_entity_id,
            "tv_entity_id": self.tv_entity_id,
            "tv_remote_entity_id": self.tv_remote_entity_id,
            "cast_entity_id": self.cast_entity_id,
            "media_players": media_players,
            "remotes": remotes,
            "ps5_switches": switches,
        }

    def call_service(
        self,
        domain: str,
        service: str,
        service_data: Dict[str, Any],
        timeout: float = 8.0
    ) -> Dict[str, Any]:
        """Call a Home Assistant service (`POST /api/services/<domain>/<service>`)."""
        url = f"{self.ha_url}/api/services/{domain}/{service}"
        try:
            r = httpx.post(url, headers=self._headers(), json=service_data, timeout=timeout)
            if r.status_code in (200, 201):
                return {
                    "success": True,
                    "service": f"{domain}.{service}",
                    "service_data": service_data,
                    "changed_states": r.json() if r.content else [],
                }
            return {
                "success": False,
                "service": f"{domain}.{service}",
                "status_code": r.status_code,
                "error": r.text[:250],
            }
        except Exception as e:
            return {
                "success": False,
                "service": f"{domain}.{service}",
                "error": str(e),
            }

    def watch_on_ps5(
        self,
        app_info: Dict[str, Any],
        show_title: str = "",
        skip_profile_prompt: bool = True,
        cec_keys_for_show: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Orchestrate waking the TV & PS5, selecting the app/source, and searching for a show via Home Assistant."""
        # Step 1: Verify connection to Home Assistant and discover entities first
        discovery = self.discover_media_entities()
        if not discovery.get("connected"):
            return {
                "success": False,
                "via": "home_assistant",
                "error": discovery.get("error", "Home Assistant is not connected."),
            }

        if not self.ps5_entity_id and not self.tv_entity_id and not self.tv_remote_entity_id:
            return {
                "success": False,
                "via": "home_assistant",
                "error": "Connected to Home Assistant, but no PS5 or Samsung TV entities were found yet.",
            }

        steps: List[Dict[str, Any]] = [{
            "step": "1_verify_ha_connection_and_entities",
            "ha_url": self.ha_url,
            "ps5_entity_id": self.ps5_entity_id,
            "tv_entity_id": self.tv_entity_id,
            "tv_remote_entity_id": self.tv_remote_entity_id,
        }]

        # Step 2: Turn on Samsung Frame TV if present
        if self.tv_entity_id:
            tv_on = self.call_service("media_player", "turn_on", {"entity_id": self.tv_entity_id})
            steps.append({"step": "2_turn_on_tv", "result": tv_on})

        # Step 3: Wake PS5 and select streaming app if PS5 entity is present in HA (ps5-mqtt)
        if self.ps5_entity_id:
            domain = self.ps5_entity_id.split(".", 1)[0]
            ps5_on = self.call_service(domain, "turn_on", {"entity_id": self.ps5_entity_id})
            steps.append({"step": "3a_wake_ps5", "result": ps5_on})

            if domain == "media_player":
                # ps5-mqtt exposes installed PS5 games/apps via media_player.select_source
                src_res = self.call_service(
                    "media_player",
                    "select_source",
                    {"entity_id": self.ps5_entity_id, "source": app_info["name"]}
                )
                steps.append({"step": "3b_select_ps5_source", "app": app_info["name"], "result": src_res})

        # Step 4: Send remote/HDMI-CEC navigation keys via Home Assistant `remote.send_command`
        if self.tv_remote_entity_id:
            if not self.ps5_entity_id:
                # Dismiss any Samsung Tizen overlay and focus/open the highlighted PS5 media app via HDMI-CEC
                wake_nav = self.call_service(
                    "remote",
                    "send_command",
                    {"entity_id": self.tv_remote_entity_id, "command": ["KEY_EXIT", "KEY_ENTER"], "delay_secs": 1.0}
                )
                steps.append({"step": "4a_hdmi_cec_focus_and_enter", "result": wake_nav})
                if skip_profile_prompt or show_title:
                    time.sleep(3.5)

            if skip_profile_prompt and app_info.get("has_profile_prompt"):
                prof_res = self.call_service(
                    "remote",
                    "send_command",
                    {"entity_id": self.tv_remote_entity_id, "command": ["KEY_ENTER"]}
                )
                steps.append({"step": "4b_select_profile", "result": prof_res})
                if show_title:
                    time.sleep(2.5)

            if show_title and cec_keys_for_show:
                search_nav = app_info.get("search_nav", ["KEY_LEFT", "KEY_UP", "KEY_ENTER"])
                nav_res = self.call_service(
                    "remote",
                    "send_command",
                    {"entity_id": self.tv_remote_entity_id, "command": search_nav, "delay_secs": 0.55}
                )
                steps.append({"step": "4c_open_search", "result": nav_res})
                time.sleep(1.2)

                kb_res = self.call_service(
                    "remote",
                    "send_command",
                    {"entity_id": self.tv_remote_entity_id, "command": cec_keys_for_show, "delay_secs": 0.4}
                )
                steps.append({"step": "4d_search_and_play", "key_count": len(cec_keys_for_show), "result": kb_res})

        return {
            "success": True,
            "via": "home_assistant",
            "ha_url": self.ha_url,
            "app": app_info["name"],
            "app_id": app_info["id"],
            "ps5_title_id": app_info["ps5_title_id"],
            "show_title": show_title or None,
            "ps5_entity_id": self.ps5_entity_id,
            "tv_entity_id": self.tv_entity_id,
            "tv_remote_entity_id": self.tv_remote_entity_id,
            "steps": steps,
            "message": (
                f"Initiated '{app_info['name']}'"
                + (f" and queued search/playback for '{show_title}'" if show_title else "")
                + f" via Home Assistant ({self.ha_url})."
            ),
        }

    def resolve_cast_entity(self, target_device: str = "living_room_tv") -> str:
        """Resolve a friendly name or alias (e.g. 'Living Room TV', 'Bathroom speaker', 'Kitchen clock', 'Home Group') to its Cast entity_id."""
        q = (target_device or "").lower().strip().replace(" ", "_").replace("-", "_")
        if not q or q in ("tv", "living_room", "living_room_tv", "chromecast", "cast", "frame"):
            return self.cast_entity_id or "media_player.living_room_tv"
        if q.startswith("media_player."):
            return q
        alias_map = {
            "bathroom": "media_player.bathroom_speaker",
            "bathroom_speaker": "media_player.bathroom_speaker",
            "kitchen": "media_player.kitchen_clock",
            "kitchen_clock": "media_player.kitchen_clock",
            "home_group": "media_player.home_group",
            "all": "media_player.home_group",
            "everywhere": "media_player.home_group",
        }
        if q in alias_map:
            return alias_map[q]
        try:
            for s in self.get_states(domain_filter="media_player"):
                eid = str(s.get("entity_id", ""))
                fname = str(s.get("attributes", {}).get("friendly_name", "")).lower().replace(" ", "_")
                if q in eid.lower() or q in fname:
                    return eid
        except Exception:
            pass
        return f"media_player.{q}"

    def cast_media(
        self,
        query_or_url: str,
        app: str = "youtube",
        target_device: str = "living_room_tv",
    ) -> Dict[str, Any]:
        """Cast YouTube videos, direct media streams, or TTS announcements to a Google Cast device via Home Assistant."""
        discovery = self.discover_media_entities()
        if not discovery.get("connected"):
            return {"success": False, "via": "home_assistant_cast", "error": discovery.get("error")}

        entity_id = self.resolve_cast_entity(target_device)
        steps: List[Dict[str, Any]] = []

        # Step 1: Turn on the Samsung Frame TV if casting to the Living Room TV
        if entity_id == (self.cast_entity_id or "media_player.living_room_tv") and self.tv_entity_id:
            tv_on = self.call_service("media_player", "turn_on", {"entity_id": self.tv_entity_id})
            steps.append({"step": "1_turn_on_tv", "entity_id": self.tv_entity_id, "result": tv_on})

        # Step 2: Wake the Google Cast receiver
        cast_on = self.call_service("media_player", "turn_on", {"entity_id": entity_id})
        steps.append({"step": "2_wake_cast_device", "entity_id": entity_id, "result": cast_on})

        norm_app = (app or "youtube").lower().strip()
        q = (query_or_url or "").strip()

        # Case A: Text-to-Speech voice announcement on the Cast device
        if norm_app in ("tts", "say", "announce", "speak"):
            tts_res = self.call_service(
                "tts",
                "speak",
                {
                    "entity_id": "tts.google_translate_en_com",
                    "media_player_entity_id": entity_id,
                    "message": q,
                },
            )
            steps.append({"step": "3_cast_tts", "message": q, "result": tts_res})
            return {
                "success": tts_res.get("success", False),
                "via": "home_assistant_cast",
                "entity_id": entity_id,
                "app": "tts",
                "message_spoken": q,
                "steps": steps,
            }

        # Case B: Direct HTTP/HTTPS stream URL (non-YouTube)
        if q.startswith(("http://", "https://")) and "youtube.com" not in q and "youtu.be" not in q:
            is_audio = any(q.lower().endswith(ext) for ext in (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"))
            content_type = "music" if is_audio else "video"
            play_res = self.call_service(
                "media_player",
                "play_media",
                {
                    "entity_id": entity_id,
                    "media_content_type": content_type,
                    "media_content_id": q,
                },
            )
            steps.append({"step": "3_cast_direct_url", "url": q, "content_type": content_type, "result": play_res})
            return {
                "success": play_res.get("success", False),
                "via": "home_assistant_cast",
                "entity_id": entity_id,
                "app": "default_media_receiver",
                "media_url": q,
                "steps": steps,
            }

        # Case C: YouTube video search or URL/ID cast via Home Assistant pychromecast YouTube controller
        video_id = resolve_youtube_video_id(q)
        if not video_id:
            return {
                "success": False,
                "via": "home_assistant_cast",
                "entity_id": entity_id,
                "error": f"Could not resolve a YouTube video ID for '{q}'.",
            }

        cast_payload = json.dumps({"app_name": "youtube", "media_id": video_id})
        play_res = self.call_service(
            "media_player",
            "play_media",
            {
                "entity_id": entity_id,
                "media_content_type": "cast",
                "media_content_id": cast_payload,
            },
        )
        steps.append({
            "step": "3_cast_youtube",
            "query": q,
            "video_id": video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "result": play_res,
        })
        return {
            "success": play_res.get("success", False),
            "via": "home_assistant_cast",
            "entity_id": entity_id,
            "app": "YouTube",
            "query": q,
            "video_id": video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "steps": steps,
            "message": f"Casting YouTube video '{q}' (https://www.youtube.com/watch?v={video_id}) to {entity_id}.",
        }

    def control_media(self, command: str, key_codes: List[str], repeat: int = 1) -> Dict[str, Any]:
        """Send playback, power, or remote navigation commands via Home Assistant."""
        discovery = self.discover_media_entities()
        if not discovery.get("connected"):
            return {"success": False, "via": "home_assistant", "error": discovery.get("error")}

        norm_cmd = command.lower().strip().replace(" ", "_").replace("-", "_")

        if norm_cmd in ("ps5_wake", "wake_ps5", "turn_on_ps5") and self.ps5_entity_id:
            domain = self.ps5_entity_id.split(".", 1)[0]
            return self.call_service(domain, "turn_on", {"entity_id": self.ps5_entity_id})

        if norm_cmd in ("ps5_standby", "standby", "rest_mode", "sleep_ps5", "turn_off_ps5") and self.ps5_entity_id:
            domain = self.ps5_entity_id.split(".", 1)[0]
            return self.call_service(domain, "turn_off", {"entity_id": self.ps5_entity_id})

        if norm_cmd in ("play", "pause", "stop"):
            service_map = {
                "play": "media_play",
                "pause": "media_pause",
                "stop": "media_stop",
            }
            targets = [eid for eid in (self.cast_entity_id, self.tv_entity_id) if eid]
            if targets:
                return self.call_service("media_player", service_map[norm_cmd], {"entity_id": targets})

        if norm_cmd in ("volume_up", "vol_up", "volume_down", "vol_down", "mute") and self.cast_entity_id:
            vol_service = {
                "volume_up": "volume_up",
                "vol_up": "volume_up",
                "volume_down": "volume_down",
                "vol_down": "volume_down",
                "mute": "volume_mute",
            }[norm_cmd]
            payload: Dict[str, Any] = {"entity_id": self.cast_entity_id}
            if vol_service == "volume_mute":
                payload["is_volume_muted"] = True
            self.call_service("media_player", vol_service, payload)

        if self.tv_remote_entity_id and key_codes:
            return self.call_service(
                "remote",
                "send_command",
                {
                    "entity_id": self.tv_remote_entity_id,
                    "command": key_codes,
                    "num_repeats": repeat,
                    "delay_secs": 0.25,
                }
            )

        return {
            "success": False,
            "via": "home_assistant",
            "error": "No matching Home Assistant media_player or remote entity found for command.",
        }

    def control_light(
        self,
        device_query: str,
        is_on: bool = True,
        brightness_pct: Optional[int] = None,
        color_temp_mireds: Optional[int] = None,
        rgb_color: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Control a light or room group via Home Assistant `light.turn_on` / `light.turn_off`."""
        conn = self.check_connection()
        if not conn.get("connected"):
            return {"success": False, "via": "home_assistant", "error": conn.get("error")}

        try:
            light_states = self.get_states(domain_filter="light")
        except Exception as e:
            return {"success": False, "via": "home_assistant", "error": str(e)}

        q = device_query.lower().strip().replace("'", "").replace("-", "_").replace(" ", "_")

        # Support "all", "all_lights", "every_light", "house", "home" to control all lights at once
        if q in ("all", "all_lights", "every_light", "lights", "everything", "house", "home"):
            service = "turn_on" if is_on else "turn_off"
            service_data: Dict[str, Any] = {"entity_id": "all"}
            if is_on:
                if brightness_pct is not None:
                    service_data["brightness_pct"] = max(1, min(100, int(brightness_pct)))
                if color_temp_mireds is not None:
                    kelvin = max(2000, min(6500, int(round(1000000 / max(153, min(500, color_temp_mireds))))))
                    service_data["color_temp_kelvin"] = kelvin
                if rgb_color is not None and len(rgb_color) == 3:
                    service_data["rgb_color"] = rgb_color
            res = self.call_service("light", service, service_data)
            return {
                "success": res.get("success", False),
                "via": "home_assistant",
                "ha_url": self.ha_url,
                "entity_id": "all",
                "name": "All Lights",
                "state": "on" if is_on else "off",
                "service_data": service_data,
                "result": res,
                "message": f"All Home Assistant lights turned {'on' if is_on else 'off'}.",
            }

        matched_entity: Optional[Dict[str, Any]] = None

        # 1. Exact or partial match on entity_id or friendly_name
        for st in light_states:
            eid = str(st.get("entity_id", ""))
            fname = str(st.get("attributes", {}).get("friendly_name", ""))
            norm_eid = eid.lower().replace("light.", "")
            norm_fname = fname.lower().replace("'", "").replace("-", "_").replace(" ", "_")
            if q in (eid.lower(), norm_eid, norm_fname) or q in norm_eid or q in norm_fname:
                matched_entity = st
                break

        if not matched_entity:
            return {
                "success": False,
                "via": "home_assistant",
                "error": f"No Home Assistant light entity matching '{device_query}'.",
            }

        entity_id = matched_entity["entity_id"]
        friendly_name = matched_entity.get("attributes", {}).get("friendly_name", entity_id)

        if not is_on:
            res = self.call_service("light", "turn_off", {"entity_id": entity_id})
            return {
                "success": res.get("success", False),
                "via": "home_assistant",
                "ha_url": self.ha_url,
                "entity_id": entity_id,
                "name": friendly_name,
                "state": "off",
                "result": res,
                "message": f"Home Assistant light '{friendly_name}' ({entity_id}) turned off.",
            }

        service_data: Dict[str, Any] = {"entity_id": entity_id}
        if brightness_pct is not None:
            service_data["brightness_pct"] = max(1, min(100, int(brightness_pct)))
        if color_temp_mireds is not None:
            # Home Assistant 2026+ uses kelvin (`color_temp_kelvin`)
            kelvin = max(2000, min(6500, int(round(1000000 / max(153, min(500, color_temp_mireds))))))
            service_data["color_temp_kelvin"] = kelvin
        if rgb_color is not None and len(rgb_color) == 3:
            service_data["rgb_color"] = rgb_color

        res = self.call_service("light", "turn_on", service_data)
        return {
            "success": res.get("success", False),
            "via": "home_assistant",
            "ha_url": self.ha_url,
            "entity_id": entity_id,
            "name": friendly_name,
            "state": "on",
            "service_data": service_data,
            "result": res,
            "message": f"Home Assistant light '{friendly_name}' ({entity_id}) set to on.",
        }

