"""
Data models and schemas for Rabbithole.
"""
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, field_validator


class ViewingRecord(BaseModel):
    """Detailed record of playback progress and engagement inside Rabbithole."""
    video_id: str
    title: str
    channel: str
    thumbnail: Optional[str] = None
    duration_seconds: Optional[int] = None
    current_position_seconds: float = 0.0
    watch_time_seconds: float = 0.0
    status: Literal["shown", "started", "in_progress", "completed"] = "started"
    last_watched_timestamp: Optional[str] = None
    completed_at: Optional[str] = None
    source_domain: Optional[str] = None
    source_query: Optional[str] = None

    @property
    def progress_percentage(self) -> float:
        if not self.duration_seconds or self.duration_seconds <= 0:
            return 0.0
        return min(max((self.current_position_seconds / self.duration_seconds) * 100.0, 0.0), 100.0)

    @property
    def is_resumeable(self) -> bool:
        return self.status == "in_progress" or (self.status == "started" and self.current_position_seconds > 5.0)


class DislikeRecord(BaseModel):
    """Detailed record of a disliked video and optional user feedback."""
    video_id: str
    title: str
    channel: str
    thumbnail: Optional[str] = None
    recommendation_context: Optional[str] = None
    chosen_reason: Optional[str] = None
    source_domain: Optional[str] = None
    date: Optional[str] = None


class WatchLaterRecord(BaseModel):
    """Detailed record of a video saved to Watch Later."""
    video_id: str
    title: str
    channel: str
    thumbnail: Optional[str] = None
    duration_seconds: Optional[int] = None
    recommendation_context: Optional[str] = None
    source_domain: Optional[str] = None
    source_category: Optional[str] = None
    is_discovery: bool = False
    discovery_connection: Optional[str] = None
    saved_at: Optional[str] = None


class InterestCategory(BaseModel):
    """A user interest category (e.g. 'Baseball & Strategy', 'Cinema & Storytelling')."""
    id: str
    name: str
    is_auto_created: bool = False
    is_dismissed: bool = False
    created_at: Optional[str] = None


class EmergingInterestSignal(BaseModel):
    """Tracks detection of emerging interests from user actions on discovery items."""
    topic: str
    source_discovery_domain: str
    distinct_video_ids: List[str] = Field(default_factory=list)
    saved_video_ids: List[str] = Field(default_factory=list)
    watched_video_ids: List[str] = Field(default_factory=list)  # videos with substantial watch time (>=60s)
    positive_actions_count: int = 0
    is_promoted: bool = False
    is_dismissed: bool = False
    first_detected_at: Optional[str] = None
    last_updated_at: Optional[str] = None


class FollowedCreator(BaseModel):
    """A creator followed by the user inside Rabbithole to understand taste."""
    channel_id: Optional[str] = None  # Stable YouTube channel ID (e.g. UCYO_jab_esuFRV4b17AJtAw)
    name: str                         # Display channel name (e.g. 3Blue1Brown)
    followed_at: Optional[str] = None # ISO timestamp or date string
    thumbnail: Optional[str] = None   # Optional channel avatar or video thumbnail

    def matches(self, channel_name: Optional[str], channel_id: Optional[str] = None) -> bool:
        """Checks if a candidate's channel matches this followed creator.
        Uses stable channel ID when available; falls back to channel name."""
        if self.channel_id and channel_id:
            return self.channel_id.strip() == channel_id.strip()
        if channel_name and self.name:
            return self.name.strip().lower() == channel_name.strip().lower()
        return False


class TasteSignal(BaseModel):
    """An individual recorded event influencing taste."""
    video_id: Optional[str] = None
    title: Optional[str] = None
    channel: Optional[str] = None
    domain: Optional[str] = None
    qualities: List[str] = Field(
        default_factory=list,
        description="Qualities enjoyed, e.g. 'visual_explanation', 'technical_depth', 'humor', 'storytelling', 'artisan_craft', 'unusual_connections'."
    )
    action: Literal["watch_later", "completed", "substantial_watch", "followed", "disliked"]
    weight: float = 1.0  # Explicit actions have higher weight
    timestamp: Optional[str] = None


