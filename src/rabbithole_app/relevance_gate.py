"""
Relevance Gate for Rabbithole.

Evaluates whether candidate video metadata supports a connection to the user's
interests or to a specific, explainable bridge. Rejects clear mismatches (e.g.
off-topic YouTube recommendation artifacts, format-only keyword matches, domain clashes),
preserves genuinely surprising cross-domain bridges, and handles uncertain cases
honestly without assigning unverified substance or relevance claims.

Operates deterministically in heuristic mode without an API key; Gemini may be used
as an optional enhancement.
"""
import os
import re
import json
import logging
from typing import List, Dict, Tuple, Optional, Set

from rabbithole_app.models import (
    CandidateVideo,
    UserPreferences,
    CuriosityQuery,
    RelevanceAssessment,
)

logger = logging.getLogger(__name__)

# Stopwords excluded from topic extraction
STOPWORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than",
    "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't",
    "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's",
    "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom",
    "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll",
    "you're", "you've", "your", "yours", "yourself", "yourselves"
}

# Generic query modifiers that describe video formats or learning angles,
# but DO NOT by themselves indicate topical relevance.
GENERIC_QUERY_MODIFIERS: Set[str] = {
    "lecture", "lectures", "archive", "archival", "documentary", "documentaries",
    "case", "study", "studies", "methodology", "methodologies", "applied", "application",
    "principles", "principle", "first", "theory", "theories", "theoretical",
    "models", "model", "structural", "structure", "structures",
    "controversy", "controversies", "retrospective", "evolution",
    "history", "historical", "analysis", "breakdown", "breakdowns",
    "pioneer", "pioneers", "mechanics", "overview", "introduction", "intro",
    "guide", "tutorial", "basics", "talk", "talks", "symposium", "conference",
    "masterclass", "101", "explained", "explainer", "demonstration", "demonstrations",
    "science", "connection", "connections", "interdisciplinary", "investigation",
    "understanding", "deep", "dive", "video", "essay", "crash", "course",
    "revolution", "origins", "foundational", "perspectives", "insights"
}

