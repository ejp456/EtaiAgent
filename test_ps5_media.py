import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from ps5_media import (
    STREAMING_APPS,
    resolve_streaming_app,
    build_cec_keyboard_sequence,
    JustWatchClient,
    PS5Client,
    SamsungFrameClient,
    PS5MediaController,
)
from home_assistant import HomeAssistantClient
from tools import (
    get_tools_schema,
    dispatch_tool,
    list_streaming_apps,
    recommend_shows_by_mood,
    search_streaming_catalog,
)


class TestPS5MediaAndMoods(unittest.TestCase):

    def test_streaming_apps_enumeration_and_aliases(self):
        expected_ids = {
            "peacock", "netflix", "youtube", "disney_plus", "hulu",
            "max", "prime_video", "apple_tv", "paramount_plus",
            "crunchyroll", "plex", "spotify", "twitch"
        }
        self.assertTrue(expected_ids.issubset(set(STREAMING_APPS.keys())))

        self.assertEqual(resolve_streaming_app("Peacock")["id"], "peacock")
        self.assertEqual(resolve_streaming_app("nbc")["id"], "peacock")
        self.assertEqual(resolve_streaming_app("Netflix")["id"], "netflix")
        self.assertEqual(resolve_streaming_app("HBO Max")["id"], "max")
        self.assertEqual(resolve_streaming_app("Disney+")["id"], "disney_plus")
        self.assertEqual(resolve_streaming_app("Amazon Prime")["id"], "prime_video")
        self.assertEqual(resolve_streaming_app("Apple TV+")["id"], "apple_tv")
        self.assertEqual(resolve_streaming_app("YouTube")["id"], "youtube")
        self.assertIsNone(resolve_streaming_app("nonexistent_app_xyz"))

    def test_mood_recommendations_with_and_without_app_filter(self):
        controller = PS5MediaController(config_path=tempfile.mktemp())

        # Mood query across all apps
        funny_res = controller.recommend_by_mood(mood="funny and cozy", limit=5)
        self.assertEqual(funny_res["count"], 5)
        titles = [r["title"] for r in funny_res["recommendations"]]
        self.assertTrue(any(t in titles for t in ("The Office", "Parks and Recreation", "Ted Lasso")))

        # Mood query filtered specifically to Peacock
        peacock_res = controller.recommend_by_mood(mood="thriller mystery", app="peacock", limit=3)
        for rec in peacock_res["recommendations"]:
            self.assertEqual(rec["app_id"], "peacock")
        peacock_titles = [r["title"] for r in peacock_res["recommendations"]]
        self.assertTrue(any(t in peacock_titles for t in ("Poker Face", "The Day of the Jackal", "The Traitors")))

        # Mood query for mind-bending sci-fi
        scifi_res = controller.recommend_by_mood(mood="mind-bending sci-fi", limit=4)
        scifi_titles = [r["title"] for r in scifi_res["recommendations"]]
        self.assertTrue(any(t in scifi_titles for t in ("Severance", "Dark", "Black Mirror")))

    def test_ps5_ddp_packet_parsing_and_playactor_cmds(self):
        awake_packet = (
            "HTTP/1.1 200 Ok\n"
            "host-id:123456789ABC\n"
            "host-type:PS5\n"
            "host-name:PS5-LivingRoom\n"
            "running-app-titleid:PPSA04920\n"
            "running-app-name:Peacock\n"
            "system-version:10010000\n"
        )
        parsed = PS5Client.parse_ddp_response(awake_packet, source_ip="192.168.86.105")
        self.assertEqual(parsed["state"], "awake")
        self.assertEqual(parsed["ip"], "192.168.86.105")
        self.assertEqual(parsed["host_name"], "PS5-LivingRoom")
        self.assertEqual(parsed["running_app_titleid"], "PPSA04920")
        self.assertEqual(parsed["running_app_name"], "Peacock")

        standby_packet = (
            "HTTP/1.1 620 Server Standby\n"
            "host-id:123456789ABC\n"
            "host-type:PS5\n"
            "host-name:PS5-LivingRoom\n"
        )
        parsed_standby = PS5Client.parse_ddp_response(standby_packet, source_ip="192.168.86.105")
        self.assertEqual(parsed_standby["state"], "standby")

        ps5 = PS5Client(ps5_ip="192.168.86.105")
        start_cmd = ps5.build_playactor_command("start-id", ["PPSA04920"])
        self.assertIn("start-id", start_cmd)
        self.assertIn("PPSA04920", start_cmd)
        self.assertIn("192.168.86.105", start_cmd)

    def test_hdmi_cec_keyboard_sequence_generation(self):
        row_seq = build_cec_keyboard_sequence("The Office", layout="qwerty_row")
        self.assertIn("KEY_ENTER", row_seq)
        self.assertEqual(row_seq[-1], "KEY_PLAY")

        grid_seq = build_cec_keyboard_sequence("Dark", layout="grid_6col")
        self.assertIn("KEY_ENTER", grid_seq)
        self.assertEqual(grid_seq[-1], "KEY_PLAY")

    def test_watch_on_ps5_and_control_media_orchestration(self):
        tmp_cfg = tempfile.mktemp()
        controller = PS5MediaController(config_path=tmp_cfg)
        controller.ps5.ps5_ip = "192.168.86.105"
        controller.tv.tv_ip = "192.168.86.110"

        with patch.object(controller.tv, "ensure_awake", return_value={"status": "awake", "tv_ip": "192.168.86.110"}), \
             patch.object(controller.tv, "open_session", return_value={"connected": True, "tv_ip": "192.168.86.110"}), \
             patch.object(controller.ps5, "wake", return_value={"success": True, "already_awake": True}), \
             patch.object(controller.ps5, "query_status", return_value={"state": "awake", "running_app_titleid": "PPSA04920", "running_app_name": "Peacock"}), \
             patch.object(controller.tv, "send_keys", return_value={"success": True, "sent_keys": ["KEY_ENTER", "KEY_PLAY"]}) as mock_send_keys:

            res = controller.watch_on_ps5(app="Peacock", show_title="The Office", skip_profile_prompt=True)
            self.assertTrue(res["success"])
            self.assertEqual(res["app"], "Peacock")
            self.assertEqual(res["ps5_title_id"], "PPSA04920")
            self.assertEqual(res["show_title"], "The Office")
            self.assertTrue(mock_send_keys.called)

            # Test live playback command via HDMI-CEC
            pause_res = controller.control_media("pause")
            self.assertTrue(pause_res["success"])

        # Verify connection-first check aborts immediately when TV WebSocket cannot connect
        with patch.object(controller.tv, "ensure_awake", return_value={"status": "awake"}), \
             patch.object(controller.tv, "open_session", return_value={"connected": False, "error": "TV unreachable"}), \
             patch.object(controller.tv, "send_keys") as mock_send_keys_fail:
            fail_res = controller.watch_on_ps5(app="Hulu", show_title="That '70s Show")
            self.assertFalse(fail_res["success"])
            self.assertFalse(mock_send_keys_fail.called)

        if os.path.exists(tmp_cfg):
            os.remove(tmp_cfg)

    def test_tools_registration_and_dispatch(self):
        schemas = get_tools_schema()
        tool_names = {t["function"]["name"] for t in schemas}
        self.assertIn("list_streaming_apps", tool_names)
        self.assertIn("recommend_shows_by_mood", tool_names)
        self.assertIn("search_streaming_catalog", tool_names)
        self.assertIn("watch_on_ps5", tool_names)
        self.assertIn("control_ps5_media", tool_names)
        self.assertIn("configure_home_assistant", tool_names)
        self.assertIn("control_light", tool_names)

        apps_resp = dispatch_tool("list_streaming_apps", {})
        parsed_apps = json.loads(apps_resp["content"])
        self.assertGreaterEqual(len(parsed_apps), 13)

        mood_resp = dispatch_tool("recommend_shows_by_mood", {"mood": "funny", "app": "peacock", "limit": 3})
        parsed_mood = json.loads(mood_resp["content"])
        self.assertEqual(parsed_mood["filtered_app"], "Peacock")
        self.assertEqual(len(parsed_mood["recommendations"]), 3)
        self.assertIn("configure_home_assistant", tool_names)

    def test_justwatch_client_parsing_and_watch_auto_resolve(self):
        jw = JustWatchClient()

        # Verify package mapping & add-on filtering
        self.assertEqual(jw.map_package_to_app({"shortName": "pck", "clearName": "Peacock Premium"})["id"], "peacock")
        self.assertEqual(jw.map_package_to_app({"shortName": "nfx", "clearName": "Netflix"})["id"], "netflix")
        self.assertEqual(jw.map_package_to_app({"shortName": "mxx", "clearName": "Max"})["id"], "max")
        self.assertEqual(jw.map_package_to_app({"shortName": "atp", "clearName": "Apple TV+"})["id"], "apple_tv")
        self.assertIsNone(jw.map_package_to_app({"shortName": "mxx", "clearName": "Max Amazon Channel", "technicalName": "maxamazonchannel"}))

        # Verify genre resolution
        genre_codes = jw.resolve_genre_codes("funny sci-fi thriller")
        self.assertIn("cmy", genre_codes)
        self.assertIn("scf", genre_codes)
        self.assertIn("trl", genre_codes)

        # Verify GraphQL node parsing and PS5 offer prioritization
        sample_node = {
            "objectType": "SHOW",
            "content": {
                "title": "Silo",
                "originalReleaseYear": 2023,
                "shortDescription": "In a ruined and toxic future, thousands live in a giant silo deep underground.",
                "fullPath": "/us/tv-show/silo",
                "genres": [{"shortName": "scf"}, {"shortName": "drm"}],
                "scoring": {"imdbScore": 8.1, "tmdbScore": 8.2},
            },
            "offers": [
                {
                    "monetizationType": "FLATRATE",
                    "presentationType": "4K",
                    "standardWebURL": "https://tv.apple.com/us/show/silo",
                    "package": {
                        "shortName": "atp",
                        "clearName": "Apple TV+",
                        "technicalName": "appletvplus",
                    },
                }
            ],
        }
        parsed = jw.parse_title_node(sample_node)
        self.assertEqual(parsed["title"], "Silo")
        self.assertEqual(parsed["primary_ps5_app"], "apple_tv")
        self.assertEqual(parsed["primary_ps5_app_name"], "Apple TV+")
        self.assertIn("watch_on_ps5(app='apple_tv'", parsed["how_to_play"])

        # Verify watch_on_ps5(app='auto', show_title='Silo') resolves Apple TV+ via JustWatch
        tmp_ha_cfg = tempfile.mktemp()
        tmp_ps5_cfg = tempfile.mktemp()
        controller = PS5MediaController(config_path=tmp_ps5_cfg, ha_config_path=tmp_ha_cfg)
        controller.ha.ha_url = "http://127.0.0.1:8123"
        controller.ha.ha_token = "test_token"

        mock_states = [
            {"entity_id": "media_player.ps5_712", "state": "idle", "attributes": {"friendly_name": "PS5-712"}},
            {"entity_id": "remote.samsung_the_frame_65", "state": "on", "attributes": {"friendly_name": "Samsung TV Remote"}},
        ]
        with patch.object(controller.justwatch, "find_streaming_app_for_title", return_value={
            "app_info": STREAMING_APPS["apple_tv"],
            "matched_title": "Silo",
            "type": "SHOW",
            "release_year": 2023,
            "available_on_ps5_apps": parsed["available_on_ps5_apps"],
        }) as mock_jw_find, \
             patch.object(controller.ha, "check_connection", return_value={"connected": True}), \
             patch.object(controller.ha, "get_states", return_value=mock_states), \
             patch.object(controller.ha, "call_service", return_value={"success": True}), \
             patch("home_assistant.time.sleep"):
            auto_res = controller.watch_on_ps5(app="auto", show_title="Silo")
            self.assertTrue(auto_res["success"])
            self.assertTrue(mock_jw_find.called)

        for p in (tmp_ha_cfg, tmp_ps5_cfg):
            if os.path.exists(p):
                os.remove(p)

    def test_home_assistant_client_and_orchestration(self):
        tmp_ha_cfg = tempfile.mktemp()
        tmp_ps5_cfg = tempfile.mktemp()
        controller = PS5MediaController(config_path=tmp_ps5_cfg, ha_config_path=tmp_ha_cfg)
        controller.ha.ha_url = "http://127.0.0.1:8123"
        controller.ha.ha_token = "test_long_lived_token_123"

        mock_states = [
            {
                "entity_id": "media_player.ps5_712",
                "state": "idle",
                "attributes": {"friendly_name": "PS5-712", "source_list": ["Peacock", "Hulu", "Netflix"]}
            },
            {
                "entity_id": "media_player.samsung_the_frame_65",
                "state": "on",
                "attributes": {"friendly_name": "Samsung The Frame 65"}
            },
            {
                "entity_id": "remote.samsung_the_frame_65",
                "state": "on",
                "attributes": {"friendly_name": "Samsung The Frame 65 Remote"}
            },
            {
                "entity_id": "media_player.living_room_tv",
                "state": "idle",
                "attributes": {"friendly_name": "Living Room TV"}
            },
        ]

        with patch.object(controller.ha, "check_connection", return_value={"connected": True, "ha_url": "http://127.0.0.1:8123"}), \
             patch.object(controller.ha, "get_states", return_value=mock_states), \
             patch.object(controller.ha, "call_service", return_value={"success": True}) as mock_call_service, \
             patch("home_assistant.resolve_youtube_video_id", return_value="dQw4w9WgXcQ"), \
             patch("home_assistant.time.sleep"):
            res = controller.watch_on_ps5(app="Hulu", show_title="That 70s Show", skip_profile_prompt=True)
            self.assertTrue(res["success"])
            self.assertEqual(res["via"], "home_assistant")
            self.assertEqual(res["ps5_entity_id"], "media_player.ps5_712")
            self.assertEqual(res["tv_entity_id"], "media_player.samsung_the_frame_65")
            self.assertEqual(res["tv_remote_entity_id"], "remote.samsung_the_frame_65")
            self.assertTrue(mock_call_service.called)

            # Google Cast YouTube casting via Home Assistant
            cast_res = controller.cast_to_tv("Severance trailer", app="youtube", target_device="living_room_tv")
            self.assertTrue(cast_res["success"])
            self.assertEqual(cast_res["via"], "home_assistant_cast")
            self.assertEqual(cast_res["entity_id"], "media_player.living_room_tv")
            self.assertEqual(cast_res["video_id"], "dQw4w9WgXcQ")

            # Live playback control via Home Assistant
            ctrl_res = controller.control_media("pause")
            self.assertTrue(ctrl_res["success"])

        for p in (tmp_ha_cfg, tmp_ps5_cfg):
            if os.path.exists(p):
                os.remove(p)

    def test_mobile_session_manager_and_telegram_bot(self):
        from telegram_bot import AgentSessionManager, TelegramBotRunner
        from agent import Message

        mgr = AgentSessionManager()
        with patch("telegram_bot.run_agent") as mock_run:
            def fake_run(messages, tools, max_turns=8):
                return messages + [
                    Message(
                        role="assistant",
                        content="",
                        tool_calls=[{"function": {"name": "search_streaming_catalog", "arguments": {"query": "Severance"}}}]
                    ),
                    Message(role="assistant", content="Severance is streaming on Apple TV+.")
                ]
            mock_run.side_effect = fake_run

            res = mgr.send_message("phone_test", "Where can I stream Severance?")
            self.assertTrue(res["success"])
            self.assertIn("Apple TV+", res["reply"])
            self.assertEqual(len(res["tool_calls"]), 1)
            self.assertEqual(res["tool_calls"][0]["name"], "search_streaming_catalog")

            hist = mgr.get_history("phone_test")
            self.assertEqual(len(hist), 2)

            mgr.reset_session("phone_test")
            self.assertEqual(len(mgr.get_history("phone_test")), 0)

        tmp_mob_cfg = tempfile.mktemp()
        bot = TelegramBotRunner(
            bot_token="123456:ABCDEF",
            allowed_chat_ids=[],
            session_manager=mgr,
            config_path=tmp_mob_cfg,
        )
        with patch.object(bot, "send_text") as mock_send_text, \
             patch.object(bot, "send_typing"), \
             patch("telegram_bot.run_agent") as mock_run2:
            mock_run2.side_effect = lambda msgs, tools, max_turns=8: msgs + [
                Message(role="assistant", content="Turned off all lights.")
            ]
            # First user auto-binds as owner
            bot.handle_message({
                "chat": {"id": 999111},
                "from": {"username": "etai"},
                "text": "Turn off all lights",
            })
            self.assertEqual(bot.allowed_chat_ids, [999111])
            self.assertTrue(mock_send_text.called)

            # Different chat_id is blocked
            bot.handle_message({
                "chat": {"id": 888222},
                "from": {"username": "stranger"},
                "text": "Turn on lights",
            })
            last_sent = mock_send_text.call_args[0][1]
            self.assertIn("Unauthorized", last_sent)

        if os.path.exists(tmp_mob_cfg):
            os.remove(tmp_mob_cfg)


if __name__ == "__main__":
    unittest.main()