class LivingTasteProfile(BaseModel):
    """
    Persistent living taste profile that updates from meaningful actions:
    substantial watches, completion, Watch Later, followed creators, and dislikes.
    Tracks subjects, creators, and broader qualities.
    """
    imported_subject_affinities: Dict[str, float] = Field(default_factory=dict)
    imported_subject_evidence_counts: Dict[str, int] = Field(default_factory=dict)
    subject_affinities: Dict[str, float] = Field(
        default_factory=dict,
        description="Subjects/domains mapped to evidence strength."
    )
    subject_evidence_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Number of distinct video interactions per subject (requires >= 2 for lasting interest)."
    )
    preferred_qualities: Dict[str, float] = Field(
        default_factory=dict,
        description="Qualities enjoyed, e.g. visual_explanation, technical_depth, humor, storytelling, artisan_craft, unusual_connections."
    )
    followed_creators: List[str] = Field(
        default_factory=list,
        description="Creators explicitly followed by the user."
    )
    disliked_patterns: List[str] = Field(
        default_factory=list,
        description="Specific disliked topics/formats."
    )
    preferred_entertainment_formats: Dict[str, float] = Field(
        default_factory=dict,
        description="Affinities for specific entertainment formats (comedy, gameplay, sports_moments, performance, animation, creative_projects, compelling_story)."
    )
    recent_signals: List[TasteSignal] = Field(
        default_factory=list,
        description="Chronological log of recent meaningful interactions."
    )
    last_updated_at: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if not self.imported_subject_affinities and self.subject_affinities:
            signal_domains = {s.domain for s in self.recent_signals if s.domain}
            for dom, score in self.subject_affinities.items():
                if dom not in signal_domains:
                    self.imported_subject_affinities[dom] = score
                    self.imported_subject_evidence_counts[dom] = max(
                        2, self.subject_evidence_counts.get(dom, 2)
                    )

    def recalculate_profile(self) -> None:
        """
        Recalculates affinities, evidence counts, preferred qualities, and entertainment formats from recent_signals in chronological order.
        - Older passive signals can be decayed via apply_passive_decay().
        - Explicit actions (watch_later, followed, completed, disliked) maintain full strength.
        """
        self.subject_affinities = dict(self.imported_subject_affinities)
        self.preferred_qualities.clear()
        self.preferred_entertainment_formats.clear()

        ent_format_aliases = {
            "comedy": "comedy",
            "humor": "comedy",
            "gameplay": "gameplay",
            "challenges": "challenges",
            "challenge": "challenges",
            "sports_moments": "sports_moments",
            "sports": "sports_moments",
            "performance": "performance",
            "music": "performance",
            "animation": "animation",
            "creative_projects": "creative_projects",
            "artisan_craft": "creative_projects",
            "craft": "creative_projects",
            "compelling_story": "compelling_story",
            "storytelling": "compelling_story",
            "lore": "compelling_story"
        }

        # Track distinct videos per domain for evidence count
        vids_per_domain: Dict[str, set[str]] = {}

        # Process in chronological order (oldest to newest)
        for i, s in enumerate(reversed(self.recent_signals)):
            action = s.action
            effective_weight = s.weight

            if s.domain:
                dom = s.domain
                if action == "disliked":
                    self.subject_affinities[dom] = max(0.0, self.subject_affinities.get(dom, 0.0) - abs(effective_weight))
                else:
                    self.subject_affinities[dom] = round(self.subject_affinities.get(dom, 0.0) + effective_weight, 2)
                    if dom not in vids_per_domain:
                        vids_per_domain[dom] = set()
                    vids_per_domain[dom].add(s.video_id or s.title or f"vid_{i}")

            if action != "disliked":
                for q in s.qualities:
                    self.preferred_qualities[q] = round(self.preferred_qualities.get(q, 0.0) + effective_weight * 0.8, 2)
                    norm_fmt = ent_format_aliases.get(q.lower().replace(" ", "_"))
                    if norm_fmt:
                        self.preferred_entertainment_formats[norm_fmt] = round(
                            self.preferred_entertainment_formats.get(norm_fmt, 0.0) + effective_weight * 0.8, 2
                        )

        self.subject_evidence_counts = dict(self.imported_subject_evidence_counts)
        for dom, vids in vids_per_domain.items():
            self.subject_evidence_counts[dom] = self.subject_evidence_counts.get(dom, 0) + len(vids)

    def apply_passive_decay(self, decay_factor: float = 0.85) -> None:
        """
        Gradually decays older passive signals (substantial_watch) while explicit actions
        (saves, follows, completions, dislikes) remain strong.
        """
        for s in self.recent_signals:
            if s.action == "substantial_watch":
                s.weight = round(s.weight * decay_factor, 2)
        self.recalculate_profile()

    def record_signal(self, signal: TasteSignal) -> None:
        """Records a new taste signal, updating affinities, evidence counts, and preferred qualities."""
        if signal.video_id:
            self.recent_signals = [
                existing for existing in self.recent_signals
                if not (existing.video_id == signal.video_id and existing.action == signal.action)
            ]
        self.recent_signals.insert(0, signal)
        self.recent_signals = self.recent_signals[:300]

        if signal.action == "followed" and signal.channel:
            if signal.channel not in self.followed_creators:
                self.followed_creators.append(signal.channel)

        self.recalculate_profile()

    def remove_signal(self, video_id: str, action: Optional[str] = None) -> None:
        """
        Removes recorded signal(s) matching video_id (and action if specified)
        and recalculates the taste profile so removed items stop influencing recommendations.
        """
        self.recent_signals = [
            s for s in self.recent_signals
            if not (s.video_id == video_id and (action is None or s.action == action))
        ]
        self.recalculate_profile()

    def unfollow_creator(self, channel: str) -> None:
        """
        Unfollows a creator, removes corresponding follow signals, and recalculates profile.
        """
        self.followed_creators = [c for c in self.followed_creators if c != channel]
        self.recent_signals = [
            s for s in self.recent_signals
            if not (s.action == "followed" and (s.channel == channel or s.video_id == channel))
        ]
        self.recalculate_profile()

    def reset(self) -> None:
        """Resets all learned taste data back to a clean slate."""
        self.imported_subject_affinities.clear()
        self.imported_subject_evidence_counts.clear()
        self.subject_affinities.clear()
        self.subject_evidence_counts.clear()
        self.preferred_qualities.clear()
        self.preferred_entertainment_formats.clear()
        self.followed_creators.clear()
        self.disliked_patterns.clear()
        self.recent_signals.clear()
        self.last_updated_at = None

    def get_top_entertainment_formats(self, limit: int = 3) -> List[tuple[str, float]]:
        """Returns top preferred entertainment formats above zero score."""
        sorted_f = sorted(
            [(f, score) for f, score in self.preferred_entertainment_formats.items() if score > 0.0],
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_f[:limit]

    def get_top_qualities(self, limit: int = 3, top_n: Optional[int] = None) -> List[tuple[str, float]]:
        """Returns top preferred qualities above zero score."""
        n = top_n if top_n is not None else limit
        sorted_q = sorted(
            [(q, score) for q, score in self.preferred_qualities.items() if score > 0.0],
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_q[:n]

    def get_established_subjects(self, min_count: int = 2, limit: int = 4) -> List[tuple[str, float]]:
        """Returns subjects with confirmed evidence (>= min_count distinct interactions)."""
        established = [
            (sub, score) for sub, score in self.subject_affinities.items()
            if self.subject_evidence_counts.get(sub, 0) >= min_count and score > 0.0
        ]
        established.sort(key=lambda x: x[1], reverse=True)
        return established[:limit]

    def get_tentative_subjects(self, limit: int = 4) -> List[tuple[str, float]]:
        """Returns emerging or single-video subjects (< 2 interactions) not yet treated as permanent."""
        tentative = [
            (sub, score) for sub, score in self.subject_affinities.items()
            if self.subject_evidence_counts.get(sub, 0) == 1 and score > 0.0
        ]
        tentative.sort(key=lambda x: x[1], reverse=True)
        return tentative[:limit]

    def compact_summary(self) -> str:
        """Compact summary of the living taste profile for LLM context."""
        parts = []
        top_qualities = [q for q, _ in self.get_top_qualities(limit=3)]
        if top_qualities:
            clean_q = [q.replace("_", " ").title() for q in top_qualities]
            parts.append(f"Preferred Viewing Qualities: {', '.join(clean_q)}")

        top_ent_formats = [f for f, _ in self.get_top_entertainment_formats(limit=3)]
        if top_ent_formats:
            clean_f = [f.replace("_", " ").title() for f in top_ent_formats]
            parts.append(f"Preferred Entertainment Formats: {', '.join(clean_f)}")

        est_subs = [s for s, _ in self.get_established_subjects(limit=3)]
        if est_subs:
            parts.append(f"Demonstrated Subject Affinities (multi-video confirmed): {', '.join(est_subs)}")

        tent_subs = [s for s, _ in self.get_tentative_subjects(limit=3)]
        if tent_subs:
            parts.append(f"Tentative / Emerging Topics (single-video): {', '.join(tent_subs)}")

        if self.followed_creators:
            parts.append(f"Followed Creators: {', '.join(self.followed_creators[:4])}")

        if self.disliked_patterns:
            parts.append(f"Avoided Styles / Formats: {', '.join(self.disliked_patterns[:4])}")

        if not parts:
            return ""
        return "Living Taste Profile (learned from user actions):\n" + "\n".join(f"  • {p}" for p in parts)


CURIOSITY_ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "mechanist": {
        "id": "mechanist",
        "name": "The Mechanist / Tinkerer",
        "tagline": "How things work under the hood",
        "icon": "⚙️",
        "description": "Fascinated by physical logic, mechanisms, schematics, extreme tolerances, and engineering from first principles.",
        "search_angles": [
            "intricate mechanism precision tolerances",
            "first principles mechanical engineering breakdown",
            "physical logic micro mechanical actuators",
            "precision fabrication workshop tolerances",
            "analog computing mechanical clockwork"
        ],
        "default_qualities": ["technical_depth", "visual_explanation", "first_principles"]
    },
    "detective": {
        "id": "detective",
        "name": "The Archival Detective",
        "tagline": "Unsolved mysteries & forgotten rabbit holes",
        "icon": "🕵️",
        "description": "Drawn to forgotten historical disputes, lost artifacts, technical dead-ends, and stranger-than-fiction paper trails.",
        "search_angles": [
            "forgotten technological dispute archival documentary",
            "unsolved historical technical mystery forensic",
            "stranger than fiction engineering dead end",
            "historical counter-narrative primary source investigation",
            "archival footage forgotten innovation"
        ],
        "default_qualities": ["storytelling", "niche_history", "unusual_connections"]
    },
    "visual_synthesizer": {
        "id": "visual_synthesizer",
        "name": "The Visual Synthesizer",
        "tagline": "Elegant models & geometric intuition",
        "icon": "📐",
        "description": "Enjoys elegant spatial animations, geometric intuition, topological visual models, and clean diagrams of complex ideas.",
        "search_angles": [
            "geometric visual intuition animation",
            "spatial topology visual explanation",
            "cellular automata algorithmic visualization",
            "visualizing multidimensional mathematical spaces",
            "aesthetic geometric simulation"
        ],
        "default_qualities": ["visual_explanation", "technical_depth", "artisan_craft"]
    },
    "craft_purist": {
        "id": "craft_purist",
        "name": "The Craft Purist / Flow State",
        "tagline": "Tactile mastery & dedicated patience",
        "icon": "🪵",
        "description": "Values mastery, patience, restorative techniques, zero-filler workshop process, and artisan dedication.",
        "search_angles": [
            "master artisan workshop process demonstration",
            "traditional woodworking joinery zero fasteners",
            "precision hand tool restoration masterclass",
            "japanese craft material conservation",
            "hypnotic artisan workshop flow"
        ],
        "default_qualities": ["artisan_craft", "visual_explanation", "storytelling"]
    },
    "game_theorist": {
        "id": "game_theorist",
        "name": "The Strategic Game Theorist",
        "tagline": "Hidden incentives & tactical psychology",
        "icon": "♟️",
        "description": "Captivated by hidden incentives, psychological bluffs, competitive dynamics, rule exploits, and asymmetric decisions.",
        "search_angles": [
            "asymmetric game theory tactical breakdown",
            "competitive psychological bluff decision analysis",
            "rules loophole strategic exploit breakdown",
            "high stakes asymmetric decision theory",
            "sports tactical psychology hidden incentives"
        ],
        "default_qualities": ["technical_depth", "unusual_connections", "storytelling"]
    },
    "playful_deconstructor": {
        "id": "playful_deconstructor",
        "name": "The Playful Deconstructor",
        "tagline": "Clever satire, creative chaos & wit",
        "icon": "🎭",
        "description": "Loves witty deconstructions, clever comedic subversions, chaotic creative experiments, and unexpected absurdity.",
        "search_angles": [
            "absurdist creative engineering build challenge",
            "witty system deconstruction parody commentary",
            "unhinged creative invention prototype demonstration",
            "bloopers design fails comedic breakdown",
            "chaotic scientific experiment comedic analysis"
        ],
        "default_qualities": ["humor", "creative_projects", "unusual_connections"]
    }
}


class UserPreferences(BaseModel):
    """User input preferences and curiosity parameters."""
    interests: str = Field(
        default="",
        description="Freeform description of interests, curiosities, or questions you want to explore."
    )
    curiosity_archetypes: List[str] = Field(
        default_factory=list,
        description="Curiosity personalities / archetypes, e.g. 'mechanist', 'detective', 'visual_synthesizer', 'craft_purist', 'game_theorist', 'playful_deconstructor'."
    )
    intellectual_vibes: List[str] = Field(
        default_factory=list,
        description="Mindset and vibe tags (e.g. 'first_principles', 'hypnotic_flow', 'bizarre_history', 'deep_tactical', 'absurdist_wit')."
    )
    curiosity_statement: Optional[str] = Field(
        default=None,
        description="Freeform personality-based curiosity statement, e.g. 'I love seeing how intricate physical mechanisms work under extreme tolerances'."
    )
    youtube_connected: bool = Field(
        default=False,
        description="Whether a YouTube account has been connected or imported."
    )
    youtube_synced_channel_count: int = Field(
        default=0,
        description="Number of YouTube channels synced from subscriptions."
    )
    youtube_sync_mode: Optional[str] = Field(
        default=None,
        description="Source of YouTube integration ('browser_cookies', 'takeout_file', 'channel_list')."
    )
    familiar_topics: str = Field(default="", description="Topics the viewer already knows well.")
    explore_topics: str = Field(default="", description="Fields the viewer is open to discovering.")
    avoid_topics: str = Field(default="", description="Topics the viewer does not want in this path.")
    learning_goal: str = Field(default="", description="What the viewer wants from the session.")
    serendipity_level: int = Field(
        default=3,
        ge=1,
        le=5,
        description="1 = tightly focused deep-dive; 5 = radical cross-domain lateral jumps."
    )
    depth_level: Literal["beginner_friendly", "curious_explorer", "deep_academic"] = Field(
        default="curious_explorer",
        description="Desired cognitive depth and density of content."
    )
    min_duration_minutes: int = Field(
        default=6,
        ge=1,
        description="Minimum duration in minutes to filter out shorts and superficial overviews."
    )
    max_duration_minutes: Optional[int] = Field(
        default=180,
        description="Maximum duration in minutes (or None for unconstrained)."
    )
    avoid_sensationalism: bool = Field(
        default=True,
        description="Strictly gatekeep and penalize clickbait titles and hyped presentation."
    )
    seed_topic: Optional[str] = Field(
        default=None,
        description="Optional specific topic if branching out from an existing rabbit hole."
    )
    experience_type: Literal["mix", "fun", "thoughtful", "surprising"] = Field(
        default="mix",
        description="Desired mood or style of viewing experience."
    )
    feed_mode: Literal["mix", "entertainment", "deep_learning"] = Field(
        default="mix",
        description="Feed presentation style: 'mix' (balanced), 'entertainment' (creative/fun only), or 'deep_learning' (5-step progressive path)."
    )
    recency_preference: Literal["any", "recent", "older"] = Field(
        default="any",
        description="Preference for upload era: 'any' (all time), 'recent' (last few years), or 'older' (archival/vintage)."
    )
    seen_video_ids: List[str] = Field(
        default_factory=list,
        description="List of video IDs previously shown to avoid repeating them on refresh."
    )
    recent_queries: List[str] = Field(default_factory=list, max_length=40)
    recent_shown_video_ids: List[str] = Field(default_factory=list, max_length=100)
    recent_shown_subjects: List[str] = Field(default_factory=list, max_length=40)
    recent_shown_creators: List[str] = Field(default_factory=list, max_length=40)
    refresh_index: int = Field(
        default=0,
        ge=0,
        description="Sequential index for rotating discovery queries across consecutive refreshes."
    )
    completed_video_ids: List[str] = Field(
        default_factory=list,
        description="List of video IDs completed by the viewer to avoid recommending them again."
    )
    watched_channels: Dict[str, float] = Field(
        default_factory=dict,
        description="Channel names mapped to cumulative watch time in seconds inside Rabbithole."
    )
    watched_topics: List[str] = Field(
        default_factory=list,
        description="Domains or subjects the user has meaningfully watched."
    )
    disliked_video_ids: List[str] = Field(
        default_factory=list,
        description="List of video IDs disliked by user to strictly suppress from future recommendations."
    )
    disliked_records: List[DislikeRecord] = Field(
        default_factory=list,
        description="Records of disliked videos including optional feedback reasons for pattern synthesis."
    )
    categories: List[str] = Field(
        default_factory=list,
        description="Editable interest categories parsed from or configured by the viewer."
    )
    active_category: Optional[str] = Field(
        default=None,
        description="Specific category tab to focus on, or None for balanced main feed."
    )
    dismissed_categories: List[str] = Field(
        default_factory=list,
        description="Categories dismissed by the viewer that should not immediately reappear."
    )
    emerging_interests: List[str] = Field(
        default_factory=list,
        description="Emerging interest topics detected from positive actions on discovery videos."
    )
    watch_later_video_ids: List[str] = Field(
        default_factory=list,
        description="List of video IDs currently saved to Watch Later."
    )
    followed_creators: List[FollowedCreator] = Field(
        default_factory=list,
        description="Creators followed by the user inside Rabbithole to understand taste."
    )
    living_taste_profile: Optional[LivingTasteProfile] = Field(
        default=None,
        description="Persistent living taste profile updated from viewing actions."
    )

    @field_validator("followed_creators", mode="before")
    @classmethod
    def parse_followed_creators(cls, v: Any) -> List[FollowedCreator]:
        if not v:
            return []
        creators: List[FollowedCreator] = []
        for item in v:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    creators.append(FollowedCreator(name=name))
            elif isinstance(item, dict):
                creators.append(FollowedCreator(**item))
            elif isinstance(item, FollowedCreator):
                creators.append(item)
        return creators

    @property
    def curiosity_summary(self) -> str:
        """Returns a combined human-readable description of personality archetypes, vibes, and interests."""
        parts = []
        if self.curiosity_archetypes:
            arch_names = [CURIOSITY_ARCHETYPES[a]["name"] for a in self.curiosity_archetypes if a in CURIOSITY_ARCHETYPES]
            if arch_names:
                parts.append(f"Archetypes: {', '.join(arch_names)}")
        if self.curiosity_statement:
            parts.append(f"Curiosity Taste: \"{self.curiosity_statement.strip()}\"")
        if self.intellectual_vibes:
            clean_v = [v.replace('_', ' ').title() for v in self.intellectual_vibes]
            parts.append(f"Vibes: {', '.join(clean_v)}")
        if self.interests:
            parts.append(f"Seed Topics: {self.interests.strip()}")
        return " | ".join(parts) if parts else (self.interests or "General Discovery")

    @property
    def has_personality_preferences(self) -> bool:
        """Returns True if the user has defined personality archetypes, vibes, or a curiosity statement."""
        return bool(self.curiosity_archetypes or self.curiosity_statement or self.intellectual_vibes)


class CuriosityQuery(BaseModel):
    sort_by_date: bool = False
    """An exploratory query formulated by the LLM to search for high-signal content."""
    query: str = Field(description="The concrete search string dispatched to video archives.")
    domain: str = Field(description="The specific domain or sub-field targeted.")
    query_type: Literal[
        "niche_history",
        "first_principles",
        "archive_lecture",
        "cross_domain_analogy",
        "practitioner_deep_dive",
        "entertainment_comedy",
        "entertainment_creative",
        "entertainment_story",
        "entertainment_gameplay",
        "entertainment_sports",
        "entertainment_performance",
        "entertainment_challenge"
    ] = Field(description="The angle of exploration or entertainment.")
    rationale: str = Field(description="Why this query helps the user discover non-mainstream content.")
    discovery_type: Optional[Literal["adjacent", "distant", "wildcard", "independent"]] = Field(
        default=None,
        description="The discovery tier if this query explores outside primary categories."
    )
    discovery_rationale: Optional[str] = Field(
        default=None,
        description="Honest explanation connecting this discovery query to taste or wildcard exploration."
    )


class CuriosityQueryPlan(BaseModel):
    """Collection of queries generated for a user's session."""
    queries: List[CuriosityQuery] = Field(..., min_length=1, max_length=6)


class CandidateVideo(BaseModel):
    """Raw video candidate metadata."""
    id: str
    title: str
    channel: str
    channel_id: Optional[str] = None
    url: str
    thumbnail: Optional[str] = None
    duration_seconds: Optional[int] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    description: Optional[str] = ""
    source_query: Optional[str] = None
    source_query_type: Optional[str] = None
    source_domain: Optional[str] = None
    source_rationale: Optional[str] = None
    discovery_type: Optional[str] = None
    discovery_rationale: Optional[str] = None
    is_discovery: bool = False
    tags: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    full_description: Optional[str] = None
    transcript_summary: Optional[str] = None
    has_captions: bool = False
    evidence_source: Optional[str] = None

    @property
    def duration_formatted(self) -> str:
        if not self.duration_seconds:
            return "Unknown duration"
        minutes, seconds = divmod(self.duration_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds:02d}s"


class RelevanceAssessment(BaseModel):
    """Metadata-grounded assessment of candidate relevance to user curiosity or bridge."""
    is_relevant: bool = Field(description="True if supported or acceptable, False if clearly mismatched.")
    status: Literal["supported", "uncertain", "rejected"] = Field(
        description="Categorical relevance status based strictly on available metadata."
    )
    reason: str = Field(description="Honest, explainable reasoning for the relevance determination.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence in the metadata assessment.")
    matched_concept: Optional[str] = Field(default=None, description="Topic or bridge concept identified.")


class EnrichedVideoMetadata(BaseModel):
    """Enriched candidate metadata and compact transcript summary fetched in Stage 2."""
    video_id: str
    title: str
    channel: str
    channel_id: Optional[str] = None
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    duration_seconds: Optional[int] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    thumbnail: Optional[str] = None
    has_captions: bool = False
    caption_language: Optional[str] = None
    transcript_summary: Optional[str] = None
    evidence_source: Literal["metadata", "captions", "metadata_and_captions", "fallback"] = "metadata"
    cached_at: Optional[str] = None
    is_fallback: bool = False
    retry_after: Optional[str] = None


class QualityAssessment(BaseModel):
    """Evaluation of a candidate video's intellectual signal, novelty, and clickbait absence."""
    clickbait_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0.0 = completely honest & descriptive, 1.0 = extreme sensationalism / emotional manipulation"
    )
    substance_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0.0 = shallow / ad farm / superficial, 1.0 = dense, well-researched, substantive"
    )
    novelty_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="0.0 = widely known mainstream pop content, 1.0 = rare, overlooked, or lateral angle"
    )
    composite_score: float = Field(
        default=0.5,
        description="Overall recommendation score calculated from substance, novelty, and anti-clickbait penalty."
    )
    is_sensationalized: bool = Field(
        default=False,
        description="True if flagged as low-effort hype or deceptive."
    )
    entertainment_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Estimated entertainment, humor, craft, or engagement value from metadata."
    )
    is_entertainment_first: bool = Field(
        default=False,
        description="True if available metadata indicates the primary appeal is enjoyment, comedy, gameplay, challenges, sports moments, performance, animation, creative projects, or compelling stories rather than educational/instructional exposition."
    )
    entertainment_format: Optional[Literal[
        "comedy",
        "gameplay",
        "challenges",
        "sports_moments",
        "performance",
        "animation",
        "creative_projects",
        "compelling_story",
        "spectacle"
    ]] = Field(
        default=None,
        description="Identified entertainment format if entertainment-first."
    )
    entertainment_evidence: Optional[str] = Field(
        default="",
        description="Metadata-grounded evidence explaining why this video is entertainment-first, or why uncertain."
    )
    actual_subject: Optional[str] = Field(
        default=None,
        description="Core subject matter of the video grounded in metadata and/or captions."
    )
    specific_angle: Optional[str] = Field(
        default=None,
        description="Specific angle, thesis, pedagogical approach, or creative premise."
    )
    content_format: Optional[str] = Field(
        default=None,
        description="Identified format (explanation, gameplay, comedy, documentary, performance, creative_projects, compelling_story, challenge, essay, tutorial)."
    )
    likely_audience: Optional[str] = Field(
        default="general",
        description="Intended audience level: beginner, enthusiast, practitioner, general, or academic."
    )
    appeal_type: Optional[str] = Field(
        default="balanced_blend",
        description="Dominant appeal: pure_entertainment, engaging_education, deep_education, or balanced_blend."
    )
    appeal_ratio: Optional[float] = Field(
        default=0.5,
        description="Ratio of entertainment to educational appeal (0.0 = pure educational, 1.0 = pure entertainment)."
    )
    confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence in evaluation based on depth of available metadata/captions."
    )
    evidence_source: Literal["metadata", "captions", "metadata_and_captions", "fallback"] = Field(
        default="metadata",
        description="Whether evaluation was grounded in metadata only, captions, or metadata + captions."
    )
    content_nature: Literal["educational", "entertaining", "blend"] = Field(
        default="blend",
        description="Whether the video leans educational, entertaining, or a blend of both."
    )
    relevance_status: Literal["supported", "uncertain", "rejected"] = Field(
        default="supported",
        description="Relevance classification based on metadata evidence."
    )
    relevance_reason: str = Field(
        default="",
        description="Explainable rationale connecting metadata to user curiosity or explaining mismatch."
    )
    connection_note: str = Field(
        default="",
        description="A concise 1-2 sentence note explaining why this specific rabbit hole is worth exploring."
    )
    key_takeaways: List[str] = Field(
        default_factory=list,
        description="1 to 3 bullet points highlighting what makes this unique."
    )
    branch_suggestions: List[str] = Field(
        default_factory=list,
        description="3 intriguing rabbit hole tangents or follow-up topics."
    )