# Rich domain lexicons to support broad interests and domain-specific terminology
DOMAIN_LEXICONS: Dict[str, Set[str]] = {
    "baseball": {
        "baseball", "pitch", "pitching", "pitcher", "pitchers", "pitch selection",
        "pitch sequencing", "batter", "batters", "batting", "hitter", "hitters",
        "strikeout", "strikezone", "sabermetrics", "mlb", "dugout", "innings",
        "catcher", "bunting", "fastball", "curveball", "slider", "changeup", "cutter",
        "sinker", "bullpen", "defensive shift", "moneyball", "bill james",
        "ops", "war", "fip", "era", "whip", "lineup", "scouting", "umpire",
        "statcast", "spin rate", "edgertronic", "seam shifted wake",
        "bloopers", "funny", "highlights", "trick play", "antics", "walk off", "walk-off", "dugout"
    },
    "birdwatching": {
        "bird", "birds", "birding", "birdwatching", "birder", "birders", "twitcher",
        "twitchers", "avian", "ornithology", "ornithological", "feather", "feathers",
        "raptor", "raptors", "warbler", "warblers", "owl", "owls", "eagle", "eagles",
        "sparrow", "hawk", "hawks", "flock", "migration", "migratory", "nesting",
        "songbird", "songbirds", "plumage", "field guide", "binoculars", "audubon",
        "sibley", "kingfisher", "corvid", "passerine", "waterfowl", "shorebird"
    },
    "photography": {
        "photography", "photographer", "photographers", "photo", "photos", "camera",
        "cameras", "telephoto", "lens", "lenses", "aperture", "shutter", "iso",
        "exposure", "focal length", "composition", "framing", "macro", "sensor",
        "lighting", "wildlife photography", "nature photography", "field sketching",
        "digiscoping", "tripod", "raw"
    },
    "movies": {
        "movie", "movies", "film", "films", "cinema", "cinematic", "cinematography",
        "filmmaker", "filmmaking", "director", "directors", "directing", "screenplay",
        "screenwriting", "script", "editing", "editor", "montage", "eisenstein",
        "hitchcock", "mise-en-scene", "three act", "three-act", "narrative",
        "storytelling", "character arc", "pacing", "jump cut", "continuity",
        "french new wave", "soviet montage", "hollywood", "scene", "dramaturgy",
        "bloopers", "parody", "satire", "short film", "animation", "miniatures", "props", "set stories", "magic"
    },
    "gardening": {
        "garden", "gardening", "gardener", "plant", "plants", "soil", "compost",
        "composting", "microbiology", "horticulture", "agronomy", "permaculture",
        "harvest", "vegetable", "vegetables", "heirloom", "cultivar", "roots",
        "mycelium", "nutrient", "soil food web", "rhizosphere", "perennial",
        "giant pumpkin", "diy garden", "satisfying", "botanical"
    },
    "cooking": {
        "cooking", "cook", "culinary", "kitchen", "chef", "food", "fermentation",
        "ferment", "fermenting", "microbial", "lactofermentation", "koji", "recipe",
        "flavor", "cuisine", "harold mcgee", "food science", "gastronomy", "baking",
        "pyrolysis", "maillard", "emulsion", "colloid", "brine", "yeast",
        "kitchen disaster", "chef comedy", "street food", "pastry art", "sugar art"
    },
    "music": {
        "music", "musical", "sound", "acoustics", "acoustic", "audio", "timbre",
        "frequency", "pitch", "harmony", "harmonic", "harmonics", "tuning", "tune",
        "temperament", "pythagorean", "just intonation", "equal temperament", "rhythm",
        "melody", "scale", "orchestration", "instrument", "instruments", "luthier",
        "voicing", "resonance", "standing wave", "flue", "pipe organ", "organ", "piano",
        "synthesizer", "synth", "synthesis", "wavefolding", "saturation", "oscillator",
        "filter", "waveform", "monochord", "intervals", "overtones", "vibration", "chords",
        "music theory memes", "fails", "unusual instrument", "junk luthier", "soundscape"
    },
    "game_theory": {
        "game theory", "minimax", "maximin", "nash equilibrium", "payoff matrix",
        "decision theory", "algorithmic game theory", "mixed strategy",
        "sequential game", "zero sum", "asymmetric information", "mechanism design",
        "prisoner's dilemma", "signaling", "strategic interaction", "game theoretical"
    },
    "mathematics": {
        "math", "mathematics", "calculus", "linear algebra", "matrix", "matrices",
        "vector", "vectors", "eigenspace", "eigenvector", "eigenvalue", "topology",
        "topological", "manifold", "geometry", "geometric", "proof", "proofs",
        "theorem", "theorems", "derivative", "derivatives", "integral", "integrals",
        "fourier", "transform", "fourier transform", "number theory", "algebra",
        "prime", "primes", "euler", "gauss", "riemann", "differential equations",
        "manim", "differential", "dimensions", "complex numbers", "quaternion",
        "combinatorics", "graph theory", "complex analysis", "discrete math",
        "modular arithmetic", "fractal", "fractals", "knot theory", "golden ratio",
        "fibonacci", "linear transformations", "multivariable calculus"
    },
    "basketball": {
        "basketball", "nba", "wnba", "dunk", "slam dunk", "three pointer", "three-pointer",
        "triple double", "triple-double", "pick and roll", "crossover", "layup", "rebound",
        "rebounds", "buzzer beater", "buzzer-beater", "shot chart", "free throw", "eurostep",
        "fast break", "lebron", "curry", "stephen curry", "jordan", "kobe", "celtics",
        "lakers", "warriors", "playoffs", "finals", "shooting form", "isolation",
        "zone defense", "shot doctor", "court", "hoop", "assist", "assists", "block",
        "steals", "box score", "true shooting", "offensive rating", "defensive rating",
        "spacing", "ball handling", "clutch shot", "ankle breaker"
    },
    "economics": {
        "economics", "economy", "economic", "macroeconomics", "microeconomics", "econometrics",
        "inflation", "interest rate", "interest rates", "monetary policy", "fiscal policy",
        "federal reserve", "central bank", "central banking", "gdp", "gross domestic product",
        "supply and demand", "market equilibrium", "capitalism", "socialism", "keynes",
        "keynesian", "hayek", "adam smith", "milton friedman", "game theory", "nash equilibrium",
        "mechanism design", "trade deficit", "tariff", "tariffs", "currency", "debt",
        "recession", "depression", "business cycle", "opportunity cost", "elasticity",
        "externality", "externalities", "behavioral economics", "liquidity", "yield curve",
        "quantitative easing", "market failure", "deadweight loss", "auction theory"
    },
    "gaming": {
        # Core medium, aliases & formats
        "videogame", "videogames", "video game", "video games", "gaming", "gameplay",
        "playthrough", "speedrun", "speedrunning", "speedrunner", "world record",
        "any%", "100%", "hitless", "modded", "boss fight", "walkthrough", "let's play",
        "glitch", "sequence break", "tool-assisted", "tas",
        # Design, systems & mechanics
        "game design", "game mechanics", "level design", "combat design", "game dev",
        "game development", "game developer", "game engine", "unreal engine", "unity engine",
        "godot", "lumen", "nanite", "collision detection", "hitbox", "hurtbox", "frame data",
        "coyote time", "jump buffering", "physics engine", "shaders", "pixel art", "bsp trees",
        "bsp tree", "platformer", "platforming", "roguelike", "roguelite", "metroidvania", "action rpg",
        "immersive sim", "sandbox", "open world design", "game ai", "behavior tree",
        "procedural generation", "procedural dungeon", "game balance", "difficulty curve",
        # Famous games, series & franchises
        "super mario", "mario 64", "super mario 64", "zelda", "breath of the wild",
        "tears of the kingdom", "ocarina of time", "elden ring", "dark souls", "bloodborne",
        "sekiro", "fromsoftware", "hollow knight", "celeste", "minecraft", "redstone",
        "doom", "doom 1993", "quake", "half-life", "half life", "portal", "metroid",
        "super metroid", "metroid prime", "final fantasy", "cyberpunk", "skyrim",
        "elder scrolls", "grand theft auto", "gta", "pokemon", "chrono trigger",
        "metal gear", "metal gear solid", "resident evil", "silent hill", "castlevania",
        "symphony of the night", "spelunky", "hades", "undertale", "bioshock",
        "shadow of the colossus", "outer wilds", "disco elysium", "factorio",
        # Recognizable creators & outlets
        "summoning salt", "game maker's toolkit", "gmtk", "noclip", "digital foundry",
        "mark brown", "ahoy", "design doc", "razbuten", "adam millard", "matthewmatosis",
        "didyouknowgaming", "iron pineapple", "karl jobst", "lowspecgamer", "core-a gaming"
    }
}

