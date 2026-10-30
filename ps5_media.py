import os
import re
import ssl
import json
import time
import base64
import socket
import struct
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Tuple
import httpx
from home_assistant import HomeAssistantClient, HA_CONFIG_FILE

PS5_TV_CONFIG_FILE = "ps5_tv_config.json"
PS5_DDP_PORT = 9302
PS5_DDP_VERSION = "00030010"

# ============================================================================
# 1. Enumerated Streaming Apps Registry (PS5 + Samsung Frame TV)
# ============================================================================

STREAMING_APPS: Dict[str, Dict[str, Any]] = {
    "peacock": {
        "id": "peacock",
        "name": "Peacock",
        "ps5_title_id": "PPSA04920",
        "samsung_app_id": "3202006020991",
        "aliases": ["peacock", "peacock tv", "peacock premium", "nbc", "nbcuniversal"],
        "justwatch_packages": ["pck", "pct"],
        "justwatch_technical_names": ["peacocktv", "peacocktvpremium", "peacock"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "qwerty_row",
        "description": "NBCUniversal streaming (The Office, Parks & Rec, Poker Face, Premier League, Traitors)"
    },
    "netflix": {
        "id": "netflix",
        "name": "Netflix",
        "ps5_title_id": "PPSA01648",
        "samsung_app_id": "3201907018807",
        "samsung_legacy_app_id": "11101200001",
        "aliases": ["netflix", "nf"],
        "justwatch_packages": ["nfx", "nfk"],
        "justwatch_technical_names": ["netflix", "netflixbasicwithads", "netflixstandardwithads"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "grid_6col",
        "description": "Netflix originals, films, series, stand-up comedy, and documentaries"
    },
    "youtube": {
        "id": "youtube",
        "name": "YouTube",
        "ps5_title_id": "PPSA01650",
        "samsung_app_id": "111299001912",
        "aliases": ["youtube", "yt", "you tube", "youtube premium", "youtube tv"],
        "justwatch_packages": ["yot", "ytr", "ytv", "yfr"],
        "justwatch_technical_names": ["youtube", "youtubered", "youtubetv", "youtubefree"],
        "has_profile_prompt": False,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "row",
        "description": "YouTube videos, creators, live streams, music, and video essays"
    },
    "disney_plus": {
        "id": "disney_plus",
        "name": "Disney+",
        "ps5_title_id": "PPSA08047",
        "samsung_app_id": "3201901017640",
        "aliases": ["disney+", "disney plus", "disney", "disneyplus"],
        "justwatch_packages": ["dnp", "dis", "dsl"],
        "justwatch_technical_names": ["disneyplus"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "grid_6col",
        "description": "Disney, Pixar, Marvel, Star Wars, and National Geographic"
    },
    "hulu": {
        "id": "hulu",
        "name": "Hulu",
        "ps5_title_id": "PPSA01654",
        "samsung_app_id": "3201601007625",
        "aliases": ["hulu"],
        "justwatch_packages": ["hlu", "hul"],
        "justwatch_technical_names": ["hulu"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "grid_6col",
        "description": "FX series, next-day network TV, anime, and Hulu originals (The Bear, Shogun, Only Murders)"
    },
    "max": {
        "id": "max",
        "name": "Max",
        "ps5_title_id": "PPSA04073",
        "samsung_app_id": "3202301029760",
        "aliases": ["max", "hbo", "hbo max", "hbomax"],
        "justwatch_packages": ["mxx", "hbm", "max"],
        "justwatch_technical_names": ["max", "hbomax"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "row",
        "description": "HBO prestige series, Warner Bros. movies, Studio Ghibli, and A24 films"
    },
    "prime_video": {
        "id": "prime_video",
        "name": "Prime Video",
        "ps5_title_id": "PPSA01649",
        "samsung_app_id": "3201910019365",
        "aliases": ["prime", "prime video", "amazon", "amazon prime", "amazon prime video"],
        "justwatch_packages": ["amp", "prv", "amz", "pva"],
        "justwatch_technical_names": ["amazonprime", "amazonprimevideowithads", "amazon"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "qwerty_row",
        "description": "Amazon Originals (Fallout, The Boys, Invincible, Reacher) and movie rentals"
    },
    "apple_tv": {
        "id": "apple_tv",
        "name": "Apple TV+",
        "ps5_title_id": "PPSA01653",
        "samsung_app_id": "3201807016597",
        "aliases": ["apple tv", "apple tv+", "apple tv plus", "apple", "appletv", "appletv+"],
        "justwatch_packages": ["atp", "atu"],
        "justwatch_technical_names": ["appletvplus", "appletv"],
        "has_profile_prompt": False,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "row",
        "description": "Apple Originals (Severance, Ted Lasso, Silo, Slow Horses, Shrinking)"
    },
    "paramount_plus": {
        "id": "paramount_plus",
        "name": "Paramount+",
        "ps5_title_id": "PPSA05004",
        "samsung_app_id": "3201710014981",
        "aliases": ["paramount", "paramount+", "paramount plus", "cbs"],
        "justwatch_packages": ["pmp", "cbs"],
        "justwatch_technical_names": ["paramountplus", "paramountpluswithshowtime", "paramountplusessential"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "grid_6col",
        "description": "Star Trek, Showtime, CBS live sports, and Paramount movies"
    },
    "crunchyroll": {
        "id": "crunchyroll",
        "name": "Crunchyroll",
        "ps5_title_id": "PPSA01655",
        "samsung_app_id": "3201602007865",
        "aliases": ["crunchyroll", "anime"],
        "justwatch_packages": ["cru", "fmn"],
        "justwatch_technical_names": ["crunchyroll"],
        "has_profile_prompt": True,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "qwerty_row",
        "description": "Simulcast anime series and films"
    },
    "plex": {
        "id": "plex",
        "name": "Plex",
        "ps5_title_id": "PPSA01652",
        "samsung_app_id": "3201512006963",
        "aliases": ["plex"],
        "justwatch_packages": ["plx", "pxc"],
        "justwatch_technical_names": ["plex", "plexplayer"],
        "has_profile_prompt": False,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "grid_6col",
        "description": "Personal media server streaming and live channels"
    },
    "spotify": {
        "id": "spotify",
        "name": "Spotify",
        "ps5_title_id": "PPSA01656",
        "samsung_app_id": "3201606009684",
        "aliases": ["spotify", "music"],
        "justwatch_packages": [],
        "justwatch_technical_names": ["spotify"],
        "has_profile_prompt": False,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "row",
        "description": "Music streaming and podcasts on PS5"
    },
    "twitch": {
        "id": "twitch",
        "name": "Twitch",
        "ps5_title_id": "PPSA01651",
        "samsung_app_id": "3202203026841",
        "aliases": ["twitch"],
        "justwatch_packages": ["twc"],
        "justwatch_technical_names": ["twitch"],
        "has_profile_prompt": False,
        "search_nav": ["KEY_LEFT", "KEY_UP", "KEY_ENTER"],
        "keyboard_layout": "row",
        "description": "Live gaming, esports, and creator streams"
    },
}

# Mapping friendly command names to Samsung Tizen / HDMI-CEC Key Codes
HDMI_CEC_KEYS: Dict[str, str] = {
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "enter": "KEY_ENTER",
    "select": "KEY_ENTER",
    "ok": "KEY_ENTER",
    "confirm": "KEY_ENTER",
    "back": "KEY_RETURN",
    "return": "KEY_RETURN",
    "exit": "KEY_EXIT",
    "home": "KEY_HOME",
    "play": "KEY_PLAY",
    "pause": "KEY_PAUSE",
    "stop": "KEY_STOP",
    "fast_forward": "KEY_FF",
    "ff": "KEY_FF",
    "rewind": "KEY_REW",
    "rew": "KEY_REW",
    "volume_up": "KEY_VOLUP",
    "vol_up": "KEY_VOLUP",
    "volume_down": "KEY_VOLDOWN",
    "vol_down": "KEY_VOLDOWN",
    "mute": "KEY_MUTE",
    "source": "KEY_SOURCE",
    "power": "KEY_POWER",
    "menu": "KEY_MENU",
    "info": "KEY_INFO",
}


def resolve_streaming_app(query: str) -> Optional[Dict[str, Any]]:
    """Resolve an app name or alias (e.g. 'Peacock', 'HBO Max', 'Netflix') to its app metadata dict."""
    if not query:
        return None
    norm = query.lower().strip().replace("-", " ").replace("_", " ")
    if norm in STREAMING_APPS:
        return STREAMING_APPS[norm]

    norm_underscore = norm.replace(" ", "_")
    if norm_underscore in STREAMING_APPS:
        return STREAMING_APPS[norm_underscore]

    for app_info in STREAMING_APPS.values():
        if norm == app_info["name"].lower():
            return app_info
        for alias in app_info.get("aliases", []):
            if norm == alias.lower() or alias.lower() in norm or norm in alias.lower():
                return app_info
    return None


# ============================================================================
# 2. Mood-Based Show Recommendation Catalog
# ============================================================================

MOOD_SHOW_CATALOG: List[Dict[str, Any]] = [
    # --- PEACOCK ---
    {
        "title": "The Office",
        "app": "peacock",
        "moods": ["funny", "cozy", "lighthearted", "comfort", "background", "sitcom", "feel-good", "nostalgic"],
        "genre": "Comedy / Mockumentary",
        "vibe": "Hilarious, endlessly rewatchable workplace comfort comedy at Dunder Mifflin."
    },
    {
        "title": "Parks and Recreation",
        "app": "peacock",
        "moods": ["funny", "upbeat", "feel-good", "cozy", "lighthearted", "comfort", "sitcom"],
        "genre": "Comedy",
        "vibe": "Optimistic, heartwarming, and laugh-out-loud funny small-town government antics."
    },
    {
        "title": "Brooklyn Nine-Nine",
        "app": "peacock",
        "moods": ["funny", "lighthearted", "upbeat", "background", "sitcom", "feel-good", "fast-paced"],
        "genre": "Comedy",
        "vibe": "Fast-paced, ensemble precinct comedy with great chemistry and cold opens."
    },
    {
        "title": "Poker Face",
        "app": "peacock",
        "moods": ["mystery", "clever", "fun", "thriller", "crime", "bingeable", "date_night"],
        "genre": "Mystery / Comedy-Drama",
        "vibe": "Rian Johnson's 'howcatchem' road-trip mystery starring Natasha Lyonne."
    },
    {
        "title": "The Traitors",
        "app": "peacock",
        "moods": ["suspense", "fun", "drama", "bingeable", "date_night", "reality", "thriller"],
        "genre": "Reality Competition / Mystery",
        "vibe": "Campy, high-stakes Scottish castle murder-mystery game hosted by Alan Cumming."
    },
    {
        "title": "30 Rock",
        "app": "peacock",
        "moods": ["funny", "witty", "clever", "lighthearted", "sitcom", "late_night"],
        "genre": "Satire / Sitcom",
        "vibe": "Rapid-fire jokes and absurd behind-the-scenes chaos at a sketch comedy show."
    },
    {
        "title": "The Day of the Jackal",
        "app": "peacock",
        "moods": ["thriller", "suspense", "action", "intense", "drama", "bingeable"],
        "genre": "Espionage Thriller",
        "vibe": "Sleek, tense cat-and-mouse European spy thriller starring Eddie Redmayne."
    },

    # --- NETFLIX ---
    {
        "title": "I Think You Should Leave with Tim Robinson",
        "app": "netflix",
        "moods": ["funny", "absurd", "late_night", "short", "lighthearted", "chaotic"],
        "genre": "Sketch Comedy",
        "vibe": "Unhinged, quotable 15-minute bursts of surreal cringe comedy."
    },
    {
        "title": "Dark",
        "app": "netflix",
        "moods": ["mind-bending", "sci-fi", "dark", "mystery", "thriller", "intense", "cerebral"],
        "genre": "Sci-Fi Mystery",
        "vibe": "Intricate, atmospheric puzzle-box time travel mystery."
    },
    {
        "title": "Black Mirror",
        "app": "netflix",
        "moods": ["mind-bending", "sci-fi", "dark", "thriller", "thought-provoking", "late_night"],
        "genre": "Sci-Fi Anthology",
        "vibe": "Standalone high-concept tech-paranoia stories—great when you want just one episode."
    },
    {
        "title": "Beef",
        "app": "netflix",
        "moods": ["intense", "dark_comedy", "drama", "bingeable", "thriller", "date_night"],
        "genre": "Dark Comedy / Drama",
        "vibe": "A road-rage incident spirals into an all-consuming, darkly hilarious feud."
    },
    {
        "title": "The Great British Baking Show",
        "app": "netflix",
        "moods": ["cozy", "relaxing", "feel-good", "comfort", "background", "lighthearted", "calm"],
        "genre": "Culinary Reality",
        "vibe": "Zero-stress, wholesome tent baking in the English countryside."
    },
    {
        "title": "Arcane",
        "app": "netflix",
        "moods": ["action", "animated", "epic", "sci-fi", "fantasy", "intense", "bingeable"],
        "genre": "Animated Action / Steampunk",
        "vibe": "Stunningly animated, emotionally charged conflict between twin cities Piltover and Zaun."
    },
    {
        "title": "The Night Agent",
        "app": "netflix",
        "moods": ["action", "thriller", "suspense", "fast-paced", "bingeable"],
        "genre": "Action Thriller",
        "vibe": "Propulsive White House conspiracy thriller that hooks you in the first 10 minutes."
    },

    # --- MAX (HBO) ---
    {
        "title": "Succession",
        "app": "max",
        "moods": ["drama", "witty", "dark_comedy", "intense", "prestige", "bingeable"],
        "genre": "Satirical Drama",
        "vibe": "Razor-sharp boardroom warfare and darkly hilarious family dysfunction."
    },
    {
        "title": "The White Lotus",
        "app": "max",
        "moods": ["dark_comedy", "mystery", "drama", "date_night", "bingeable", "satire"],
        "genre": "Satirical Mystery-Drama",
        "vibe": "Sun-soaked luxury resort mystery where guests' and staff's lives unravel."
    },
    {
        "title": "The Last of Us",
        "app": "max",
        "moods": ["intense", "emotional", "action", "thriller", "sci-fi", "dark", "prestige"],
        "genre": "Post-Apocalyptic Drama",
        "vibe": "Gripping, cinematic survival journey across a reclaimed America."
    },
    {
        "title": "Curb Your Enthusiasm",
        "app": "max",
        "moods": ["funny", "lighthearted", "sitcom", "witty", "late_night", "comfort"],
        "genre": "Comedy",
        "vibe": "Larry David turning minor everyday social grievances into catastrophic arguments."
    },
    {
        "title": "True Detective",
        "app": "max",
        "moods": ["dark", "crime", "mystery", "thriller", "moody", "cerebral"],
        "genre": "Crime Anthology",
        "vibe": "Brooding, atmospheric detective investigations into eerie crimes."
    },

    # --- HULU ---
    {
        "title": "The Bear",
        "app": "hulu",
        "moods": ["intense", "emotional", "drama", "bingeable", "fast-paced", "prestige"],
        "genre": "Comedy-Drama",
        "vibe": "Adrenaline-fueled, deeply human kitchen transformation in Chicago."
    },
    {
        "title": "Only Murders in the Building",
        "app": "hulu",
        "moods": ["cozy", "funny", "mystery", "lighthearted", "date_night", "feel-good"],
        "genre": "Mystery Comedy",
        "vibe": "Warm, witty New York apartment whodunit with Steve Martin, Martin Short, and Selena Gomez."
    },
    {
        "title": "Shogun",
        "app": "hulu",
        "moods": ["epic", "drama", "intense", "action", "prestige", "historical"],
        "genre": "Historical Drama",
        "vibe": "Masterful, sweeping political chess match in feudal Japan."
    },
    {
        "title": "What We Do in the Shadows",
        "app": "hulu",
        "moods": ["funny", "absurd", "lighthearted", "late_night", "cozy", "sitcom"],
        "genre": "Supernatural Mockumentary",
        "vibe": "Centuries-old Staten Island vampire roommates bickering over chores."
    },

    # --- APPLE TV+ ---
    {
        "title": "Severance",
        "app": "apple_tv",
        "moods": ["mind-bending", "thriller", "sci-fi", "mystery", "cerebral", "suspense", "bingeable"],
        "genre": "Sci-Fi Thriller",
        "vibe": "Eerie, captivating workplace mystery where employees' work and home memories are surgically split."
    },
    {
        "title": "Ted Lasso",
        "app": "apple_tv",
        "moods": ["feel-good", "cozy", "funny", "upbeat", "lighthearted", "comfort", "wholesome"],
        "genre": "Sports Comedy-Drama",
        "vibe": "Pure optimism and kindness as an American football coach leads an English soccer club."
    },
    {
        "title": "Slow Horses",
        "app": "apple_tv",
        "moods": ["thriller", "witty", "suspense", "crime", "action", "bingeable"],
        "genre": "Spy Thriller",
        "vibe": "Gary Oldman leads a house of disgraced MI5 agents who keep stumbling into real plots."
    },
    {
        "title": "Shrinking",
        "app": "apple_tv",
        "moods": ["feel-good", "funny", "emotional", "lighthearted", "cozy", "date_night"],
        "genre": "Comedy-Drama",
        "vibe": "Warm, funny ensemble series starring Jason Segel and Harrison Ford as therapists."
    },

    # --- PRIME VIDEO ---
    {
        "title": "Fallout",
        "app": "prime_video",
        "moods": ["sci-fi", "action", "fun", "dark_comedy", "epic", "bingeable"],
        "genre": "Sci-Fi Action",
        "vibe": "Wild, darkly funny retro-futuristic wasteland adventure."
    },
    {
        "title": "Clarkson's Farm",
        "app": "prime_video",
        "moods": ["cozy", "funny", "relaxing", "documentary", "feel-good", "lighthearted"],
        "genre": "Docuseries / Comedy",
        "vibe": "Surprisingly heartfelt and hilarious chronicle of running a farm in the Cotswolds."
    },
    {
        "title": "The Boys",
        "app": "prime_video",
        "moods": ["action", "dark_comedy", "intense", "chaotic", "thriller"],
        "genre": "Satirical Superhero Action",
        "vibe": "Irreverent, high-octane takedown of corrupt corporate superheroes."
    },

    # --- DISNEY+ ---
    {
        "title": "Andor",
        "app": "disney_plus",
        "moods": ["sci-fi", "thriller", "intense", "drama", "prestige", "suspense"],
        "genre": "Sci-Fi Spy Thriller",
        "vibe": "Grounded, tense rebellion espionage thriller set in the Star Wars universe."
    },
    {
        "title": "The Mandalorian",
        "app": "disney_plus",
        "moods": ["action", "sci-fi", "fun", "adventure", "cozy", "nostalgic"],
        "genre": "Space Western",
        "vibe": "Episodic galactic bounty-hunting adventures with Din Djarin and Grogu."
    },
]

MOOD_SYNONYMS: Dict[str, List[str]] = {
    "funny": ["funny", "comedy", "laugh", "hilarious", "humorous", "sitcom", "witty", "absurd"],
    "cozy": ["cozy", "comfort", "relaxing", "chill", "warm", "wholesome", "calm", "easy", "unwind"],
    "lighthearted": ["lighthearted", "light", "feel-good", "upbeat", "fun", "casual", "easygoing"],
    "thriller": ["thriller", "suspense", "tense", "edge", "gripping", "intense", "fast-paced"],
    "mind-bending": ["mind-bending", "trippy", "cerebral", "twist", "puzzle", "sci-fi", "smart", "thought-provoking"],
    "dark": ["dark", "gritty", "moody", "bleak", "serious", "crime", "noir"],
    "action": ["action", "exciting", "adventure", "epic", "adrenaline"],
    "date_night": ["date_night", "date", "couple", "together", "mystery", "bingeable"],
    "background": ["background", "second screen", "casual", "sitcom", "comfort"],
    "late_night": ["late_night", "late", "short", "absurd", "weird"],
}

# Mapping mood/genre keywords to JustWatch 3-letter genre shortNames
JUSTWATCH_GENRE_MAP: Dict[str, List[str]] = {
    "comedy": ["cmy"],
    "funny": ["cmy"],
    "sitcom": ["cmy"],
    "lighthearted": ["cmy"],
    "witty": ["cmy"],
    "absurd": ["cmy"],
    "cozy": ["cmy", "fml", "rly"],
    "comfort": ["cmy", "fml"],
    "feel-good": ["cmy", "fml"],
    "wholesome": ["fml", "cmy"],
    "family": ["fml"],
    "drama": ["drm"],
    "emotional": ["drm"],
    "prestige": ["drm"],
    "thriller": ["trl"],
    "suspense": ["trl"],
    "mystery": ["trl", "crm"],
    "intense": ["trl", "drm"],
    "crime": ["crm", "trl"],
    "dark": ["trl", "crm", "drm"],
    "dark_comedy": ["cmy", "drm"],
    "sci-fi": ["scf"],
    "scifi": ["scf"],
    "science fiction": ["scf"],
    "mind-bending": ["scf", "trl"],
    "cerebral": ["scf", "trl", "drm"],
    "action": ["act"],
    "adventure": ["act", "fnt"],
    "epic": ["act", "scf", "fnt", "drm"],
    "fantasy": ["fnt"],
    "horror": ["hrr"],
    "scary": ["hrr", "trl"],
    "spooky": ["hrr"],
    "romance": ["rma"],
    "romantic": ["rma"],
    "date_night": ["trl", "cmy", "rma", "drm"],
    "animation": ["ani"],
    "animated": ["ani"],
    "anime": ["ani"],
    "documentary": ["doc"],
    "relaxing": ["doc", "rly", "cmy"],
    "reality": ["rly"],
    "background": ["cmy", "rly"],
    "history": ["hst"],
    "historical": ["hst", "drm"],
    "western": ["wsn"],
    "music": ["msc"],
    "sport": ["spt"],
    "sports": ["spt"],
}

JUSTWATCH_GENRE_LABELS: Dict[str, str] = {
    "act": "Action & Adventure",
    "ani": "Animation",
    "cmy": "Comedy",
    "com": "Comedy",
    "crm": "Crime",
    "doc": "Documentary",
    "drm": "Drama",
    "eur": "Made in Europe",
    "fml": "Kids & Family",
    "fnt": "Fantasy",
    "hst": "History",
    "hrr": "Horror",
    "hor": "Horror",
    "msc": "Music & Musical",
    "rma": "Romance",
    "scf": "Sci-Fi",
    "spt": "Sports",
    "trl": "Mystery & Thriller",
    "thr": "Mystery & Thriller",
    "war": "War & Military",
    "wsn": "Western",
    "rly": "Reality TV",
}


# ============================================================================
# 2b. JustWatch Live Streaming Catalog Client (GraphQL API)
# ============================================================================

JUSTWATCH_GRAPHQL_URL = "https://apis.justwatch.com/graphql"

JUSTWATCH_POPULAR_AND_SEARCH_QUERY = """
query GetPopularTitles(
  $country: Country! = US
  $language: Language! = en
  $first: Int! = 10
  $platform: Platform! = WEB
  $filter: TitleFilter
) {
  popularTitles(
    country: $country
    filter: $filter
    first: $first
    sortBy: POPULAR
    sortRandomSeed: 0
  ) {
    edges {
      node {
        __typename
        id
        objectId
        objectType
        content(country: $country, language: $language) {
          title
          originalReleaseYear
          shortDescription
          fullPath
          genres {
            shortName
          }
          scoring {
            imdbScore
            tmdbScore
          }
        }
        offers(country: $country, platform: $platform) {
          monetizationType
          presentationType
          standardWebURL
          package {
            id
            packageId
            clearName
            technicalName
            shortName
          }
        }
      }
    }
  }
}
"""

JUSTWATCH_PACKAGES_QUERY = """
query GetPackages($country: Country! = US, $platform: Platform! = WEB) {
  packages(country: $country, platform: $platform) {
    shortName
    clearName
    technicalName
  }
}
"""


class JustWatchClient:
    """Zero-API-key client for querying JustWatch's live streaming catalog and mapping offers to PS5 apps."""

    def __init__(self, endpoint: str = JUSTWATCH_GRAPHQL_URL, country: str = "US", language: str = "en"):
        self.endpoint = endpoint
        self.country = country.upper()
        self.language = language.lower()
        self._cached_packages: Dict[str, List[Dict[str, Any]]] = {}

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) EtaiAgent/1.0",
        }

    @staticmethod
    def map_package_to_app(package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map a JustWatch provider `package` dict to a supported PS5 `STREAMING_APPS` entry."""
        if not package:
            return None
        short_name = str(package.get("shortName") or "").lower().strip()
        tech_name = str(package.get("technicalName") or "").lower().strip()
        clear_name = str(package.get("clearName") or "").lower().strip()

        # Skip third-party channel add-ons (e.g. 'Max Amazon Channel', 'Paramount+ Apple TV Channel', 'Peacock Roku')
        addon_markers = (
            "amazon channel", "apple tv channel", "roku", "fubotv", "directv",
            "spectrum", "plex channel", "amc+", "mgm+", "starz",
        )
        if any(marker in clear_name for marker in addon_markers) or "amazonchannel" in tech_name or "appletvchannel" in tech_name or "rokuchannel" in tech_name:
            return None

        # 1. Exact match on JustWatch package shortName or technicalName
        for app_info in STREAMING_APPS.values():
            if short_name and short_name in [p.lower() for p in app_info.get("justwatch_packages", [])]:
                return app_info
            if tech_name and tech_name in [t.lower() for t in app_info.get("justwatch_technical_names", [])]:
                return app_info

        # 2. Match via clearName aliases
        if clear_name:
            return resolve_streaming_app(clear_name)
        return None

    def get_packages(self, country: Optional[str] = None, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """Fetch and cache JustWatch provider packages for a country."""
        loc = (country or self.country).upper()
        if loc in self._cached_packages:
            return self._cached_packages[loc]

        try:
            r = httpx.post(
                self.endpoint,
                headers=self._headers(),
                json={
                    "operationName": "GetPackages",
                    "variables": {"country": loc, "platform": "WEB"},
                    "query": JUSTWATCH_PACKAGES_QUERY,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            pkgs = r.json().get("data", {}).get("packages") or []
            if isinstance(pkgs, list) and pkgs:
                self._cached_packages[loc] = pkgs
                return pkgs
        except Exception:
            pass
        return []

    def get_app_package_codes(self, app_id: str, country: Optional[str] = None) -> List[str]:
        """Return JustWatch `shortName` package codes for a given PS5 streaming app ID."""
        app_info = STREAMING_APPS.get(app_id) or resolve_streaming_app(app_id)
        if not app_info:
            return []
        canonical_id = app_info["id"]
        codes = list(app_info.get("justwatch_packages", []))

        # Also check dynamically discovered packages if cached/available
        for pkg in self._cached_packages.get((country or self.country).upper(), []):
            mapped = self.map_package_to_app(pkg)
            if mapped and mapped["id"] == canonical_id:
                sn = pkg.get("shortName")
                if sn and sn not in codes:
                    codes.append(sn)
        return codes

    @staticmethod
    def resolve_genre_codes(mood_or_genre: str) -> List[str]:
        """Convert a mood, vibe, or genre string into JustWatch 3-letter genre codes."""
        if not mood_or_genre or not mood_or_genre.strip():
            return []
        norm = mood_or_genre.lower().strip()
        codes: List[str] = []

        if norm in JUSTWATCH_GENRE_LABELS and norm not in codes:
            codes.append(norm)

        for phrase, g_list in JUSTWATCH_GENRE_MAP.items():
            if phrase in norm:
                for g in g_list:
                    if g not in codes:
                        codes.append(g)

        for word in re.findall(r"[\w-]+", norm):
            if word in JUSTWATCH_GENRE_LABELS and word not in codes:
                codes.append(word)
            for g in JUSTWATCH_GENRE_MAP.get(word, []):
                if g not in codes:
                    codes.append(g)

        return codes

    def parse_title_node(self, node: Dict[str, Any], preferred_app_id: Optional[str] = None) -> Dict[str, Any]:
        """Parse a JustWatch GraphQL title node into a structured catalog item with PS5 playback instructions."""
        content = node.get("content") or {}
        title = content.get("title") or "Unknown Title"
        obj_type = node.get("objectType") or "SHOW"
        year = content.get("originalReleaseYear")
        desc = content.get("shortDescription") or ""
        full_path = content.get("fullPath") or ""
        scoring = content.get("scoring") or {}
        raw_genres = content.get("genres") or []
        genre_names = [
            JUSTWATCH_GENRE_LABELS.get(g.get("shortName", ""), g.get("shortName", ""))
            for g in raw_genres
            if isinstance(g, dict) and g.get("shortName")
        ]

        offers = node.get("offers") or []
        ps5_streaming_apps: List[Dict[str, Any]] = []
        seen_app_ids = set()
        other_offers: List[Dict[str, str]] = []

        # Prioritize subscription/free streaming offers ('FLATRATE', 'FREE', 'ADS')
        streaming_monetization = {"FLATRATE", "FREE", "ADS"}
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            m_type = str(offer.get("monetizationType") or "").upper()
            pkg = offer.get("package") or {}
            mapped_app = self.map_package_to_app(pkg)

            if m_type in streaming_monetization and mapped_app:
                app_id = mapped_app["id"]
                if app_id not in seen_app_ids:
                    seen_app_ids.add(app_id)
                    ps5_streaming_apps.append({
                        "app_id": app_id,
                        "app_name": mapped_app["name"],
                        "ps5_title_id": mapped_app["ps5_title_id"],
                        "monetization": m_type.lower(),
                        "provider_name": pkg.get("clearName") or mapped_app["name"],
                        "stream_url": offer.get("standardWebURL"),
                    })
            elif pkg.get("clearName"):
                entry = {
                    "provider": str(pkg.get("clearName")),
                    "monetization": m_type.lower(),
                }
                if mapped_app:
                    entry["ps5_app_id"] = mapped_app["id"]
                if entry not in other_offers and len(other_offers) < 5:
                    other_offers.append(entry)

        # Choose the primary PS5 app to launch (preferring `preferred_app_id` if available)
        primary_app: Optional[Dict[str, Any]] = None
        if preferred_app_id:
            for app_entry in ps5_streaming_apps:
                if app_entry["app_id"] == preferred_app_id:
                    primary_app = app_entry
                    break
        if not primary_app and ps5_streaming_apps:
            primary_app = ps5_streaming_apps[0]

        how_to_play = (
            f"Call watch_on_ps5(app='{primary_app['app_id']}', show_title={json.dumps(title)})"
            if primary_app
            else f"Not currently on a flatrate subscription PS5 app (check other_offers or search on YouTube/Prime Video)."
        )

        return {
            "title": title,
            "type": obj_type,
            "release_year": year,
            "genres": genre_names,
            "imdb_score": scoring.get("imdbScore"),
            "tmdb_score": scoring.get("tmdbScore"),
            "description": desc,
            "justwatch_url": f"https://www.justwatch.com{full_path}" if full_path else None,
            "primary_ps5_app": primary_app["app_id"] if primary_app else None,
            "primary_ps5_app_name": primary_app["app_name"] if primary_app else None,
            "available_on_ps5_apps": ps5_streaming_apps,
            "other_offers": other_offers if not ps5_streaming_apps else [],
            "how_to_play": how_to_play,
        }

    def search_catalog(
        self,
        query: str = "",
        app: str = "",
        mood_or_genre: str = "",
        content_type: str = "",
        monetization: str = "flatrate",
        limit: int = 8,
        country: Optional[str] = None,
        timeout: float = 6.0,
    ) -> Dict[str, Any]:
        """Query JustWatch's live streaming catalog by title search, streaming app, mood/genre, or content type."""
        loc = (country or self.country).upper()
        limit = max(1, min(25, int(limit)))

        resolved_app = resolve_streaming_app(app) if app and app.strip() else None
        preferred_app_id = resolved_app["id"] if resolved_app else None

        # Build JustWatch TitleFilter
        title_filter: Dict[str, Any] = {}
        if query and query.strip():
            title_filter["searchQuery"] = query.strip()

        if resolved_app:
            pkg_codes = self.get_app_package_codes(resolved_app["id"], country=loc)
            if pkg_codes:
                title_filter["packages"] = pkg_codes
        else:
            # When browsing without a specific app filter and without a specific title query,
            # restrict to our supported PS5 streaming apps so every recommendation is playable
            if not query.strip():
                all_ps5_pkgs: List[str] = []
                for a_info in STREAMING_APPS.values():
                    for code in a_info.get("justwatch_packages", []):
                        if code not in all_ps5_pkgs:
                            all_ps5_pkgs.append(code)
                if all_ps5_pkgs:
                    title_filter["packages"] = all_ps5_pkgs

        genre_codes = self.resolve_genre_codes(mood_or_genre)
        if genre_codes:
            title_filter["genres"] = genre_codes

        norm_type = content_type.lower().strip()
        if norm_type in ("show", "shows", "tv", "series", "tv_show"):
            title_filter["objectTypes"] = ["SHOW"]
        elif norm_type in ("movie", "movies", "film", "films"):
            title_filter["objectTypes"] = ["MOVIE"]
        else:
            title_filter["objectTypes"] = ["SHOW", "MOVIE"]

        norm_mon = monetization.lower().strip()
        if norm_mon in ("flatrate", "subscription", "stream", "streaming"):
            title_filter["monetizationTypes"] = ["FLATRATE", "FREE", "ADS"]

        fetch_count = max(limit * 2, 12)
        try:
            r = httpx.post(
                self.endpoint,
                headers=self._headers(),
                json={
                    "operationName": "GetPopularTitles",
                    "variables": {
                        "country": loc,
                        "language": self.language,
                        "first": fetch_count,
                        "platform": "WEB",
                        "filter": title_filter,
                    },
                    "query": JUSTWATCH_POPULAR_AND_SEARCH_QUERY,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            payload = r.json()
            edges = (
                payload.get("data", {})
                .get("popularTitles", {})
                .get("edges")
                or []
            )

            results: List[Dict[str, Any]] = []
            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not node:
                    continue
                parsed = self.parse_title_node(node, preferred_app_id=preferred_app_id)
                # When filtering by a specific PS5 app, keep titles available on that app
                if preferred_app_id and parsed["primary_ps5_app"] != preferred_app_id:
                    continue
                results.append(parsed)
                if len(results) >= limit:
                    break

            return {
                "success": True,
                "source": "justwatch_live_graphql",
                "country": loc,
                "query": query or None,
                "filtered_app": resolved_app["name"] if resolved_app else "All PS5 Streaming Apps",
                "filtered_genres": [JUSTWATCH_GENRE_LABELS.get(c, c) for c in genre_codes],
                "count": len(results),
                "results": results,
            }
        except Exception as e:
            return {
                "success": False,
                "source": "justwatch_live_graphql",
                "error": f"JustWatch catalog query failed: {str(e)}",
                "query": query or None,
                "filtered_app": resolved_app["name"] if resolved_app else "All PS5 Streaming Apps",
                "count": 0,
                "results": [],
            }

    def find_streaming_app_for_title(
        self,
        title: str,
        preferred_app: str = "",
        country: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up which PS5 streaming app currently streams a given show or movie title."""
        if not title or not title.strip():
            return None
        res = self.search_catalog(
            query=title.strip(),
            app=preferred_app,
            monetization="any",
            limit=5,
            country=country,
        )
        for item in res.get("results", []):
            if item.get("primary_ps5_app"):
                app_info = STREAMING_APPS.get(item["primary_ps5_app"])
                if app_info:
                    return {
                        "app_info": app_info,
                        "matched_title": item["title"],
                        "type": item["type"],
                        "release_year": item["release_year"],
                        "available_on_ps5_apps": item["available_on_ps5_apps"],
                    }
        return None



# ============================================================================
# 3. Network Helpers & Local Subnet Detection
# ============================================================================

def get_local_subnet_prefix() -> str:
    """Detect the active LAN IPv4 prefix (e.g. '192.168.86.')."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.86.1", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}."
    except Exception:
        pass
    return "192.168.86."


def send_wake_on_lan(mac_address: str, broadcast_ip: str = "255.255.255.255") -> bool:
    """Send a Wake-on-LAN magic packet for a target MAC address."""
    if not mac_address:
        return False
    clean_mac = re.sub(r"[^0-9A-Fa-f]", "", mac_address)
    if len(clean_mac) != 12:
        return False
    try:
        mac_bytes = bytes.fromhex(clean_mac)
        magic_packet = b"\xff" * 6 + mac_bytes * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic_packet, (broadcast_ip, 9))
            subnet_bcast = f"{get_local_subnet_prefix()}255"
            sock.sendto(magic_packet, (subnet_bcast, 9))
        return True
    except Exception:
        return False


# ============================================================================
# 4. Pure-Python RFC 6455 TLS WebSocket Client for Samsung Tizen (Port 8002)
# ============================================================================

class MinimalTLSWebSocket:
    """Zero-dependency RFC 6455 WebSocket client supporting Samsung TV TLS Port 8002."""

    def __init__(self, host: str, port: int, path: str, use_ssl: bool = True, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.path = path
        self.use_ssl = use_ssl
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

    def _open_raw_socket(self) -> socket.socket:
        """Open a TCP socket directly, or via HTTP CONNECT tunnel if HTTP_PROXY/HTTPS_PROXY is set (e.g. inside srt)."""
        proxy_url = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        if proxy_url:
            clean = re.sub(r"^https?://", "", proxy_url.strip()).rstrip("/")
            if ":" in clean:
                p_host, p_port_str = clean.rsplit(":", 1)
                p_port = int(p_port_str)
                # Connect to 127.0.0.1 explicitly so Seatbelt localhost AF_INET rule matches
                if p_host.lower() == "localhost":
                    p_host = "127.0.0.1"
                sock = socket.create_connection((p_host, p_port), timeout=self.timeout)
                connect_req = (
                    f"CONNECT {self.host}:{self.port} HTTP/1.1\r\n"
                    f"Host: {self.host}:{self.port}\r\n\r\n"
                )
                sock.sendall(connect_req.encode("utf-8"))
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    resp += chunk
                status_line = resp.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
                if "200" not in status_line:
                    sock.close()
                    raise ConnectionError(f"Proxy CONNECT to {self.host}:{self.port} failed: {status_line}")
                return sock

        return socket.create_connection((self.host, self.port), timeout=self.timeout)

    def connect(self) -> None:
        raw_sock = self._open_raw_socket()
        if self.use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
        else:
            self.sock = raw_sock

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode("utf-8"))

        # Read HTTP 101 response headers
        response_data = b""
        while b"\r\n\r\n" not in response_data:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            response_data += chunk
        header_line = response_data.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
        if "101" not in header_line:
            raise ConnectionError(f"WebSocket handshake failed: {header_line}")

    def recv_json(self) -> Dict[str, Any]:
        if not self.sock:
            raise ConnectionError("Socket not connected")
        header = self._recv_exact(2)
        if not header:
            return {}
        b1, b2 = header[0], header[1]
        masked = bool(b2 & 0x80)
        payload_len = b2 & 0x7F
        if payload_len == 126:
            ext = self._recv_exact(2)
            payload_len = struct.unpack("!H", ext)[0]
        elif payload_len == 127:
            ext = self._recv_exact(8)
            payload_len = struct.unpack("!Q", ext)[0]

        mask_key = self._recv_exact(4) if masked else b""
        payload = bytearray(self._recv_exact(payload_len))
        if masked and mask_key:
            for i in range(len(payload)):
                payload[i] ^= mask_key[i % 4]

        text = payload.decode("utf-8", errors="ignore")
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}

    def send_json(self, data: Dict[str, Any]) -> None:
        if not self.sock:
            raise ConnectionError("Socket not connected")
        payload = json.dumps(data).encode("utf-8")
        mask_key = os.urandom(4)
        header = bytearray([0x81])  # FIN + text frame
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))

        header.extend(mask_key)
        masked_payload = bytearray(length)
        for i in range(length):
            masked_payload[i] = payload[i] ^ mask_key[i % 4]

        self.sock.sendall(header + masked_payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n and self.sock:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# ============================================================================
# 5. Samsung Frame TV Controller (WebSocket Port 8002 + REST API + HDMI-CEC)
# ============================================================================

class SamsungFrameClient:
    """Local controller for Samsung The Frame TV (Tizen OS) with HDMI-CEC passthrough."""

    def __init__(
        self,
        tv_ip: Optional[str] = None,
        tv_token: Optional[str] = None,
        tv_mac: Optional[str] = None,
        app_name: str = "EtaiAgent"
    ):
        self.tv_ip = tv_ip
        self.tv_token = tv_token
        self.tv_mac = tv_mac
        self.tv_name = "Samsung Frame TV"
        self.app_name = app_name
        self._session_ws: Optional[MinimalTLSWebSocket] = None

    def open_session(self, pair_timeout: float = 12.0) -> Dict[str, Any]:
        """Establish and verify a persistent TLS WebSocket connection to the Samsung Frame TV before issuing commands."""
        if not self.tv_ip:
            return {"connected": False, "error": "Samsung Frame TV IP is not configured or discovered yet."}

        self.close_session()
        b64_name = base64.b64encode(self.app_name.encode("utf-8")).decode("ascii")
        path = f"/api/v2/channels/samsung.remote.control?name={b64_name}"
        if self.tv_token:
            path += f"&token={self.tv_token}"

        ws = MinimalTLSWebSocket(
            host=self.tv_ip,
            port=8002,
            path=path,
            use_ssl=True,
            timeout=pair_timeout if not self.tv_token else 6.0
        )
        try:
            ws.connect()
            connect_msg = ws.recv_json()
            event = connect_msg.get("event", "")
            if event == "ms.channel.unauthorized":
                ws.close()
                return {
                    "connected": False,
                    "tv_ip": self.tv_ip,
                    "error": "Unauthorized on Samsung Frame TV. Please accept the 'Allow EtaiAgent' popup on your TV screen.",
                }
            new_token = connect_msg.get("data", {}).get("token")
            if new_token:
                self.tv_token = str(new_token)

            time.sleep(0.35)
            self._session_ws = ws
            return {
                "connected": True,
                "tv_ip": self.tv_ip,
                "event": event or "ms.channel.connect",
                "token_saved": bool(self.tv_token),
            }
        except Exception as e:
            ws.close()
            return {
                "connected": False,
                "tv_ip": self.tv_ip,
                "error": f"Failed to establish WebSocket connection to Samsung Frame TV ({self.tv_ip}:8002): {str(e)}"
            }

    def close_session(self) -> None:
        """Close the active persistent WebSocket session if open."""
        if self._session_ws is not None:
            try:
                time.sleep(0.35)
                self._session_ws.close()
            except Exception:
                pass
            self._session_ws = None

    def check_info(self, ip: Optional[str] = None, timeout: float = 0.8) -> Optional[Dict[str, Any]]:
        """Query Samsung Tizen REST API at http://<ip>:8001/api/v2/."""
        target_ip = ip or self.tv_ip
        if not target_ip:
            return None
        try:
            r = httpx.get(f"http://{target_ip}:8001/api/v2/", timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                dev = data.get("device", {})
                if "Samsung" in dev.get("type", "") or "Tizen" in dev.get("OS", "") or "id" in data:
                    return {
                        "ip": target_ip,
                        "name": data.get("name") or dev.get("name") or "Samsung Frame TV",
                        "model": dev.get("modelName", "The Frame"),
                        "mac": dev.get("wifiMac"),
                        "power_state": dev.get("PowerState", "on"),
                        "frame_tv_support": dev.get("FrameTVSupport") == "true",
                        "token_auth_support": dev.get("TokenAuthSupport") == "true",
                    }
        except Exception:
            pass
        return None

    def discover(self, subnet_prefix: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Discover Samsung Frame TV on the local network via SSDP multicast + port 8001 scan."""
        # 1. Check already configured IP first
        if self.tv_ip:
            info = self.check_info(self.tv_ip, timeout=1.0)
            if info:
                self.tv_name = info["name"]
                self.tv_mac = info.get("mac") or self.tv_mac
                return info

        # 2. Try SSDP M-SEARCH multicast
        ssdp_candidates: List[str] = []
        try:
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                "MAN: \"ssdp:discover\"\r\n"
                "MX: 1\r\n"
                "ST: urn:samsung.com:device:RemoteControlReceiver:1\r\n\r\n"
            ).encode("utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
                s.settimeout(0.8)
                s.sendto(msg, ("239.255.255.250", 1900))
                while True:
                    try:
                        _, addr = s.recvfrom(2048)
                        if addr[0] not in ssdp_candidates:
                            ssdp_candidates.append(addr[0])
                    except socket.timeout:
                        break
        except Exception:
            pass

        for ip in ssdp_candidates:
            info = self.check_info(ip, timeout=0.8)
            if info:
                self.tv_ip = ip
                self.tv_name = info["name"]
                self.tv_mac = info.get("mac") or self.tv_mac
                return info

        # 3. Fast concurrent scan of local /24 subnet on port 8001
        prefix = subnet_prefix or get_local_subnet_prefix()
        candidate_ips = [f"{prefix}{i}" for i in range(1, 255)]
        with ThreadPoolExecutor(max_workers=48) as pool:
            futures = {pool.submit(self.check_info, ip, 0.45): ip for ip in candidate_ips}
            for fut in as_completed(futures):
                res = fut.result()
                if res:
                    self.tv_ip = res["ip"]
                    self.tv_name = res["name"]
                    self.tv_mac = res.get("mac") or self.tv_mac
                    return res
        return None

    def ensure_awake(self) -> Dict[str, Any]:
        """Wake Samsung Frame TV via Wake-on-LAN or exit Art Mode if needed."""
        if self.tv_mac:
            send_wake_on_lan(self.tv_mac)
        info = self.check_info()
        if info and info.get("power_state") == "standby":
            self.send_keys(["KEY_POWER"], delay_ms=500)
            return {"status": "waking_from_standby", "tv_ip": self.tv_ip}
        return {"status": "awake", "tv_ip": self.tv_ip, "info": info}

    def send_keys(
        self,
        keys: List[str],
        delay_ms: int = 550,
        pair_timeout: float = 12.0
    ) -> Dict[str, Any]:
        """Send one or more remote keys (forwarded via HDMI-CEC when on PS5 input) over Port 8002."""
        if not self.tv_ip:
            return {"success": False, "error": "Samsung Frame TV IP is not configured or discovered yet."}

        own_session = False
        if self._session_ws is None:
            conn_res = self.open_session(pair_timeout=pair_timeout)
            if not conn_res.get("connected"):
                return {
                    "success": False,
                    "tv_ip": self.tv_ip,
                    "error": conn_res.get("error", "Could not connect to Samsung Frame TV.")
                }
            own_session = True

        try:
            sent_keys: List[str] = []
            for idx, k in enumerate(keys):
                key_code = HDMI_CEC_KEYS.get(k.lower(), k.upper())
                if not key_code.startswith("KEY_"):
                    key_code = f"KEY_{key_code}"
                payload = {
                    "method": "ms.remote.control",
                    "params": {
                        "Cmd": "Click",
                        "DataOfCmd": key_code,
                        "Option": "false",
                        "TypeOfRemote": "SendRemoteKey"
                    }
                }
                self._session_ws.send_json(payload)
                sent_keys.append(key_code)
                if idx < len(keys) - 1 and delay_ms > 0:
                    time.sleep(delay_ms / 1000.0)

            time.sleep(0.35)
            return {
                "success": True,
                "tv_ip": self.tv_ip,
                "token_saved": bool(self.tv_token),
                "sent_keys": sent_keys
            }
        except Exception as e:
            self.close_session()
            return {
                "success": False,
                "tv_ip": self.tv_ip,
                "error": f"Failed to send keys to Samsung Frame TV ({self.tv_ip}): {str(e)}"
            }
        finally:
            if own_session:
                self.close_session()

    def launch_tv_app(self, app_id: str) -> Dict[str, Any]:
        """Launch a native Tizen app on the Samsung Frame TV via REST API or Port 8002 WebSocket."""
        if not self.tv_ip:
            return {"success": False, "error": "Samsung Frame TV IP is not configured."}
        try:
            r = httpx.post(f"http://{self.tv_ip}:8001/api/v2/applications/{app_id}", timeout=3.0)
            if r.status_code in (200, 201):
                return {"success": True, "method": "rest", "app_id": app_id}
        except Exception:
            pass

        # Fallback: Launch via Port 8002 TLS WebSocket ed.apps.launch event
        b64_name = base64.b64encode(self.app_name.encode("utf-8")).decode("ascii")
        path = f"/api/v2/channels/samsung.remote.control?name={b64_name}"
        if self.tv_token:
            path += f"&token={self.tv_token}"
        ws = MinimalTLSWebSocket(host=self.tv_ip, port=8002, path=path, use_ssl=True, timeout=5.0)
        try:
            ws.connect()
            ws.recv_json()
            ws.send_json({
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.apps.launch",
                    "to": "host",
                    "data": {
                        "appId": app_id,
                        "action_type": "DEEP_LINK"
                    }
                }
            })
            return {"success": True, "method": "websocket_ed_apps_launch", "app_id": app_id}
        except Exception as e:
            return {"success": False, "app_id": app_id, "error": str(e)}
        finally:
            ws.close()


# ============================================================================
# 6. PlayStation 5 Controller (UDP 9302 DDP + Playactor CLI)
# ============================================================================

class PS5Client:
    """Local network discovery, wake, standby, and app launcher for PlayStation 5."""

    def __init__(self, ps5_ip: Optional[str] = None, host_name: Optional[str] = None):
        self.ps5_ip = ps5_ip
        self.host_name = host_name
        self.host_id: Optional[str] = None

    @staticmethod
    def parse_ddp_response(raw_text: str, source_ip: str = "") -> Dict[str, Any]:
        """Parse PS5 UDP Port 9302 HTTP-like DDP response packet."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return {}
        status_line = lines[0]
        state = "unknown"
        if "200" in status_line:
            state = "awake"
        elif "620" in status_line:
            state = "standby"

        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        return {
            "ip": source_ip,
            "state": state,
            "status_line": status_line,
            "host_id": headers.get("host-id"),
            "host_name": headers.get("host-name", "PS5"),
            "host_type": headers.get("host-type", "PS5"),
            "running_app_name": headers.get("running-app-name"),
            "running_app_titleid": headers.get("running-app-titleid"),
            "system_version": headers.get("system-version"),
        }

    def query_status(self, ip: Optional[str] = None, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """Query PS5 status directly via UDP Port 9302 DDP packet."""
        target_ip = ip or self.ps5_ip
        if not target_ip:
            return None
        packet = f"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:{PS5_DDP_VERSION}\n\n".encode("utf-8")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(packet, (target_ip, PS5_DDP_PORT))
                data, addr = s.recvfrom(2048)
                parsed = self.parse_ddp_response(data.decode("utf-8", errors="ignore"), addr[0])
                if parsed.get("host_type") or parsed.get("host_id"):
                    self.ps5_ip = addr[0]
                    self.host_name = parsed.get("host_name") or self.host_name
                    self.host_id = parsed.get("host_id") or self.host_id
                    return parsed
        except Exception:
            pass
        return None

    def discover(self, subnet_prefix: Optional[str] = None, timeout: float = 1.2) -> Optional[Dict[str, Any]]:
        """Discover PS5 on LAN via UDP Port 9302 broadcast and subnet sweep."""
        if self.ps5_ip:
            direct = self.query_status(self.ps5_ip, timeout=0.8)
            if direct:
                return direct

        packet = f"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:{PS5_DDP_VERSION}\n\n".encode("utf-8")
        prefix = subnet_prefix or get_local_subnet_prefix()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(timeout)
                # Send to broadcast + subnet broadcast
                s.sendto(packet, ("255.255.255.255", PS5_DDP_PORT))
                s.sendto(packet, (f"{prefix}255", PS5_DDP_PORT))
                # Also spray unicast UDP packets across local /24 in case router blocks UDP broadcast
                for i in range(1, 255):
                    try:
                        s.sendto(packet, (f"{prefix}{i}", PS5_DDP_PORT))
                    except Exception:
                        pass

                data, addr = s.recvfrom(2048)
                parsed = self.parse_ddp_response(data.decode("utf-8", errors="ignore"), addr[0])
                if parsed:
                    self.ps5_ip = addr[0]
                    self.host_name = parsed.get("host_name") or self.host_name
                    self.host_id = parsed.get("host_id") or self.host_id
                    return parsed
        except Exception:
            pass
        return None

    def _get_playactor_cmd(self) -> Optional[List[str]]:
        """Return the command list to invoke playactor if installed locally, in npx cache, or globally."""
        local_bin = os.path.abspath("./node_modules/.bin/playactor")
        if os.path.exists(local_bin):
            return [local_bin]
        global_bin = shutil.which("playactor")
        if global_bin:
            return [global_bin]
        npx_cache_root = os.path.expanduser("~/.npm/_npx")
        try:
            if os.path.exists(npx_cache_root):
                for entry in os.listdir(npx_cache_root):
                    candidate = os.path.join(npx_cache_root, entry, "node_modules", ".bin", "playactor")
                    if os.path.exists(candidate):
                        return [candidate]
        except Exception:
            pass
        npx_bin = shutil.which("npx")
        if npx_bin:
            return [npx_bin, "--yes", "playactor"]
        return None

    def build_playactor_command(self, subcommand: str, extra_args: Optional[List[str]] = None) -> List[str]:
        """Construct the playactor CLI invocation for wake, standby, check, or start-id."""
        base = self._get_playactor_cmd() or ["npx", "playactor"]
        cmd = list(base) + [subcommand]
        if extra_args:
            cmd.extend(extra_args)
        if self.ps5_ip:
            cmd.extend(["--ip", self.ps5_ip])
        elif self.host_name:
            cmd.extend(["--host-name", self.host_name])
        else:
            cmd.append("--ps5")
        return cmd

    def is_playactor_paired(self) -> bool:
        """Check whether playactor credentials have been registered on this machine."""
        cred_paths = [
            os.path.expanduser("~/.config/playactor/credentials.json"),
            os.path.expanduser("~/Library/Preferences/playactor-nodejs/credentials.json"),
        ]
        return any(os.path.exists(p) for p in cred_paths)

    def _run_playactor(self, subcommand: str, extra_args: Optional[List[str]] = None, timeout: int = 12) -> Dict[str, Any]:
        if not self.is_playactor_paired():
            return {
                "success": False,
                "reason": "playactor_not_paired",
                "hint": "Run `npx --yes playactor login --ps5` once in your terminal to pair PSN Remote Play credentials."
            }
        cmd = self.build_playactor_command(subcommand, extra_args=extra_args)
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            if res.returncode == 0:
                return {"success": True, "command": " ".join(cmd), "output": out}
            return {
                "success": False,
                "command": " ".join(cmd),
                "error": err or out or f"playactor exited with code {res.returncode}",
                "hint": "If your PS5 is not yet paired with playactor, run `./node_modules/.bin/playactor login --ps5` in a terminal once."
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "command": " ".join(cmd), "error": f"playactor '{subcommand}' timed out after {timeout}s."}
        except Exception as e:
            return {"success": False, "command": " ".join(cmd), "error": str(e)}

    def wake(self) -> Dict[str, Any]:
        """Wake the PS5 from Rest Mode."""
        status = self.query_status()
        if status and status.get("state") == "awake":
            return {"success": True, "already_awake": True, "status": status}
        return self._run_playactor("wake", ["--no-open-urls", "--no-auth"], timeout=25)

    def standby(self) -> Dict[str, Any]:
        """Put the PS5 into Rest Mode (Standby)."""
        return self._run_playactor("standby", ["--no-open-urls", "--no-auth"], timeout=20)

    def start_title(self, title_id: str) -> Dict[str, Any]:
        """Launch a specific PS5 game or streaming app by its PPSA Title ID."""
        return self._run_playactor("start-id", [title_id], timeout=25)


# ============================================================================
# 7. Unified PS5 + Samsung Frame Media & Mood Controller
# ============================================================================

def build_cec_keyboard_sequence(text: str, layout: str = "row") -> List[str]:
    """Generate a compact directional key macro for in-app search bars via HDMI-CEC.

    Because HDMI-CEC only forwards directional pad + Enter/Back/Play/Pause into PS5 media apps,
    most streaming apps (Netflix, Peacock, YouTube, Hulu, Max) show predictive title suggestions
    after entering just the first 3-5 characters of the show title.
    """
    clean = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    # Strip leading articles ("the ", "a ") to reach unique title characters faster
    for prefix in ("the ", "a ", "an "):
        if clean.startswith(prefix) and len(clean) > len(prefix) + 2:
            clean = clean[len(prefix):]
            break

    target_chars = clean[:5]
    keys: List[str] = []

    if layout == "grid_6col":
        # Standard 6-column A-Z + 0-9 grid (Netflix / Hulu / Disney+)
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 "
        cur_row, cur_col = 0, 0
        for ch in target_chars:
            idx = alphabet.find(ch)
            if idx == -1:
                continue
            r, c = divmod(idx, 6)
            while cur_row < r:
                keys.append("KEY_DOWN")
                cur_row += 1
            while cur_row > r:
                keys.append("KEY_UP")
                cur_row -= 1
            while cur_col < c:
                keys.append("KEY_RIGHT")
                cur_col += 1
            while cur_col > c:
                keys.append("KEY_LEFT")
                cur_col -= 1
            keys.append("KEY_ENTER")
    elif layout == "qwerty_row":
        # 3-row QWERTY keyboard grid (Peacock / Prime Video on PS5)
        rows = [
            "qwertyuiop",
            "asdfghjkl ",
            "zxcvbnm012",
        ]
        cur_row, cur_col = 0, 0
        for ch in target_chars[:4]:
            found = None
            for r_idx, row_str in enumerate(rows):
                c_idx = row_str.find(ch)
                if c_idx != -1:
                    found = (r_idx, c_idx)
                    break
            if not found:
                continue
            r, c = found
            while cur_row < r:
                keys.append("KEY_DOWN")
                cur_row += 1
            while cur_row > r:
                keys.append("KEY_UP")
                cur_row -= 1
            while cur_col < c:
                keys.append("KEY_RIGHT")
                cur_col += 1
            while cur_col > c:
                keys.append("KEY_LEFT")
                cur_col -= 1
            keys.append("KEY_ENTER")
    else:
        # Linear A-Z top-row predictive entry (YouTube / Max)
        alphabet = "abcdefghijklmnopqrstuvwxyz "
        cur_pos = 0
        for ch in target_chars[:4]:
            idx = alphabet.find(ch)
            if idx == -1:
                continue
            while cur_pos < idx:
                keys.append("KEY_RIGHT")
                cur_pos += 1
            while cur_pos > idx:
                keys.append("KEY_LEFT")
                cur_pos -= 1
            keys.append("KEY_ENTER")

    # Move down/right to select the top predictive show tile and press Enter twice (Open -> Play)
    keys.extend(["KEY_DOWN", "KEY_DOWN", "KEY_ENTER", "KEY_ENTER", "KEY_PLAY"])
    return keys


class PS5MediaController:
    """High-level coordinator for Home Assistant + PS5 + Samsung Frame TV (HDMI-CEC) + Mood Discovery."""

    def __init__(self, config_path: str = PS5_TV_CONFIG_FILE, ha_config_path: str = HA_CONFIG_FILE):
        self.config_path = config_path
        self.ps5 = PS5Client()
        self.tv = SamsungFrameClient()
        self.ha = HomeAssistantClient(config_path=ha_config_path)
        self.justwatch = JustWatchClient()
        self._load_config()

    def _load_config(self) -> None:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.ps5.ps5_ip = data.get("ps5_ip") or self.ps5.ps5_ip
                    self.ps5.host_name = data.get("ps5_host_name") or self.ps5.host_name
                    self.ps5.host_id = data.get("ps5_host_id") or self.ps5.host_id
                    self.tv.tv_ip = data.get("tv_ip") or self.tv.tv_ip
                    self.tv.tv_token = data.get("tv_token") or self.tv.tv_token
                    self.tv.tv_mac = data.get("tv_mac") or self.tv.tv_mac
                    self.tv.tv_name = data.get("tv_name") or self.tv.tv_name
            except Exception:
                pass

    def _save_config(self) -> None:
        data = {
            "ps5_ip": self.ps5.ps5_ip,
            "ps5_host_name": self.ps5.host_name,
            "ps5_host_id": self.ps5.host_id,
            "tv_ip": self.tv.tv_ip,
            "tv_token": self.tv.tv_token,
            "tv_mac": self.tv.tv_mac,
            "tv_name": self.tv.tv_name,
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Automatically whitelist discovered PS5 and Samsung Frame TV IPs in srt-settings.json
        srt_file = "srt-settings.json"
        if self.config_path == PS5_TV_CONFIG_FILE and os.path.exists(srt_file):
            try:
                with open(srt_file, "r", encoding="utf-8") as f:
                    srt_data = json.load(f)
                allowed = srt_data.setdefault("network", {}).setdefault("allowedDomains", [])
                updated = False
                for host in (self.ps5.ps5_ip, self.tv.tv_ip):
                    if host and host not in allowed:
                        allowed.append(host)
                        updated = True
                if updated:
                    with open(srt_file, "w", encoding="utf-8") as f:
                        json.dump(srt_data, f, indent=2)
            except Exception:
                pass

    def discover_and_configure(
        self,
        ps5_ip: str = "",
        tv_ip: str = "",
        action: str = "discover"
    ) -> Dict[str, Any]:
        """Auto-discover PS5 and Samsung Frame TV on the LAN, save IPs, and optionally pair."""
        if ps5_ip.strip():
            self.ps5.ps5_ip = ps5_ip.strip()
        if tv_ip.strip():
            self.tv.tv_ip = tv_ip.strip()

        ps5_info = self.ps5.discover()
        tv_info = self.tv.discover()
        self._save_config()

        result: Dict[str, Any] = {
            "action": action,
            "ps5": ps5_info or {
                "ip": self.ps5.ps5_ip,
                "state": "not_discovered",
                "note": "Make sure PS5 is on the same Wi-Fi/LAN subnet and 'Enable Turning on PS5 from Network' is enabled."
            },
            "samsung_frame_tv": tv_info or {
                "ip": self.tv.tv_ip,
                "state": "not_discovered",
                "note": "Make sure the Samsung Frame TV is connected to your local Wi-Fi/LAN."
            },
        }

        if action.lower() in ("pair_tv", "pair", "discover_and_pair") and self.tv.tv_ip:
            pair_res = self.tv.send_keys(["KEY_RETURN"], delay_ms=100, pair_timeout=15.0)
            self._save_config()
            result["tv_pairing"] = pair_res

        return result

    def list_apps(self) -> List[Dict[str, Any]]:
        """Return all enumerated streaming apps supported on the PS5 & Samsung Frame."""
        apps_summary = []
        for app_id, info in STREAMING_APPS.items():
            apps_summary.append({
                "id": app_id,
                "name": info["name"],
                "ps5_title_id": info["ps5_title_id"],
                "samsung_app_id": info["samsung_app_id"],
                "justwatch_packages": info.get("justwatch_packages", []),
                "aliases": info["aliases"],
                "description": info["description"],
            })
        return apps_summary

    def search_streaming_catalog(
        self,
        query: str = "",
        app: str = "",
        mood_or_genre: str = "",
        content_type: str = "",
        limit: int = 8
    ) -> Dict[str, Any]:
        """Search live streaming app catalogs via JustWatch (with local catalog fallback if offline)."""
        live_res = self.justwatch.search_catalog(
            query=query,
            app=app,
            mood_or_genre=mood_or_genre,
            content_type=content_type,
            limit=limit,
        )
        if live_res.get("success") and live_res.get("count", 0) > 0:
            return live_res

        # Fallback to local MOOD_SHOW_CATALOG if offline or if no live results matched
        resolved_app = resolve_streaming_app(app) if app and app.strip() else None
        app_filter = resolved_app["id"] if resolved_app else None
        q_norm = query.lower().strip()
        fallback_items = []
        for item in MOOD_SHOW_CATALOG:
            if app_filter and item["app"] != app_filter:
                continue
            if q_norm and q_norm not in item["title"].lower() and q_norm not in item["genre"].lower() and q_norm not in item["vibe"].lower():
                continue
            app_meta = STREAMING_APPS.get(item["app"], {})
            fallback_items.append({
                "title": item["title"],
                "type": "SHOW",
                "release_year": None,
                "genres": [item["genre"]],
                "description": item["vibe"],
                "primary_ps5_app": item["app"],
                "primary_ps5_app_name": app_meta.get("name", item["app"]),
                "available_on_ps5_apps": [{
                    "app_id": item["app"],
                    "app_name": app_meta.get("name", item["app"]),
                    "ps5_title_id": app_meta.get("ps5_title_id"),
                    "monetization": "flatrate",
                }],
                "how_to_play": f"Call watch_on_ps5(app='{item['app']}', show_title={json.dumps(item['title'])})",
            })
            if len(fallback_items) >= max(1, limit):
                break

        if not live_res.get("success") and fallback_items:
            return {
                "success": True,
                "source": "local_catalog_fallback",
                "justwatch_error": live_res.get("error"),
                "query": query or None,
                "filtered_app": resolved_app["name"] if resolved_app else "All PS5 Streaming Apps",
                "count": len(fallback_items),
                "results": fallback_items,
            }
        return live_res

    def recommend_by_mood(
        self,
        mood: str,
        app: str = "",
        limit: int = 5
    ) -> Dict[str, Any]:
        """Recommend shows to watch on the PS5 based on the user's mood and optional streaming service."""
        norm_mood = mood.lower().strip()
        resolved_app = resolve_streaming_app(app) if app.strip() else None
        app_filter = resolved_app["id"] if resolved_app else None

        # Expand mood keywords using synonyms
        expanded_terms = {norm_mood}
        for word in re.findall(r"[\w-]+", norm_mood):
            expanded_terms.add(word)
            for canonical, syns in MOOD_SYNONYMS.items():
                if word == canonical or word in syns:
                    expanded_terms.add(canonical)
                    expanded_terms.update(syns)

        scored_shows: List[Tuple[int, Dict[str, Any]]] = []
        for item in MOOD_SHOW_CATALOG:
            if app_filter and item["app"] != app_filter:
                continue
            item_moods = set(item.get("moods", []))
            overlap = len(expanded_terms.intersection(item_moods))
            # Also match against genre and vibe description
            text_blob = f"{item['genre']} {item['vibe']}".lower()
            text_hits = sum(1 for term in expanded_terms if len(term) > 2 and term in text_blob)
            total_score = overlap * 3 + text_hits
            if total_score > 0 or not norm_mood:
                scored_shows.append((total_score, item))

        # If no direct mood keyword hit, return top all-around picks for the requested app (or overall)
        if not scored_shows:
            for item in MOOD_SHOW_CATALOG:
                if not app_filter or item["app"] == app_filter:
                    scored_shows.append((1, item))

        scored_shows.sort(key=lambda x: x[0], reverse=True)
        recommendations = []
        seen_titles = set()
        for score, show in scored_shows[:max(1, limit)]:
            app_meta = STREAMING_APPS.get(show["app"], {})
            seen_titles.add(show["title"].lower())
            recommendations.append({
                "title": show["title"],
                "app_id": show["app"],
                "app_name": app_meta.get("name", show["app"]),
                "ps5_title_id": app_meta.get("ps5_title_id"),
                "genre": show["genre"],
                "matching_moods": show["moods"],
                "vibe": show["vibe"],
                "source": "curated_mood_catalog",
                "how_to_play": f"Call watch_on_ps5(app='{show['app']}', show_title='{show['title']}')"
            })

        # Also query JustWatch live catalog for matching mood/genre to surface trending live titles
        live_catalog = self.justwatch.search_catalog(
            app=app,
            mood_or_genre=mood,
            content_type="SHOW",
            limit=limit,
            timeout=3.5,
        )
        live_picks = []
        for jw_item in live_catalog.get("results", []):
            t_lower = jw_item["title"].lower()
            if t_lower not in seen_titles and jw_item.get("primary_ps5_app"):
                seen_titles.add(t_lower)
                live_picks.append({
                    "title": jw_item["title"],
                    "app_id": jw_item["primary_ps5_app"],
                    "app_name": jw_item["primary_ps5_app_name"],
                    "release_year": jw_item.get("release_year"),
                    "genre": ", ".join(jw_item.get("genres") or []),
                    "imdb_score": jw_item.get("imdb_score"),
                    "vibe": jw_item.get("description", ""),
                    "source": "justwatch_live_catalog",
                    "how_to_play": jw_item["how_to_play"],
                })

        return {
            "requested_mood": mood,
            "filtered_app": resolved_app["name"] if resolved_app else "All PS5 Streaming Apps",
            "count": len(recommendations),
            "recommendations": recommendations,
            "live_justwatch_picks": live_picks[:limit],
        }

    def watch_on_ps5(
        self,
        app: str = "auto",
        show_title: str = "",
        skip_profile_prompt: bool = True
    ) -> Dict[str, Any]:
        """Wake the PS5 & Samsung Frame TV, launch the streaming app on the PS5, and search/play a show via HDMI-CEC."""
        resolved = None if app.lower().strip() in ("", "auto", "any", "justwatch") else resolve_streaming_app(app)
        justwatch_lookup: Optional[Dict[str, Any]] = None

        if not resolved:
            # 1. Try finding the show in our local curated catalog
            search_target = (show_title or app).strip()
            for item in MOOD_SHOW_CATALOG:
                if search_target and (
                    item["title"].lower() in search_target.lower()
                    or search_target.lower() in item["title"].lower()
                ):
                    resolved = STREAMING_APPS[item["app"]]
                    if not show_title:
                        show_title = item["title"]
                    break

            # 2. Query JustWatch live streaming availability for the show/movie title
            if not resolved and search_target:
                justwatch_lookup = self.justwatch.find_streaming_app_for_title(search_target)
                if justwatch_lookup:
                    resolved = justwatch_lookup["app_info"]
                    if not show_title:
                        show_title = justwatch_lookup["matched_title"]

        if not resolved:
            supported = [info["name"] for info in STREAMING_APPS.values()]
            return {
                "success": False,
                "error": f"Could not resolve streaming app '{app}'"
                + (f" or find a supported PS5 subscription stream for '{show_title}' on JustWatch." if show_title else ".")
                + f" Supported apps: {supported}"
            }

        # Primary Path: Use Home Assistant REST API if configured & connected
        if self.ha.is_configured():
            # If playing YouTube with a query/title, cast directly via Google Cast (Chromecast Ultra) for instant playback
            if resolved["id"] == "youtube" and show_title.strip():
                cast_res = self.ha.cast_media(query_or_url=show_title, app="youtube", target_device="living_room_tv")
                if cast_res.get("success"):
                    return cast_res

            kb_keys = build_cec_keyboard_sequence(show_title, layout=resolved.get("keyboard_layout", "row")) if show_title.strip() else None
            ha_res = self.ha.watch_on_ps5(
                app_info=resolved,
                show_title=show_title,
                skip_profile_prompt=skip_profile_prompt,
                cec_keys_for_show=kb_keys
            )
            if ha_res.get("success"):
                return ha_res

        # Fallback Path: Direct LAN / HDMI-CEC WebSocket control
        # Ensure devices are discovered if IPs aren't cached yet
        if not self.ps5.ps5_ip:
            self.ps5.discover()
        if not self.tv.tv_ip:
            self.tv.discover()
        self._save_config()

        steps: List[Dict[str, Any]] = []

        # Step 1: Establish and verify live WebSocket connection to Samsung Frame TV first
        tv_wake = self.tv.ensure_awake()
        tv_conn = self.tv.open_session()
        self._save_config()
        steps.append({
            "step": "1_establish_tv_connection",
            "tv_wake": tv_wake,
            "connection": tv_conn
        })
        if not tv_conn.get("connected"):
            return {
                "success": False,
                "app": resolved["name"],
                "app_id": resolved["id"],
                "ps5_ip": self.ps5.ps5_ip,
                "tv_ip": self.tv.tv_ip,
                "steps": steps,
                "error": tv_conn.get("error", "Could not establish connection to Samsung Frame TV before issuing commands.")
            }

        try:
            # Step 2: Wake PS5 from Rest Mode (via playactor if UDP available, or HDMI-CEC over our open TV session)
            ps5_status = self.ps5.query_status()
            if ps5_status and ps5_status.get("state") == "awake":
                ps5_wake = {"success": True, "already_awake": True, "status": ps5_status}
            else:
                ps5_wake = self.ps5.wake()
                if not ps5_wake.get("success"):
                    # Trigger HDMI-CEC (Anynet+) wake/focus over the verified Samsung Frame TV WebSocket session
                    cec_wake_keys = self.tv.send_keys(["KEY_EXIT", "KEY_ENTER"], delay_ms=800)
                    ps5_wake = {
                        "success": cec_wake_keys.get("success", False),
                        "method": "hdmi_cec_via_samsung_frame",
                        "cec_keys": cec_wake_keys,
                    }
            steps.append({"step": "2_wake_ps5", "result": ps5_wake})

            # Step 3: Ensure Samsung TV exits any Tizen overlay (like Browser/Smart Hub) and launches app on PS5
            st = self.ps5.query_status(timeout=0.8) or {}
            running_id = st.get("running_app_titleid") or ""
            running_name = (st.get("running_app_name") or "").lower()

            if running_id == resolved["ps5_title_id"] or resolved["name"].lower() in running_name:
                ps5_launch = {
                    "success": True,
                    "already_running_on_ps5": True,
                    "running_app_name": st.get("running_app_name"),
                    "running_app_titleid": running_id
                }
            else:
                # Close any Samsung TV native app/browser overlay so the TV shows the PS5 HDMI input,
                # then press Enter on the PS5 (logs into PS5 profile / opens highlighted Media app)
                # NOTE: Never send KEY_HOME here because KEY_HOME opens Samsung's Smart Hub bar!
                nav_res = self.tv.send_keys(["KEY_EXIT", "KEY_ENTER"], delay_ms=1200)
                ps5_launch = {
                    "success": nav_res.get("success", False),
                    "method": "ps5_hdmi_cec_enter",
                    "ps5_running_app": st.get("running_app_name"),
                    "result": nav_res
                }
                if skip_profile_prompt or show_title.strip():
                    time.sleep(5.0)
            steps.append({
                "step": "3_launch_ps5_app",
                "app": resolved["name"],
                "ps5_title_id": resolved["ps5_title_id"],
                "result": ps5_launch
            })

            # Step 4: Send staged HDMI-CEC navigation / show search sequence over the open TV session
            cec_batches: List[Dict[str, Any]] = []
            if skip_profile_prompt and resolved.get("has_profile_prompt"):
                # Stage 4a: Select default profile on 'Who's Watching?' and wait for Home screen
                prof_res = self.tv.send_keys(["KEY_ENTER"], delay_ms=500)
                cec_batches.append({"stage": "select_profile", "result": prof_res})
                if show_title.strip():
                    time.sleep(3.5)

            if show_title.strip():
                # Stage 4b: Open the in-app Search page and wait for keyboard grid
                search_nav_keys = resolved.get("search_nav", ["KEY_LEFT", "KEY_UP", "KEY_ENTER"])
                nav_res = self.tv.send_keys(search_nav_keys, delay_ms=650)
                cec_batches.append({"stage": "open_search", "result": nav_res})
                time.sleep(1.5)

                # Stage 4c: Enter show title characters on keyboard grid and start playback
                kb_keys = build_cec_keyboard_sequence(show_title, layout=resolved.get("keyboard_layout", "row"))
                kb_res = self.tv.send_keys(kb_keys, delay_ms=420)
                cec_batches.append({"stage": "search_and_play", "key_count": len(kb_keys), "result": kb_res})

            self._save_config()
            if cec_batches:
                steps.append({
                    "step": "4_hdmi_cec_search_and_play",
                    "show_title": show_title,
                    "batches": cec_batches
                })
        finally:
            self.tv.close_session()

        return {
            "success": True,
            "app": resolved["name"],
            "app_id": resolved["id"],
            "ps5_title_id": resolved["ps5_title_id"],
            "show_title": show_title or None,
            "ps5_ip": self.ps5.ps5_ip,
            "tv_ip": self.tv.tv_ip,
            "steps": steps,
            "message": (
                f"Initiated '{resolved['name']}'"
                + (f" and queued HDMI-CEC search/playback for '{show_title}'" if show_title else "")
                + " on your PS5 via Samsung Frame TV."
            )
        }

    def control_media(self, command: str, repeat: int = 1) -> Dict[str, Any]:
        """Control live PS5 / Samsung Frame TV playback and navigation via Home Assistant or direct HDMI-CEC."""
        norm_cmd = command.lower().strip().replace(" ", "_").replace("-", "_")
        repeat = max(1, min(20, int(repeat)))

        # Resolve key sequence first so both Home Assistant and direct fallback can use it
        keys: List[str] = []
        if norm_cmd in ("select_profile", "confirm_profile"):
            keys = ["KEY_ENTER"]
        elif norm_cmd in HDMI_CEC_KEYS:
            keys = [HDMI_CEC_KEYS[norm_cmd]] * repeat
        elif norm_cmd not in ("ps5_wake", "wake_ps5", "turn_on_ps5", "ps5_standby", "standby", "rest_mode", "sleep_ps5", "turn_off_ps5"):
            parts = [p.strip() for p in command.split(",") if p.strip()]
            if len(parts) > 1:
                keys = [HDMI_CEC_KEYS.get(p.lower(), p.upper()) for p in parts]
            else:
                return {
                    "success": False,
                    "error": f"Unknown command '{command}'. Supported commands: {sorted(list(HDMI_CEC_KEYS.keys()) + ['ps5_wake', 'ps5_standby', 'select_profile'])}"
                }

        # Primary Path: Use Home Assistant REST API if configured & connected
        if self.ha.is_configured():
            ha_res = self.ha.control_media(command=command, key_codes=keys[:1] if len(set(keys)) == 1 else keys, repeat=repeat)
            if ha_res.get("success"):
                return ha_res

        # Handle PS5-specific power commands directly if Home Assistant isn't used
        if norm_cmd in ("ps5_wake", "wake_ps5", "turn_on_ps5"):
            if not self.ps5.ps5_ip:
                self.ps5.discover()
                self._save_config()
            return self.ps5.wake()

        if norm_cmd in ("ps5_standby", "standby", "rest_mode", "sleep_ps5", "turn_off_ps5"):
            if not self.ps5.ps5_ip:
                self.ps5.discover()
                self._save_config()
            return self.ps5.standby()

        # Handle HDMI-CEC playback & D-pad commands via Samsung Frame TV
        if not self.tv.tv_ip:
            self.tv.discover()
            self._save_config()

        res = self.tv.send_keys(keys, delay_ms=220)
        self._save_config()
        return res

    def cast_to_tv(
        self,
        query_or_url: str,
        app: str = "youtube",
        target_device: str = "living_room_tv"
    ) -> Dict[str, Any]:
        """Cast a YouTube video (by search query, URL, or video ID), direct media URL, or TTS voice message to Google Cast via Home Assistant."""
        return self.ha.cast_media(
            query_or_url=query_or_url,
            app=app,
            target_device=target_device,
        )