class FeedItem(BaseModel):
    """A recommended item presented in the Rabbithole feed."""
    video: CandidateVideo
    assessment: QualityAssessment
    source_query: Optional[str] = None
    path_step: Optional[str] = None
    path_reason: Optional[str] = None
    is_discovery: bool = Field(default=False, description="True if selected as a surprising cross-domain discovery.")
    discovery_type: Optional[Literal["adjacent", "distant", "wildcard", "independent"]] = Field(
        default=None,
        description="The discovery tier: 'adjacent', 'distant', 'wildcard', or 'independent'."
    )
    discovery_connection: Optional[str] = Field(default=None, description="Explainable rationale connecting discovery to user taste.")
    discovery_signal: Optional[str] = Field(default=None, description="The specific user signal or experiment that led to this discovery.")
    category: Optional[str] = Field(default=None, description="Interest category this video was surfaced under.")


class FeedResponse(BaseModel):
    """The complete curated feed returned to the client."""
    items: List[FeedItem]
    generated_queries: List[CuriosityQuery]
    total_candidates_analyzed: int
    filtered_out_count: int
    seen_count: int = Field(default=0, description="Total number of previously seen videos tracked.")
    pool_exhausted: bool = Field(default=False, description="True if available candidate pool is smaller than requested path length.")
    status_message: str
    discovery_count: int = Field(default=0, description="Number of discovery slot items included in the feed.")