DOMAIN_RECOGNIZED_CREATORS: Dict[str, Set[str]] = {
    "mathematics": {"3blue1brown", "numberphile", "grant sanderson", "mathologer", "stand-up maths", "standupmaths", "welch labs"},
    "baseball": {"foolish baseball", "jomboy media", "baseball doesn't exist", "pitching ninja"},
    "basketball": {"thinking basketball", "kOT4Q", "mike korzemba", "jxmyhighroller", "dime drop"},
    "economics": {"economics explained", "freakonomics", "visual economix", "marginal revolution university"},
    "gaming": {"summoning salt", "game maker's toolkit", "gmtk", "noclip", "digital foundry", "mark brown", "ahoy", "design doc", "razbuten", "adam millard", "matthewmatosis", "didyouknowgaming", "iron pineapple", "karl jobst", "lowspecgamer", "core-a gaming"}
}

DOMAIN_TRIGGERS: Dict[str, Set[str]] = {
    "baseball": {"baseball", "pitch", "pitching", "pitcher", "sabermetrics", "mlb", "softball"},
    "birdwatching": {"bird", "birds", "birding", "birdwatching", "ornithology", "birder", "twitcher", "avian"},
    "photography": {"photo", "photos", "photography", "photographer", "camera", "cameras", "optics"},
    "movies": {"movie", "movies", "film", "films", "cinema", "cinematography", "storytelling", "narrative", "screenplay", "screenwriting"},
    "gardening": {"garden", "gardening", "plant", "plants", "soil", "compost", "botany", "horticulture", "agronomy"},
    "cooking": {"cook", "cooking", "culinary", "kitchen", "chef", "food", "fermentation", "baking", "gastronomy", "cuisine"},
    "music": {"music", "musical", "sound", "acoustic", "acoustics", "audio", "synth", "synthesis", "synthesizer", "timbre", "harmonic", "tuning"},
    "mathematics": {"math", "mathematics", "calculus", "geometry", "linear algebra", "topology", "3blue1brown", "algebra", "fourier"},
    "basketball": {"basketball", "nba", "wnba", "hoops", "ncaa basketball"},
    "economics": {"economics", "economy", "macroeconomics", "microeconomics", "econometrics", "monetary policy", "fiscal policy"},
    "gaming": {"videogames", "videogame", "video games", "video game", "gaming", "gameplay", "game design", "game dev", "game development", "speedrun", "speedrunning"}
}


# Domain clash patterns: distinct subjects that frequently pollute search results
# when YouTube matches generic formatting words or partial strings.
DOMAIN_CLASH_PATTERNS = [
    (
        "genealogy_forensics",
        {"genealogy", "family research", "dna testing", "forensic genealogy", "csi meets roots", "ancestry", "family tree", "pedigree"},
        {"baseball", "birdwatching", "movies", "gardening", "music", "fluid dynamics", "infrastructure"}
    ),
    (
        "surfing_lifestyle",
        {"surf", "surfing", "surfboard", "wsl", "surf podcast", "al cleland", "casey wasserman", "wave riding", "surf splendor"},
        {"baseball", "birdwatching", "movies", "gardening", "music", "infrastructure"}
    ),
    (
        "celebrity_drama_kpop",
        {"kpop", "k-pop", "bts", "jungkook", "taehyung", "jimin", "army", "gossip", "dating rumors", "drama beef"},
        {"baseball", "birdwatching", "movies", "gardening", "music", "infrastructure"}
    ),
    (
        "streamer_gaming_vod",
        {"northernlion vod", "the dles", "tape to tape", "gameplay walkthrough", "lets play", "vtuber"},
        {"baseball", "birdwatching", "movies", "gardening", "music", "infrastructure"}
    ),
    (
        "subatomic_atomic_physics",
        {"atomic models", "dalton", "rutherford", "bohr", "heisenberg", "subatomic", "electron orbital", "lewis structure", "valence electron"},
        {"birdwatching", "baseball", "gardening", "movies"}
    ),
    (
        "clinical_oncology",
        {"breast cancer", "oncology", "chemotherapy", "cancer clinic", "kaiser permanente breast", "mastectomy"},
        {"birdwatching", "baseball", "movies", "music", "gardening"}
    ),
    (
        "generic_academic_homework",
        {
            "how to write a case study", "step-by-step guide to writing a case study",
            "qualitative research design", "dissertation help", "action research visual explanation",
            "teaching is a craft"
        },
        {"birdwatching", "photography", "baseball", "gardening", "music"}
    ),
    (
        "crime_scene_forensics",
        {"forensic photography", "crime scene", "applied forensic research sciences", "afsj", "autopsy", "ballistics"},
        {"birdwatching"}  # specifically clashes when curiosity is birdwatching / nature photography
    ),
    (
        "association_football_soccer",
        {"soccer", "u s soccer", "football club", "premier league", "la liga", "bundesliga", "fifa", "champions league"},
        {"baseball"}  # clashes when 'pitch' refers to soccer fields rather than baseball pitching
    ),
    (
        "corporate_business_strategy",
        {
            "corporate strategy", "business strategy", "growing companies", "porter's five forces",
            "management consulting", "executive leadership", "mergers and acquisitions", "venture capital",
            "enterprise software", "b2b sales", "corporate finance", "business model"
        },
        {"baseball", "baseball & strategy", "basketball", "birdwatching", "gardening"}
    ),
    (
        "lifestyle_recipes_woodworking",
        {
            "homemade pasta recipe", "fresh fettuccine from scratch", "favorite pasta recipe",
            "studio desk build", "woodworking desk build", "room tour", "daily vlog",
            "what i eat in a day", "skincare routine"
        },
        {"mathematics", "math", "economics", "baseball", "basketball"}
    )
]


def _extract_tokens(text: str) -> Set[str]:
    """Extract clean alphanumeric tokens of length >= 3, excluding stopwords."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in STOPWORDS}


def _is_text_avoided(text: str, avoided_terms: List[str]) -> bool:
    """Checks whether text contains any avoided topics using word boundaries."""
    if not text or not avoided_terms:
        return False
    t_lower = text.lower()
    for term in avoided_terms:
        term_clean = term.strip().lower()
        if not term_clean:
            continue
        if term_clean in ("history", "historic", "historical"):
            if re.search(r"\bhistor\w*\b", t_lower, re.IGNORECASE):
                return True
        else:
            pattern = r"\b" + re.escape(term_clean) + r"(?:s|es|'s)?\b"
            if re.search(pattern, t_lower, re.IGNORECASE):
                return True
            if " " in term_clean and term_clean in t_lower:
                return True
            if len(term_clean) > 3 and term_clean in t_lower:
                return True
    return False


class RelevanceGate:
    """
    Evaluates candidate videos before they enter viewing paths.
    Ensures candidates have supported connections to user curiosity or valid bridges.
    """

    def __init__(self, api_key: Optional[str] = None):
        if api_key is not None:
            self.api_key = api_key.strip() if api_key.strip() else None
        else:
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.last_mode: str = "uninitialized"

    def assess_candidate(
        self,
        candidate: CandidateVideo,
        preferences: UserPreferences,
        query_context: Optional[CuriosityQuery] = None
    ) -> RelevanceAssessment:
        """
        Assesses whether a candidate video's metadata supports a connection to
        the user's interests or to a specific explainable bridge.
        """
        # Always run deterministic heuristic assessment as baseline
        return self._heuristic_assessment(candidate, preferences, query_context)

    def _heuristic_assessment(
        self,
        candidate: CandidateVideo,
        preferences: UserPreferences,
        query_context: Optional[CuriosityQuery] = None
    ) -> RelevanceAssessment:
        title = candidate.title or ""
        channel = candidate.channel or ""
        desc = candidate.description or ""
        query = candidate.source_query or (query_context.query if query_context else "")
        q_type = candidate.source_query_type or (query_context.query_type if query_context else "")
        domain = candidate.source_domain or (query_context.domain if query_context else "")

        full_metadata = f"{title} {channel} {desc}".lower()
        content_text = f"{title} {desc}".lower()
        title_channel = f"{title} {channel}".lower()
        channel_lower = channel.lower()
        interests_lower = preferences.interests.lower()

        # 0. Global Exclusions: Explicit Dislikes and Avoided Topics across ALL modes, categories, and views
        avoided_terms = [part.strip().lower() for part in re.split(r"[,;\n|]", preferences.avoid_topics) if part.strip()]
        if _is_text_avoided(full_metadata, avoided_terms):
            return RelevanceAssessment(
                is_relevant=False,
                status="rejected",
                reason=f"Candidate matches avoided topics ('{preferences.avoid_topics}').",
                confidence=0.95
            )

        if preferences.disliked_video_ids and candidate.id in set(preferences.disliked_video_ids):
            return RelevanceAssessment(
                is_relevant=False,
                status="rejected",
                reason="Video was previously disliked by user.",
                confidence=0.99
            )

        if preferences.disliked_records:
            for dr in preferences.disliked_records:
                if dr.video_id == candidate.id:
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Candidate matches disliked feedback ('{dr.title or dr.channel}').",
                        confidence=0.99
                    )
                if dr.channel and dr.channel.lower() == channel_lower and dr.chosen_reason and "channel" in dr.chosen_reason.lower():
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Channel was explicitly disliked by user ('{dr.channel}').",
                        confidence=0.95
                    )

        if preferences.active_category:
            active_cat_lower = preferences.active_category.lower()

            # 1. Active Category Domain Clash Checks
            # Baseball & Strategy tab: reject corporate, executive, or business strategy
            is_baseball_tab = any(trig in active_cat_lower for trig in DOMAIN_TRIGGERS.get("baseball", set()))
            if is_baseball_tab:
                corp_terms = {
                    "corporate strategy", "business strategy", "growing companies", "porter's five forces",
                    "management consulting", "executive leadership", "mergers and acquisitions",
                    "venture capital", "enterprise software", "b2b sales", "corporate finance"
                }
                has_corp = any(ct in full_metadata for ct in corp_terms)
                has_baseball_substantive = any(
                    bt in full_metadata for bt in [
                        "baseball", "pitch", "pitching", "pitcher", "sabermetrics", "mlb", "strikeout",
                        "dugout", "bullpen", "fastball", "curveball", "slider", "catcher", "batter", "innings"
                    ]
                )
                if has_corp and not has_baseball_substantive:
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Metadata indicates corporate/business strategy with no valid connection to '{preferences.active_category}'.",
                        confidence=0.92,
                    )

            # Mathematics tab: reject off-topic lifestyle, cooking, recipes, woodworking, board games
            is_math_tab = any(trig in active_cat_lower for trig in DOMAIN_TRIGGERS.get("mathematics", set()))
            if is_math_tab:
                lifestyle_terms = {
                    "homemade pasta recipe", "fresh fettuccine", "favorite pasta recipe", "pasta recipe",
                    "recipe", "pasta", "studio desk build", "woodworking desk build", "room tour",
                    "daily vlog", "what i eat in a day", "skincare routine", "unboxing my new camera",
                    "woodworking", "studio desk", "desk build", "shop tour", "furniture",
                    "board games", "board game", "game night", "casual games", "vlog"
                }
                has_lifestyle = any(lt in content_text for lt in lifestyle_terms)
                has_math_terms = any(
                    mt in content_text for mt in [
                        "fourier", "eigenvector", "eigenvalue", "calculus", "linear algebra", "topology",
                        "manifold", "geometry", "proof", "theorem", "derivative", "integral", "number theory",
                        "prime", "primes", "riemann", "differential equations", "quaternion", "manim", "math"
                    ]
                )
                if has_lifestyle and not has_math_terms:
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Metadata indicates off-topic upload with no mathematical connection to '{preferences.active_category}'.",
                        confidence=0.92,
                    )

            # Basketball tab: reject other sports, cooking, corporate business
            is_basketball_tab = any(trig in active_cat_lower for trig in DOMAIN_TRIGGERS.get("basketball", set()))
            if is_basketball_tab:
                bball_clash_terms = {
                    "baseball", "pitching", "mlb", "soccer", "premier league", "golf swing", "tennis",
                    "corporate strategy", "business strategy", "recipe"
                }
                has_bball_clash = any(bct in content_text for bct in bball_clash_terms)
                has_bball_terms = any(
                    bbt in content_text for bbt in [
                        "basketball", "nba", "dunk", "pick and roll", "crossover", "triple double",
                        "buzzer beater", "shot chart", "free throw", "lebron", "curry", "celtics", "lakers"
                    ]
                )
                if has_bball_clash and not has_bball_terms:
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Metadata indicates off-topic content with no connection to '{preferences.active_category}'.",
                        confidence=0.90,
                    )

            # Economics tab: reject sports, gaming, cooking, home DIY
            is_econ_tab = any(trig in active_cat_lower for trig in DOMAIN_TRIGGERS.get("economics", set()))
            if is_econ_tab:
                econ_clash_terms = {
                    "baseball", "basketball", "nba", "minecraft", "speedrun", "recipe", "home renovation", "diy woodworking"
                }
                has_econ_clash = any(ect in content_text for ect in econ_clash_terms)
                has_econ_terms = any(
                    et in content_text for et in [
                        "economics", "economy", "macroeconomics", "microeconomics", "inflation",
                        "interest rate", "monetary policy", "central bank", "federal reserve", "gdp", "supply and demand"
                    ]
                )
                if has_econ_clash and not has_econ_terms:
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Metadata indicates off-topic content with no connection to '{preferences.active_category}'.",
                        confidence=0.90,
                    )

            # Gaming / Videogames tab: reject sports, board games/tabletop, cooking, corporate business, lifestyle vlogs
            is_gaming_tab = any(trig in active_cat_lower for trig in DOMAIN_TRIGGERS.get("gaming", set()))
            if is_gaming_tab:
                gaming_clash_terms = {
                    "nba", "mlb", "premier league", "celtics", "lakers", "nfl", "touchdown", "quarterback",
                    "board games", "board game", "game night", "catan", "monopoly", "poker tournament",
                    "corporate strategy", "business strategy", "recipe", "pasta", "cooking", "woodworking",
                    "skincare routine", "what i eat in a day", "room tour", "daily vlog"
                }
                has_gaming_clash = any(gct in content_text for gct in gaming_clash_terms)
                has_gaming_terms = any(
                    gt in content_text for gt in DOMAIN_LEXICONS["gaming"]
                )
                if has_gaming_clash and not has_gaming_terms:
                    return RelevanceAssessment(
                        is_relevant=False,
                        status="rejected",
                        reason=f"Metadata indicates off-topic content with no video game connection to '{preferences.active_category}'.",
                        confidence=0.92,
                    )

            # 2. Substantive Subject Evidence in Title, Channel, or Description
            matched_lexicon = False
            matched_lexicon_term = ""
            known_domain = None
            for domain_name, triggers in DOMAIN_TRIGGERS.items():
                if any(trigger in active_cat_lower for trigger in triggers):
                    known_domain = domain_name
                    # Require term in actual content (title/desc) or recognized domain creator
                    for term in DOMAIN_LEXICONS[domain_name]:
                        if len(term) >= 4 and term not in GENERIC_QUERY_MODIFIERS:
                            if term in content_text:
                                matched_lexicon = True
                                matched_lexicon_term = term
                                break
                    if matched_lexicon:
                        break

                    # Check recognized creators for that domain if title/desc does not clash
                    rec_creators = DOMAIN_RECOGNIZED_CREATORS.get(domain_name, set())
                    if any(rc in channel_lower for rc in rec_creators):
                        clash_words = {
                            "recipe", "pasta", "cooking", "woodworking", "desk", "furniture", "vlog",
                            "board games", "board game", "game night", "tour", "unboxing", "skincare", "haul"
                        }
                        if not any(cw in content_text for cw in clash_words):
                            matched_lexicon = True
                            matched_lexicon_term = f"recognized creator in {domain_name}"
                            break

            # Direct token match for categories (including auto-created tabs)
            category_tokens = _extract_tokens(preferences.active_category) - GENERIC_QUERY_MODIFIERS
            metadata_tokens = _extract_tokens(full_metadata)
            direct_match = False

            if known_domain == "baseball":
                # For Baseball & Strategy, must specifically match baseball terms
                direct_match = "baseball" in metadata_tokens or "mlb" in metadata_tokens
            elif known_domain == "gaming":
                direct_match = any(t in metadata_tokens for t in {
                    "videogame", "videogames", "gaming", "gameplay", "speedrun", "speedrunning",
                    "gamedev", "minecraft", "mario", "zelda", "elden", "doom", "celeste", "pokemon"
                })
            elif category_tokens:
                if len(category_tokens) == 1:
                    direct_match = bool(category_tokens & metadata_tokens)
                else:
                    direct_match = len(category_tokens & metadata_tokens) >= min(2, len(category_tokens))

            if not (matched_lexicon or direct_match):
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason=f"Metadata does not support the active tab '{preferences.active_category}'.",
                    confidence=0.88,
                )

            # Candidate is confirmed on-topic for the active category
            match_reason = matched_lexicon_term or preferences.active_category
            return RelevanceAssessment(
                is_relevant=True,
                status="supported",
                reason=f"Metadata supports active tab '{preferences.active_category}' ('{match_reason}').",
                confidence=0.92,
                matched_concept=f"category:{preferences.active_category}"
            )

        # Independent discovery candidates: evaluate for quality, watchability, dislikes, and avoided topics
        # without requiring connection to existing interests or clashing with seed topics
        cand_disc_type = getattr(candidate, "discovery_type", None) or (getattr(query_context, "discovery_type", None) if query_context else None)
        is_independent = (
            cand_disc_type in ("independent", "wildcard")
            or candidate.source_query_type == "independent_discovery"
            or (getattr(candidate, "is_discovery", False) and cand_disc_type in ("independent", "wildcard"))
        )

        if is_independent and not preferences.active_category:
            # 1. Avoided topics check
            avoided_terms = [part.strip().lower() for part in re.split(r"[,;\n|]", preferences.avoid_topics) if part.strip()]
            if _is_text_avoided(full_metadata, avoided_terms):
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason=f"Candidate matches avoided topics ('{preferences.avoid_topics}').",
                    confidence=0.95
                )

            # 2. Explicit dislikes check
            if preferences.disliked_video_ids and candidate.id in set(preferences.disliked_video_ids):
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason="Video was previously disliked by user.",
                    confidence=0.99
                )
            if preferences.disliked_records:
                for dr in preferences.disliked_records:
                    if dr.video_id == candidate.id or (dr.channel and dr.channel.lower() == channel_lower):
                        return RelevanceAssessment(
                            is_relevant=False,
                            status="rejected",
                            reason=f"Candidate matches disliked feedback ('{dr.title or dr.channel}').",
                            confidence=0.95
                        )

            # 3. Basic quality / watchability & non-empty metadata
            if len(desc.strip()) < 10 and len(title.split()) <= 2:
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason="Available metadata is too sparse or empty for independent discovery evaluation.",
                    confidence=0.85
                )

            # 4. Verify coherence with the independent/wildcard query / domain
            target_domain_name = domain or getattr(candidate, "source_domain", "") or ""
            target_tokens = ((_extract_tokens(query) | _extract_tokens(target_domain_name)) - GENERIC_QUERY_MODIFIERS) - STOPWORDS
            meta_tokens = _extract_tokens(full_metadata)
            matched_target = target_tokens & meta_tokens
            disc_label_type = cand_disc_type if cand_disc_type in ("wildcard", "adjacent", "distant") else "independent"
            label_domain = target_domain_name or (", ".join(sorted(list(matched_target))[:2]) if matched_target else f"{disc_label_type} exploration")
            if target_tokens and not matched_target:
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason=f"Metadata does not support the proposed {disc_label_type} discovery topic ('{query}' / '{target_domain_name}').",
                    confidence=0.88
                )

            return RelevanceAssessment(
                is_relevant=True,
                status="supported",
                reason=f"Supported as {disc_label_type} discovery in {label_domain}: '{title}'.",
                confidence=0.90,
                matched_concept=f"{disc_label_type}:{label_domain}"
            )

        # Check for minimal or sparse metadata (Uncertain case)
        if len(desc.strip()) < 20 and len(title.split()) <= 3:
            # Short ambiguous title and empty description
            # Before marking uncertain, check if it explicitly clashes
            for clash_name, terms, clashed_domains in DOMAIN_CLASH_PATTERNS:
                if any(t in full_metadata for t in terms):
                    if any(cd in interests_lower for cd in clashed_domains):
                        return RelevanceAssessment(
                            is_relevant=False,
                            status="rejected",
                            reason=f"Metadata indicates {clash_name} with no connection to '{preferences.interests}'.",
                            confidence=0.85
                        )
            return RelevanceAssessment(
                is_relevant=True,
                status="uncertain",
                reason="Available metadata is sparse or ambiguous; connection to user's stated interests cannot be verified.",
                confidence=0.30
            )

        # 1. DOMAIN CLASH & OFF-TOPIC PATTERN DETECTION
        for clash_name, terms, clashed_domains in DOMAIN_CLASH_PATTERNS:
            if any(term in full_metadata for term in terms):
                # Check if this clash applies to the user's current interests
                if any(cd in interests_lower for cd in clashed_domains):
                    # Guard: verify the candidate does not also have a genuine, intentional match
                    # (e.g. user literally asked for genealogy or forensics)
                    if not any(t in interests_lower for t in terms):
                        return RelevanceAssessment(
                            is_relevant=False,
                            status="rejected",
                            reason=f"Metadata indicates {clash_name} with no valid connection to '{preferences.interests}'.",
                            confidence=0.90
                        )

        # Check followed creators: if candidate is from a creator the user follows,
        # it matches their taste profile (provided it did not clash above)
        if preferences.followed_creators:
            for fc in preferences.followed_creators:
                if fc.matches(channel, getattr(candidate, "channel_id", None)):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"From followed creator '{fc.name}' matching user's taste profile.",
                        confidence=0.92,
                        matched_concept=f"followed_creator:{fc.name}"
                    )

        # Check learned subjects from living taste profile
        cand_domain = candidate.source_domain or (query_context.domain if query_context else "")
        if preferences.living_taste_profile:
            lp = preferences.living_taste_profile
            all_profile_subs = [s for s, _ in (lp.get_established_subjects() + lp.get_tentative_subjects())]
            for sub in all_profile_subs:
                sub_tokens = _extract_tokens(sub)
                if sub_tokens and len(sub_tokens & _extract_tokens(full_metadata)) >= min(2, len(sub_tokens)):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"Metadata connects to learned taste profile subject '{sub}'.",
                        confidence=0.88,
                        matched_concept=f"taste_profile:{sub}"
                    )
                if cand_domain and sub.lower() in cand_domain.lower():
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"Metadata connects to learned taste profile subject '{sub}' via domain '{cand_domain}'.",
                        confidence=0.88,
                        matched_concept=f"taste_profile:{sub}"
                    )

        # Check genuine discovery candidates (adjacent, distant, wildcard)
        cand_disc_type = getattr(candidate, "discovery_type", None) or (getattr(query_context, "discovery_type", None) if query_context else None)
        if cand_disc_type:
            target_domain_name = domain or getattr(candidate, "source_domain", "") or ""
            target_tokens = ((_extract_tokens(query) | _extract_tokens(target_domain_name)) - GENERIC_QUERY_MODIFIERS) - STOPWORDS
            meta_tokens = _extract_tokens(full_metadata)
            matched_target = target_tokens & meta_tokens
            if matched_target:
                label_domain = target_domain_name or (", ".join(sorted(list(matched_target))[:2]) if matched_target else "discovery field")
                if cand_disc_type in ("independent", "wildcard"):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"Supported as high-quality {cand_disc_type} discovery in {label_domain} outside current categories.",
                        confidence=0.90,
                        matched_concept=f"{cand_disc_type}:{label_domain}"
                    )
                elif cand_disc_type == "distant":
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"Supported as distant discovery in {label_domain} matching viewer's preferred qualities.",
                        confidence=0.88,
                        matched_concept=f"distant_discovery:{label_domain}"
                    )
                else:  # adjacent
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"Supported as adjacent discovery: lateral branch into {label_domain}.",
                        confidence=0.90,
                        matched_concept=f"adjacent_discovery:{label_domain}"
                    )
            else:
                # Video metadata lacks substantive support for the proposed discovery topic.
                # A long description or multiword title alone is NOT evidence of relevance.
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason=f"Metadata does not support the proposed {cand_disc_type} discovery topic ('{query}' / '{target_domain_name}').",
                    confidence=0.88
                )

        # 2. EXPLAINABLE CROSS-DOMAIN BRIDGE & ENTERTAINMENT ASSESSMENT
        # If candidate was retrieved by a cross-domain query or targets an analogical bridge,
        # assess connection to the bridge concepts without requiring exact user interest keywords.
        is_bridge_query = (q_type == "cross_domain_analogy") or ("bridge" in str(q_type).lower())
        is_entertainment_query = bool(q_type and str(q_type).startswith("entertainment_"))

        if is_entertainment_query:
            # Check if candidate connects to user interest or domain terms in an entertaining context
            matched_int = _extract_tokens(preferences.interests) & _extract_tokens(full_metadata)
            if matched_int:
                return RelevanceAssessment(
                    is_relevant=True,
                    status="supported",
                    reason=f"Metadata supports entertaining presentation connecting to '{preferences.interests}' ({', '.join(sorted(list(matched_int))[:2])}).",
                    confidence=0.92,
                    matched_concept=f"entertainment:{','.join(sorted(list(matched_int))[:2])}"
                )

        if is_bridge_query or "game theory" in query.lower() or "game theory" in domain.lower():
            # Check for Game Theory & Decision Science bridge (e.g. baseball -> pitch sequencing -> game theory)
            if any(gt in full_metadata for gt in DOMAIN_LEXICONS["game_theory"]):
                # If connected to baseball as well, direct connection; if pure game theory, valid bridge
                has_interest_anchor = any(t in full_metadata for t in DOMAIN_LEXICONS["baseball"])
                if has_interest_anchor:
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata directly connects baseball strategy and pitch sequencing to game theoretic decision models.",
                        confidence=0.95,
                        matched_concept="baseball + game_theory"
                    )
                else:
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to game theory bridge concept (decision theory / minimax strategy) for lateral exploration.",
                        confidence=0.85,
                        matched_concept="game_theory_bridge"
                    )

        # Check for other explainable bridges
        if is_bridge_query:
            # Birdwatching bridges: biomimicry, field sketching, ecoacoustics
            if "bird" in interests_lower:
                if any(bm in full_metadata for bm in {"biomimicry", "bullet train", "shinkansen", "kingfisher"}):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to biomimicry bridge (avian morphology applied to engineering).",
                        confidence=0.90,
                        matched_concept="biomimicry_bridge"
                    )
                if any(art in full_metadata for art in {"sketching", "drawing", "illustration", "nature journal", "field sketch"}):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to field observation and nature sketching bridge.",
                        confidence=0.88,
                        matched_concept="nature_sketching_bridge"
                    )
                if any(ea in full_metadata for ea in {"ecoacoustics", "soundscape", "bioacoustics", "environmental sound"}):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to ecoacoustics and environmental audio observation bridge.",
                        confidence=0.85,
                        matched_concept="ecoacoustics_bridge"
                    )

            # Film & storytelling bridges: emotion theory, cognitive perception, eye tracking
            if "movie" in interests_lower or "film" in interests_lower or "story" in interests_lower:
                if any(cog in full_metadata for cog in {"emotion theory", "cognitive", "eye tracking", "psychology", "neuroscience", "audience mind"}):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to cognitive film theory and audience psychology bridge.",
                        confidence=0.88,
                        matched_concept="cognitive_film_bridge"
                    )

            # Gardening & cooking bridges: microbial fermentation, biochemistry
            if "garden" in interests_lower or "cook" in interests_lower:
                if any(ferm in full_metadata for ferm in {"fermentation", "lactofermentation", "anaerobic", "microbial", "biochemistry", "respiration"}):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to microbial biochemistry and fermentation bridge.",
                        confidence=0.90,
                        matched_concept="microbial_fermentation_bridge"
                    )

            # Music & sound bridges: mathematics, geometry, pythagorean ratios
            if "music" in interests_lower or "sound" in interests_lower:
                if any(math_term in full_metadata for math_term in {"pythagoras", "pythagorean", "geometry", "monochord", "ratios", "intervals", "mathematical problem"}):
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason="Metadata supports connection to mathematical tuning and harmonic acoustics bridge.",
                        confidence=0.90,
                        matched_concept="mathematical_acoustics_bridge"
                    )

        # 3. CORE INTEREST & DOMAIN LEXICON ASSESSMENT
        # Check against established domain lexicons triggered by interest words
        for dom, lexicon in DOMAIN_LEXICONS.items():
            triggers = DOMAIN_TRIGGERS.get(dom, {dom})
            if any(trig in interests_lower for trig in triggers):
                matched_domain_terms = [t for t in lexicon if t in full_metadata]
                if matched_domain_terms:
                    # Found substantive domain evidence
                    best_match = matched_domain_terms[0]
                    return RelevanceAssessment(
                        is_relevant=True,
                        status="supported",
                        reason=f"Metadata directly connects to {dom} concept ('{best_match}').",
                        confidence=0.90,
                        matched_concept=f"{dom}:{best_match}"
                    )

        # 4. QUERY TOPIC TERMS VS GENERIC QUERY MODIFIERS
        # Extract meaningful topic terms from candidate's source query
        query_tokens = _extract_tokens(query)
        query_topic_tokens = query_tokens - GENERIC_QUERY_MODIFIERS
        metadata_tokens = _extract_tokens(full_metadata)

        matched_query_topics = query_topic_tokens & metadata_tokens
        matched_modifiers_only = (query_tokens & GENERIC_QUERY_MODIFIERS) & metadata_tokens

        # If query had topic terms (e.g. 'baseball', 'birdwatching', 'defensive shift'),
        # and candidate matched ZERO topic terms, but matched generic modifiers:
        if query_topic_tokens and not matched_query_topics and matched_modifiers_only:
            # Check if candidate matches any user interest tokens
            interest_tokens = _extract_tokens(preferences.interests)
            matched_interest = interest_tokens & metadata_tokens
            if not matched_interest:
                return RelevanceAssessment(
                    is_relevant=False,
                    status="rejected",
                    reason=f"Metadata matches generic query formatting words ({', '.join(sorted(list(matched_modifiers_only))[:3])}) but contains no evidence connecting to '{preferences.interests}' or search topic.",
                    confidence=0.85
                )

        if matched_query_topics:
            return RelevanceAssessment(
                is_relevant=True,
                status="supported",
                reason=f"Metadata connects to search topic concepts ({', '.join(sorted(list(matched_query_topics))[:3])}).",
                confidence=0.82,
                matched_concept=",".join(sorted(list(matched_query_topics))[:3])
            )

        # 5. OPEN-ENDED INTEREST TOKEN OVERLAP
        # For unfamiliar/unmapped domains (e.g. 'Everyday Municipal Infrastructure & Utility Grids')
        interest_tokens = _extract_tokens(preferences.interests)
        matched_interest_tokens = interest_tokens & metadata_tokens
        if matched_interest_tokens:
            return RelevanceAssessment(
                is_relevant=True,
                status="supported",
                reason=f"Metadata connects to user interest concepts ({', '.join(sorted(list(matched_interest_tokens))[:3])}).",
                confidence=0.80,
                matched_concept=",".join(sorted(list(matched_interest_tokens))[:3])
            )

        # 5b. Archetype-supported candidates for personality-based preferences
        if getattr(preferences, "has_personality_preferences", False) and not preferences.active_category:
            q_tokens = (_extract_tokens(query) - GENERIC_QUERY_MODIFIERS) - STOPWORDS
            matched_q_tokens = q_tokens & metadata_tokens
            if len(matched_q_tokens) >= 2 or (len(q_tokens) == 1 and len(matched_q_tokens) >= 1):
                return RelevanceAssessment(
                    is_relevant=True,
                    status="supported",
                    reason=f"Metadata supports curiosity archetype inquiry '{query}' ({', '.join(sorted(list(matched_q_tokens))[:3])}).",
                    confidence=0.85,
                    matched_concept=f"archetype_inquiry:{query}"
                )

        # 6. DEFAULT REJECTION FOR UNSUPPORTED CANDIDATES
        return RelevanceAssessment(
            is_relevant=False,
            status="rejected",
            reason=f"Metadata lacks supported connection to '{preferences.interests}' or explainable discovery bridges.",
            confidence=0.80
        )

    def filter_candidates(
        self,
        candidates: List[CandidateVideo],
        preferences: UserPreferences,
        query_map: Optional[Dict[str, CuriosityQuery]] = None
    ) -> Tuple[List[CandidateVideo], List[Tuple[CandidateVideo, RelevanceAssessment]]]:
        """
        Filters candidates through the relevance gate.
        Returns:
            (accepted_candidates, rejected_candidates_with_assessment)
        """
        accepted: List[CandidateVideo] = []
        rejected: List[Tuple[CandidateVideo, RelevanceAssessment]] = []

        for cand in candidates:
            q_context = query_map.get(cand.source_query) if query_map and cand.source_query else None
            assessment = self.assess_candidate(cand, preferences, q_context)
            if assessment.is_relevant:
                accepted.append(cand)
            else:
                rejected.append((cand, assessment))
                logger.info(f"Relevance gate rejected '{cand.title}': {assessment.reason}")

        return accepted, rejected
