"""
Two-Tier Anti-Sensationalism & Curiosity Evaluation Engine.
Tier 1: Fast deterministic heuristic gating (caps, clickbait patterns, duration).
Tier 2: LLM-based intellectual depth, novelty, and contextual connection scoring.
"""
import os
import re
import json
import logging
import certifi
from typing import List, Dict, Tuple, Optional, Literal, Any
from dotenv import load_dotenv

from rabbithole_app.models import (
    CandidateVideo,
    QualityAssessment,
    FeedItem,
    UserPreferences,
    RelevanceAssessment,
    DislikeRecord,
    FollowedCreator,
    EnrichedVideoMetadata,
)
from rabbithole_app.relevance_gate import RelevanceGate
from rabbithole_app.enricher import CandidateEnricher, EnrichmentCache

# Ensure SSL and network proxy resilience
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()
logger = logging.getLogger(__name__)

# Heuristic patterns for clickbait / sensationalism
CLICKBAIT_PATTERNS = [
    r"\b(shocking|you won'?t believe|mind[- ]blowing|insane|crazy|exposed)\b",
    r"\b(the truth about|they don'?t want you to know|ruined my life|gone wrong)\b",
    r"\b(i tried|i spent \d+|i bought|stop doing this|do this instead)\b",
    r"\b(top \d+|best \d+ in 20\d\d|ultimate guide|tier list)\b",
    r"\b(never do this|secret trick|game[- ]changer|warning|must watch)\b",
    r"\b(reaction|reacts to|drama|beef)\b",
]
CLICKBAIT_REGEX = re.compile("|".join(CLICKBAIT_PATTERNS), re.IGNORECASE)

EXCESSIVE_PUNCTUATION_REGEX = re.compile(r"(!{2,}|\?{2,}|\?!|\!\?)")

# Explicit educational explainer / lecture patterns that disqualify videos from entertainment-first
EDUCATIONAL_EXPLAINER_PATTERNS = [
    r"\b(video essay|documentary essay|analytical essay|architecture essay|architectural essay)\b",
    r"\b(the math behind|mathematics of|math of|calculus of|geometry of|physics of)\b",
    r"\b(sound synthesis breakdown|synthesizer architecture|fourier transform)\b",
    r"\b(technical breakdown|engine breakdown|mechanics breakdown|architectural breakdown|systems breakdown)\b",
    r"\b(non-euclidean games|non-euclidean game engine|hyperbolic engine|game engine breakdown)\b",
    r"\b(soviet economics|economic history|macroeconomics|microeconomics|central planning economics|economics of)\b",
    r"\b(lecture|symposium|colloquium|seminar|keynote lecture|university lecture|academics?)\b",
    r"\b(first principles|theoretical framework|formal analysis|paper explained|arxiv breakdown)\b",
    r"\b(how .* (works|engine works|functions|is calculated|is structured))\b",
]
EDUCATIONAL_EXPLAINER_REGEX = re.compile("|".join(EDUCATIONAL_EXPLAINER_PATTERNS), re.IGNORECASE)

# Distinct entertainment format markers
ENTERTAINMENT_FORMAT_PATTERNS: Dict[str, List[str]] = {
    "sports_moments": [
        r"\b(sports moments|sports highlights|athletic highlights|buzzer beater|walk-?off|grand slam|dunk|insane goal|knockout|clutch play|trick shot|game winner|touchdown|hat trick)\b"
    ],
    "gameplay": [
        r"\b(gameplay|playthrough|lets play|let's play|speedrun|clutch gameplay|gaming clutch|blind run|gaming moments|modded run|boss fight|hardcore run)\b"
    ],
    "comedy": [
        r"\b(comedy|comedic|funny|hilarious|bloopers?|standup|stand-up|skit|sketch|parody|satire|roast|improv|prank|fails?)\b"
    ],
    "challenges": [
        r"\b(challenge|100 days|survived|tournament|competition|race|world record|beat the game|hardcore challenge)\b"
    ],
    "performance": [
        r"\b(live performance|concert|musical cover|jamming|orchestral performance|solo performance|improvised music|dance performance|live acoustic)\b"
    ],
    "animation": [
        r"\b(animated short|animation|cgi short|stop motion|claymation|cartoon short|anime short|3d animation)\b"
    ],
    "creative_projects": [
        r"\b(rube goldberg|we built|i built|making a giant|creative build|satisfying build|miniature craft|kinetic sculpture|artisan build|restoration project)\b"
    ],
    "compelling_story": [
        r"\b(bizarre story|unbelievable tale|heist|wild adventure|stranger than fiction|insane story|lore breakdown|narrative lore|mythology|crazy tale)\b"
    ],
}

EVALUATION_SYSTEM_PROMPT = """
You are the Quality, Grounded Evidence & Serendipity Judge for Rabbithole.
Your goal is to evaluate candidate YouTube videos against strict standards of:
1. Anti-Sensationalism (penalize clickbait, deceptive hype, cheap drama).
2. Intellectual Substance or Creative Craft (reward depth, rigorous research, artisan craft, creative animation, or original thought).
3. Entertainment Value & Wit (reward genuine humor, witty commentary, creative storytelling, engaging presentation, or satisfying artisan craftsmanship).
4. Novelty & Serendipity (reward overlooked archives, niche expertise, and unexpected connections).

CRITICAL GROUNDING RULES:
Base your assessment strictly on the provided metadata (title, channel, tags, categories, description) and, when provided, transcript excerpts.
NEVER claim to have watched the video. State clearly whether judgments are derived from captions or metadata.
Captions are optional. Many outstanding videos (visual arts, animations, music performances, artisan craft) have no spoken words or transcripts; do NOT penalize a video or lower its quality score simply because captions are unavailable. Evaluate it fairly from available metadata.

CRITICAL ENTERTAINMENT-FIRST VS. ENGAGING EDUCATION DISTINCTION:
An educational video essay, university lecture, mathematical breakdown (e.g. 'The Math Behind Music', non-Euclidean engine breakdown), architecture analysis, or economics lecture is NOT entertainment-first, even if it has polished visuals, humor, or high craft.
Mark 'is_entertainment_first' = true ONLY when metadata provides evidence that the primary appeal is enjoyment: comedy, gameplay, challenges, sports moments, live performance/music, animation, creative projects, or compelling narrative lore/tales.
If metadata is sparse, ambiguous, or lacks clear entertainment markers, mark 'is_entertainment_first' = false and represent uncertainty honestly.

For each candidate video, produce:
- actual_subject: The specific, factual core topic of the video (e.g., 'Fourier transform mathematics', 'NBA clutch defense', 'Soviet brutalist architecture').
- specific_angle: The specific pedagogical thesis, unique approach, or application (e.g., 'Visual geometric explanation of circular winding frequencies', 'Practical application of Fourier transforms in musical acoustics and sound synthesis', 'Comedy compilation of historical architectural planning mistakes').
- format: One of 'explanation', 'gameplay', 'comedy', 'documentary', 'performance', 'creative_projects', 'compelling_story', 'challenge', 'essay', or 'tutorial'.
- likely_audience: One of 'beginner', 'enthusiast', 'practitioner', 'general', or 'academic'.
- appeal_type: One of 'pure_entertainment', 'engaging_education', 'deep_education', or 'balanced_blend'.
- appeal_ratio: 0.0 (pure educational) to 1.0 (pure entertainment).
- confidence: 0.0 to 1.0 representing confidence in this evaluation based on depth of available evidence.
- evidence_source: 'metadata_and_captions' if captions were provided, else 'metadata'.
- clickbait_score: 0.0 (honest, descriptive) to 1.0 (pure sensationalism / emotional bait).
- substance_score: 0.0 (empty fluff / ad farm) to 1.0 (dense, rigorous, deeply informative).
- novelty_score: 0.0 (standard mainstream cliché) to 1.0 (hidden gem, lateral, unexpected).
- entertainment_score: 0.0 (dry / boring / uninspired) to 1.0 (creative, funny, engaging, or satisfying craft).
- content_nature: "educational", "entertaining", or "blend".
- is_entertainment_first: boolean (true ONLY if primary appeal is enjoyment/entertainment, false for lectures, explainers, essays).
- entertainment_format: null or one of "comedy", "gameplay", "challenges", "sports_moments", "performance", "animation", "creative_projects", "compelling_story".
- entertainment_evidence: brief 1-sentence note stating why metadata supports (or does not support) entertainment-first status.
- connection_note: 1-2 sharp sentences explaining why metadata indicates this rabbit hole is worth exploring.
- key_takeaways: 1 to 2 distinct things the metadata suggests the viewer will discover.
- branch_suggestions: 3 intriguing follow-up rabbit holes or concepts sparked by this video.
"""

PRIMER_WORDS: set[str] = {
    "intro", "introduction", "introductory", "primer", "fundamentals", "fundamental",
    "basics", "basic", "overview", "explained", "explaining", "simple", "simply",
    "intuitive", "guide", "visual", "what", "really", "learn", "understanding", "tutorial"
}

DISTINCT_APPLICATION_TERMS: set[str] = {
    "music", "audio", "sound", "acoustics", "synthesis", "synthesizer", "timbre", "harmonics",
    "game", "games", "gaming", "engine", "shaders", "graphics", "rendering",
    "compression", "jpeg", "mp3", "dsp", "telecom", "signal",
    "quantum", "wavefunction", "spectroscopy",
    "biology", "genetics", "dna", "medicine", "mri", "imaging",
    "finance", "trading", "stock", "market",
    "robotics", "control", "pid", "aerospace"
}


def are_topics_substantially_duplicate(left: FeedItem, right: FeedItem) -> bool:
    """
    Determines if two videos cover essentially the same idea or primer,
    versus a distinct domain application.
    Grounded in structured fields (actual_subject, specific_angle, format)
    with fallback to title topic terms.
    """
    l_ev = getattr(left, "assessment", None)
    r_ev = getattr(right, "assessment", None)

    l_title = (left.video.title or "").lower()
    r_title = (right.video.title or "").lower()

    # 1. Check structured fields if present
    l_sub = (getattr(l_ev, "actual_subject", None) or "").strip().lower()
    r_sub = (getattr(r_ev, "actual_subject", None) or "").strip().lower()
    l_angle = (getattr(l_ev, "specific_angle", None) or "").strip().lower()
    r_angle = (getattr(r_ev, "specific_angle", None) or "").strip().lower()

    l_full = f"{l_title} {l_sub} {l_angle}"
    r_full = f"{r_title} {r_sub} {r_angle}"

    # Check for distinct applications
    l_apps = {w for w in re.findall(r"[a-z0-9]+", l_full) if w in DISTINCT_APPLICATION_TERMS}
    r_apps = {w for w in re.findall(r"[a-z0-9]+", r_full) if w in DISTINCT_APPLICATION_TERMS}

    # If one video is a distinct application (e.g. music/audio, game engine, compression)
    # and the other is not in that application (or has a different application):
    # they are NOT duplicates!
    if l_apps != r_apps and (bool(l_apps) != bool(r_apps) or not (l_apps & r_apps)):
        return False

    # Check subject alignment
    if l_sub and r_sub:
        l_sub_terms = {w for w in re.findall(r"[a-z0-9]+", l_sub) if len(w) > 3}
        r_sub_terms = {w for w in re.findall(r"[a-z0-9]+", r_sub) if len(w) > 3}

        # If subjects are distinct concepts (e.g. Fourier vs Calculus, or Basketball vs Baseball):
        if l_sub_terms and r_sub_terms and not (l_sub_terms & r_sub_terms):
            return False

        # If subjects match:
        if (l_sub in r_sub) or (r_sub in l_sub) or (len(l_sub_terms & r_sub_terms) >= 1):
            # Check if both are introductory primers
            l_is_primer = any(pw in l_full for pw in PRIMER_WORDS)
            r_is_primer = any(pw in r_full for pw in PRIMER_WORDS)
            if l_is_primer and r_is_primer:
                return True

            # Check angle similarity
            l_angle_terms = {w for w in re.findall(r"[a-z0-9]+", l_angle) if len(w) > 3} - PRIMER_WORDS
            r_angle_terms = {w for w in re.findall(r"[a-z0-9]+", r_angle) if len(w) > 3} - PRIMER_WORDS
            if l_angle_terms and r_angle_terms:
                shared = len(l_angle_terms & r_angle_terms)
                union = len(l_angle_terms | r_angle_terms)
                if shared / union >= 0.5:
                    return True

    # 2. Fallback to title token overlap
    from rabbithole_app.journey import _terms, _TITLE_FILLER
    l_terms = _terms(l_title) - _TITLE_FILLER
    r_terms = _terms(r_title) - _TITLE_FILLER
    shared = len(l_terms & r_terms)
    if shared < 2:
        return False
    union = len(l_terms | r_terms)
    return (shared / union) >= 0.6


def _infer_heuristic_structured_fields(
    cand: CandidateVideo,
    is_entertainment_first: bool,
    entertainment_format: Optional[str],
    content_nature: str,
    substance_score: float,
    entertainment_score: float,
    has_captions: bool = False
) -> Dict[str, Any]:
    """Infers structured, evidence-grounded fields from available metadata and captions."""
    title_lower = (cand.title or "").lower()
    desc_lower = (cand.description or "").lower()
    full_text = f"{title_lower} {desc_lower}"

    # 1. Actual Subject
    subject = cand.source_domain or ""
    if "fourier transform" in title_lower:
        subject = "Fourier Transform"
    elif "linear algebra" in title_lower or "eigenvector" in title_lower:
        subject = "Linear Algebra"
    elif "calculus" in title_lower or "derivative" in title_lower:
        subject = "Calculus"
    elif "non-euclidean" in title_lower:
        subject = "Non-Euclidean Geometry"
    elif "soviet architecture" in title_lower or "brutalist" in title_lower:
        subject = "Soviet Architecture"
    elif "soviet economics" in title_lower:
        subject = "Soviet Economics"
    elif "basketball" in title_lower or "nba" in title_lower:
        subject = "Basketball"
    elif "baseball" in title_lower or "mlb" in title_lower:
        subject = "Baseball"
    elif getattr(cand, "tags", None):
        subject = cand.tags[0].title()
    elif not subject:
        words = [w for w in re.findall(r"[a-z0-9]+", cand.title) if len(w) > 3 and w.lower() not in PRIMER_WORDS]
        subject = " ".join(words[:2]).title() if words else (cand.title[:30] or "General Discovery")

    # 2. Specific Angle
    app_matches = [w for w in re.findall(r"[a-z0-9]+", full_text) if w in DISTINCT_APPLICATION_TERMS]
    is_primer = any(pw in full_text for pw in PRIMER_WORDS)
    if app_matches:
        specific_angle = f"Application of {subject} in {', '.join(set(app_matches[:2]))}"
    elif is_primer:
        specific_angle = f"Visual introductory primer on core foundations of {subject}"
    elif "history" in full_text or "evolution" in full_text:
        specific_angle = f"Historical evolution and documentation of {subject}"
    else:
        specific_angle = f"Exploration of {cand.title}"

    # 3. Format
    if is_entertainment_first and entertainment_format:
        content_format = entertainment_format
    elif any(w in title_lower for w in ("video essay", "essay", "documentary essay")):
        content_format = "essay"
    elif any(w in title_lower for w in ("lecture", "seminar", "symposium", "colloquium")):
        content_format = "lecture"
    elif any(w in title_lower for w in ("tutorial", "walkthrough", "how to")):
        content_format = "tutorial"
    elif any(w in title_lower for w in ("documentary", "chronicle")):
        content_format = "documentary"
    elif any(w in title_lower for w in ("performance", "concert", "cover")):
        content_format = "performance"
    else:
        content_format = "explanation"

    # 4. Likely Audience
    if any(w in title_lower for w in ("beginner", "intro", "primer", "basics", "simple")):
        likely_audience = "beginner"
    elif any(w in title_lower for w in ("advanced", "masterclass", "practitioner", "deep dive")):
        likely_audience = "practitioner"
    elif any(w in title_lower for w in ("lecture", "university", "formal", "theory", "academic")):
        likely_audience = "academic"
    elif is_entertainment_first:
        likely_audience = "general"
    else:
        likely_audience = "enthusiast"

    # 5. Appeal Type & Ratio
    if is_entertainment_first and entertainment_score >= 0.70:
        appeal_type = "pure_entertainment"
    elif substance_score >= 0.65 and entertainment_score <= 0.38:
        appeal_type = "deep_education"
    elif substance_score >= 0.55 and entertainment_score >= 0.55:
        appeal_type = "engaging_education"
    else:
        appeal_type = "balanced_blend"

    total_appeal = entertainment_score + substance_score
    appeal_ratio = round(entertainment_score / max(total_appeal, 0.01), 2)

    # 6. Confidence & Evidence Source
    if has_captions:
        confidence = 0.88
        evidence_source = "metadata_and_captions"
    elif getattr(cand, "tags", None) and len(cand.description or "") > 200:
        confidence = 0.78
        evidence_source = "metadata"
    else:
        confidence = 0.65
        evidence_source = "metadata"

    return {
        "actual_subject": subject,
        "specific_angle": specific_angle,
        "content_format": content_format,
        "likely_audience": likely_audience,
        "appeal_type": appeal_type,
        "appeal_ratio": appeal_ratio,
        "confidence": confidence,
        "evidence_source": evidence_source,
    }



def _clean_json_markdown(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def summarize_dislike_patterns(records: List[DislikeRecord]) -> str:
    """Creates a compact summary of user dislike patterns for Gemini and heuristics."""
    if not records:
        return ""

    reason_counts: Dict[str, int] = {}
    examples: List[str] = []

    for r in records:
        r_reason = r.chosen_reason or "No reason given"
        reason_counts[r_reason] = reason_counts.get(r_reason, 0) + 1
        title_snippet = (r.title[:45] + "...") if len(r.title) > 45 else r.title
        examples.append(f"'{title_snippet}' ({r.channel}): {r_reason}")

    reasons_str = ", ".join(f"{cnt}x '{reason}'" for reason, cnt in reason_counts.items())
    recent_examples = "; ".join(examples[-4:])

    return (
        f"Summary of Disliked Patterns ({len(records)} total): {reasons_str}. "
        f"Recent rejected samples: [{recent_examples}]. "
        f"Guidance: Suppress these specific candidates and temper similar rejected formats/tones. "
        f"Do NOT ban entire broad topics or creators unless explicitly instructed."
    )


def summarize_followed_creators(creators: List[FollowedCreator]) -> str:
    """Compact summary explaining user's followed creators to Gemini and heuristics."""
    if not creators:
        return ""

    creator_entries = []
    for c in creators:
        if c.channel_id:
            creator_entries.append(f"{c.name} (channel_id: {c.channel_id})")
        else:
            creator_entries.append(c.name)

    creators_str = ", ".join(creator_entries)
    return (
        f"Followed Creators: {creators_str}\n"
        f"  Guidance: A follow means 'I enjoy this creator’s work and want you to understand my taste.' "
        f"Gemini may occasionally search for or recommend their videos, but should still seek new creators "
        f"and surprising connections. Following should not guarantee a slot, bypass quality filters, "
        f"or turn a category tab into a channel feed."
    )


def summarize_interest_state(preferences: UserPreferences) -> str:
    """Creates a compact summary of the user's saved interest state, categories, emerging signals, and feedback."""
    parts = []
    if preferences.categories:
        parts.append(f"Saved Categories: {', '.join(preferences.categories)}")
    if preferences.emerging_interests:
        parts.append(f"Emerging Interests (detected from discovery actions): {', '.join(preferences.emerging_interests)}")
    if preferences.dismissed_categories:
        parts.append(f"Dismissed Categories (do not recreate): {', '.join(preferences.dismissed_categories)}")
    if preferences.watched_topics:
        parts.append(f"Actively Watched Topics: {', '.join(preferences.watched_topics[:4])}")

    if preferences.living_taste_profile:
        profile_summary = preferences.living_taste_profile.compact_summary()
        if profile_summary:
            parts.append(profile_summary)

    followed_summary = summarize_followed_creators(preferences.followed_creators)
    if followed_summary:
        parts.append(followed_summary)

    dislike_summary = summarize_dislike_patterns(preferences.disliked_records)
    if dislike_summary:
        parts.append(f"User Dislike Feedback / Patterns (from current session history):\n  {dislike_summary}")

    if not parts:
        return ""
    return "User Interest State & Session Memory (managed locally by Rabbithole):\n" + "\n".join(f"- {p}" for p in parts)


def upload_recency_boost(candidate, preferences):
    from datetime import datetime, timezone
    value = str(candidate.upload_date or "").replace("-", "")[:8]
    try:
        date = datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    age = max(0, (datetime.now(timezone.utc) - date).days)
    if preferences.recency_preference == "recent":
        return 0.18 * max(0.0, 1.0 - age / 730)
    if preferences.recency_preference == "older":
        return 0.12 * min(1.0, age / 3650)
    return 0.0


class FilterEngine:
    def __init__(self, api_key: Optional[str] = None, enricher: Optional[CandidateEnricher] = None):
        if api_key is not None:
            self.api_key = api_key.strip() if api_key.strip() else None
        else:
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.relevance_gate = RelevanceGate(api_key=self.api_key)
        self.enricher = enricher if enricher is not None else CandidateEnricher()
        self.last_mode: str = "uninitialized"
        self.last_error: Optional[str] = None

    def run_tier1_heuristics(
        self,
        candidate: CandidateVideo,
        preferences: UserPreferences
    ) -> Tuple[bool, float, str]:
        """
        Fast heuristic check.
        Returns (passed, initial_clickbait_score, rejection_reason).
        """
        # Duration filtering
        min_sec = preferences.min_duration_minutes * 60
        if candidate.duration_seconds is not None:
            if candidate.duration_seconds < min_sec:
                return False, 1.0, f"Duration ({candidate.duration_seconds}s) below minimum {min_sec}s"
            if preferences.max_duration_minutes:
                max_sec = preferences.max_duration_minutes * 60
                if candidate.duration_seconds > max_sec:
                    return False, 0.5, f"Duration exceeds maximum {max_sec}s"

        title = candidate.title

        # Check all-caps ratio in title
        letters = [c for c in title if c.isalpha()]
        if len(letters) >= 12:
            upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            if upper_ratio > 0.45:
                return False, 0.85, "Excessive ALL CAPS in title"

        # Check sensationalist clickbait regex
        matches = CLICKBAIT_REGEX.findall(title)
        punct_matches = EXCESSIVE_PUNCTUATION_REGEX.findall(title)
        
        clickbait_score = 0.0
        if matches:
            clickbait_score += 0.35 * len(matches)
        if punct_matches:
            clickbait_score += 0.30

        if preferences.avoid_sensationalism and clickbait_score >= 0.60:
            return False, clickbait_score, f"Sensationalist pattern detected: {matches or punct_matches}"

        return True, min(clickbait_score, 0.5), "Passed Tier 1"

    def filter_and_rank(
        self,
        candidates: List[CandidateVideo],
        preferences: UserPreferences,
        top_k: int = 8,
        enable_enrichment: bool = True
    ) -> Tuple[List[FeedItem], int]:
        """
        Two-stage evaluation pipeline:
        Stage 1: Fast deterministic heuristic gating (duration, caps, clickbait patterns,
                 disliked/completed/seen exclusions) and inexpensive relevance gate checks.
        Stage 2: For a bounded shortlist of promising candidates, fetch fuller metadata and captions
                 via CandidateEnricher, run evidence-grounded evaluation (Gemini or heuristic),
                 and rerank with followed creator preference, living taste, and repeated-topic suppression.
        """
        tier1_passed: List[CandidateVideo] = []
        rel_map: Dict[str, RelevanceAssessment] = {}
        filtered_count = 0
        seen_set = set(preferences.seen_video_ids or [])
        completed_set = set(preferences.completed_video_ids or [])
        disliked_set = set(preferences.disliked_video_ids or [])

        for cand in candidates:
            # Exclude disliked videos immediately across all feeds
            if cand.id in disliked_set:
                filtered_count += 1
                logger.debug(f"Filtered out disliked video '{cand.id}' ('{cand.title}')")
                continue

            # Exclude already-completed videos (never recommend completed videos again)
            if cand.id in completed_set:
                filtered_count += 1
                logger.debug(f"Filtered out completed video '{cand.id}' ('{cand.title}')")
                continue

            # On main feed, exclude already-seen videos across page reloads/refreshes.
            # On focused category tabs, allow previously shown videos to return if fresh pool is sparse.
            if not preferences.active_category and cand.id in set(preferences.recent_shown_video_ids):
                filtered_count += 1
                logger.debug(f"Filtered out previously seen video '{cand.id}' ('{cand.title}')")
                continue

            passed, cb_score, reason = self.run_tier1_heuristics(cand, preferences)
            if not passed:
                filtered_count += 1
                logger.debug(f"Filtered out '{cand.title}': {reason}")
                continue

            # Run Relevance Gate before videos can enter the viewing path pool
            rel = self.relevance_gate.assess_candidate(cand, preferences)
            if not rel.is_relevant:
                filtered_count += 1
                logger.info(f"Relevance gate rejected '{cand.title}': {rel.reason}")
                continue

            rel_map[cand.id] = rel
            tier1_passed.append(cand)

        if not tier1_passed:
            return [], filtered_count

        # Select a diverse shortlist of promising candidates across queries, topics, and creators
        shortlist_bound = min(len(tier1_passed), min(max(top_k, 12), 15))
        shortlist = self.select_diverse_shortlist(tier1_passed, preferences, rel_map, limit=shortlist_bound)

        enriched_map: Dict[str, EnrichedVideoMetadata] = {}
        if enable_enrichment:
            try:
                enriched_map = self.enricher.enrich_shortlist(shortlist)
                for cand in shortlist:
                    em = enriched_map.get(cand.id)
                    if em:
                        cand.upload_date = em.upload_date or cand.upload_date
                        cand.duration_seconds = em.duration_seconds or cand.duration_seconds
                        cand.channel_id = em.channel_id or cand.channel_id
                        cand.tags = em.tags
                        cand.categories = em.categories
                        cand.full_description = em.description
                        cand.transcript_summary = em.transcript_summary
                        cand.has_captions = em.has_captions
                        cand.evidence_source = em.evidence_source
                        if em.description and not cand.description:
                            cand.description = em.description
            except Exception as e:
                logger.warning(f"Shortlist enrichment failed: {e}. Proceeding with existing candidate metadata.")

        # Stage 2 Evaluation (Gemini or heuristic fallback)
        scored_items = self._run_tier2_evaluation(shortlist, preferences, rel_map)

        # Stage 2 Reranking with followed creator preference, living taste, and repeated-topic suppression
        reranked_items = self._rerank_shortlist(scored_items, preferences, top_k=top_k)

        # If shortlist reranking produced fewer than top_k items and leftover candidates exist, evaluate leftover
        shortlist_ids = {c.id for c in shortlist}
        leftover = [c for c in tier1_passed if c.id not in shortlist_ids]
        if len(reranked_items) < top_k and leftover:
            leftover_scored = self._evaluate_with_heuristics(leftover, preferences, rel_map)
            for it in leftover_scored:
                if len(reranked_items) >= top_k:
                    break
                if not any(are_topics_substantially_duplicate(it, prior) for prior in reranked_items):
                    reranked_items.append(it)

        return reranked_items[:top_k], filtered_count

    def select_diverse_shortlist(
        self,
        candidates: List[CandidateVideo],
        preferences: UserPreferences,
        rel_map: Dict[str, Any],
        limit: int = 15,
    ) -> List[CandidateVideo]:
        """
        Selects a diverse shortlist of promising candidates across distinct queries,
        topics, and creators before filling remaining slots by cheap relevance and quality signals.
        Preserves focused interest tabs and genuine discovery slots.
        Avoids search-order monopolization (e.g. one query or creator taking all slots).
        """
        if not candidates or len(candidates) <= limit:
            return list(candidates)

        def cheap_pre_score(cand: CandidateVideo) -> float:
            score = 0.5
            rel = rel_map.get(cand.id)
            if rel:
                score += (getattr(rel, "confidence", 0.5) - 0.5) * 0.4
                status = getattr(rel, "status", "")
                if status == "supported":
                    score += 0.25
                elif status == "uncertain":
                    score -= 0.15

            # Duration signal (favor substantive content 5m - 50m)
            dur = cand.duration_seconds or 0
            if 300 <= dur <= 3000:
                score += 0.10
            elif 180 <= dur < 300 or 3000 < dur <= 5400:
                score += 0.05
            elif 0 < dur < 90:
                score -= 0.15

            # View count signal
            if cand.view_count and cand.view_count > 25000:
                score += 0.05

            # Followed creator preference
            if preferences.followed_creators:
                for fc in preferences.followed_creators:
                    if fc.matches(cand.channel, cand.channel_id):
                        score += 0.20
                        break

            # Living taste profile match
            if preferences.living_taste_profile:
                lp = preferences.living_taste_profile
                title_lower = cand.title.lower()
                for q, _ in lp.get_top_qualities(limit=3):
                    if q.lower() in title_lower:
                        score += 0.03
                for sub, _ in lp.get_established_subjects():
                    if sub.lower() in title_lower:
                        score += 0.04
                        break

            return round(score, 3)

        def is_discovery_cand(cand: CandidateVideo) -> bool:
            qtype = (cand.source_query_type or "").lower()
            dtype = (getattr(cand, "discovery_type", None) or "").lower()
            return bool(
                getattr(cand, "is_discovery", False)
                or qtype in ("wildcard_discovery", "distant_discovery", "lateral_discovery")
                or "discovery" in qtype
                or dtype in ("wildcard", "distant", "adjacent")
            )

        shortlist: List[CandidateVideo] = []
        selected_ids: set[str] = set()
        creator_counts: Dict[str, int] = {}

        def add_candidate(cand: CandidateVideo) -> None:
            shortlist.append(cand)
            selected_ids.add(cand.id)
            creator_key = cand.channel_id or cand.channel or "unknown"
            creator_counts[creator_key] = creator_counts.get(creator_key, 0) + 1

        # 1. On main feed (not a focused interest tab), preserve genuine discovery slots (up to 3 slots)
        if not preferences.active_category:
            discovery_cands = [c for c in candidates if is_discovery_cand(c)]
            if discovery_cands:
                discovery_sorted = sorted(discovery_cands, key=cheap_pre_score, reverse=True)
                discovery_target = min(3, max(1, limit // 4))
                seen_disc_queries = set()
                for dc in discovery_sorted:
                    if len(shortlist) >= discovery_target:
                        break
                    q_key = dc.source_query or dc.source_domain or "discovery"
                    creator_key = dc.channel_id or dc.channel or "unknown"
                    if q_key not in seen_disc_queries and creator_counts.get(creator_key, 0) < 1:
                        add_candidate(dc)
                        seen_disc_queries.add(q_key)

                # Fill remaining discovery target slots if needed
                for dc in discovery_sorted:
                    if len(shortlist) >= discovery_target:
                        break
                    if dc.id not in selected_ids:
                        add_candidate(dc)

        # 2. Partition remaining candidates into distinct query/topic groups
        remaining_pool = [c for c in candidates if c.id not in selected_ids]
        query_groups: Dict[str, List[CandidateVideo]] = {}
        for c in remaining_pool:
            q_key = c.source_query or c.source_domain or c.source_category or "general"
            if q_key not in query_groups:
                query_groups[q_key] = []
            query_groups[q_key].append(c)

        # Sort within each query group by cheap pre-score descending
        for q_key in query_groups:
            query_groups[q_key].sort(key=cheap_pre_score, reverse=True)

        # Pass 1: Select 1 top candidate per distinct query with creator limit < 1
        for q_key, group_cands in list(query_groups.items()):
            if len(shortlist) >= limit:
                break
            for cand in list(group_cands):
                creator_key = cand.channel_id or cand.channel or "unknown"
                if creator_counts.get(creator_key, 0) < 1:
                    add_candidate(cand)
                    group_cands.remove(cand)
                    break

        # Pass 2: Select 2nd candidate per distinct query with creator limit < 2
        for q_key, group_cands in list(query_groups.items()):
            if len(shortlist) >= limit:
                break
            for cand in list(group_cands):
                creator_key = cand.channel_id or cand.channel or "unknown"
                if creator_counts.get(creator_key, 0) < 2:
                    add_candidate(cand)
                    group_cands.remove(cand)
                    break

        # Pass 3: Fill remaining slots up to limit by pre-score with creator saturation penalty
        if len(shortlist) < limit:
            leftover_cands = [c for c in candidates if c.id not in selected_ids]
            def adjusted_fill_score(c: CandidateVideo) -> float:
                creator_key = c.channel_id or c.channel or "unknown"
                penalty = creator_counts.get(creator_key, 0) * 0.15
                return cheap_pre_score(c) - penalty

            leftover_sorted = sorted(leftover_cands, key=adjusted_fill_score, reverse=True)
            for c in leftover_sorted:
                if len(shortlist) >= limit:
                    break
                add_candidate(c)

        return shortlist

    def _rerank_shortlist(
        self,
        items: List[FeedItem],
        preferences: UserPreferences,
        top_k: int = 8
    ) -> List[FeedItem]:
        """
        Reranks the enriched shortlist using structured fields, followed creator preference,
        living taste profile, and repeated-topic suppression.
        Avoids placing several videos that cover essentially the same idea in one feed.
        When comparable primers compete, followed creator (e.g. 3Blue1Brown) receives meaningful preference,
        while distinct applications (e.g. Fourier in music) remain preserved.
        """
        if not items:
            return []

        # In entertainment feed mode, only entertainment-first items should be admitted
        if preferences.feed_mode == "entertainment":
            items = [it for it in items if getattr(it.assessment, "is_entertainment_first", False)]
            if not items:
                return []

        # 1. Base scores with taste alignment and followed creator preference
        scores: Dict[str, float] = {}
        seen_ids = set(preferences.seen_video_ids or [])

        for it in items:
            ev = it.assessment
            cand = it.video
            base = ev.composite_score

            # Followed creator preference: Meaningful preference (+0.20)
            followed_boost = 0.0
            if preferences.followed_creators:
                for fc in preferences.followed_creators:
                    if fc.matches(cand.channel, cand.channel_id):
                        followed_boost = 0.20
                        break

            # Freshness bonus
            freshness = 0.08 if cand.id not in seen_ids else 0.0

            # Living taste profile match
            taste_boost = 0.0
            if preferences.living_taste_profile:
                lp = preferences.living_taste_profile
                cand_text = f"{cand.title} {getattr(ev, 'actual_subject', '') or ''} {getattr(ev, 'specific_angle', '') or ''}".lower()
                top_q = [q for q, _ in lp.get_top_qualities(limit=3)]
                for q in top_q:
                    if q.lower() in cand_text:
                        taste_boost += 0.03
                for sub, _ in lp.get_established_subjects():
                    if sub.lower() in cand_text:
                        taste_boost += 0.04
                        break
            taste_boost = min(taste_boost, 0.08)

            scores[cand.id] = round(base + followed_boost + freshness + taste_boost, 3)

        # 2. Greedy diverse selection with repeated-topic suppression
        selected: List[FeedItem] = []
        used_channels: set[str] = set()
        pool = list(items)

        while pool and len(selected) < top_k:
            def selection_utility(it: FeedItem) -> float:
                score = scores[it.video.id]

                # Channel diversity penalty
                if it.video.channel in used_channels:
                    score -= 0.15

                # Repeated-topic suppression against already selected items
                for prior in selected:
                    if are_topics_substantially_duplicate(it, prior):
                        score -= 0.50
                        break

                return score

            best = max(pool, key=selection_utility)
            pool.remove(best)
            selected.append(best)
            used_channels.add(best.video.channel)

        return selected

    def _run_tier2_evaluation(
        self,
        candidates: List[CandidateVideo],
        preferences: UserPreferences,
        rel_map: Optional[Dict[str, RelevanceAssessment]] = None
    ) -> List[FeedItem]:
        """Runs Tier 2 evaluation via Gemini if key is available, else heuristic fallback."""
        if self.api_key:
            try:
                items = self._evaluate_with_gemini(candidates, preferences, rel_map)
                self.last_mode = "gemini"
                self.last_error = None
                return items
            except Exception as e:
                self.last_mode = "gemini_failed_fallback"
                self.last_error = str(e)
                logger.warning(f"Tier 2 Gemini evaluation failed: {e}. Falling back to heuristic evaluation.")
        else:
            self.last_mode = "no_key_heuristic"
            self.last_error = None

        return self._evaluate_with_heuristics(candidates, preferences, rel_map)

    def _evaluate_with_gemini(
        self,
        candidates: List[CandidateVideo],
        preferences: UserPreferences,
        rel_map: Optional[Dict[str, RelevanceAssessment]] = None
    ) -> List[FeedItem]:
        from google import genai
        from google.genai import types
        from pydantic import BaseModel, Field

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                client_args={"trust_env": False, "timeout": 20.0}
            )
        )

        class SingleAssessment(BaseModel):
            video_id: str
            actual_subject: Optional[str] = None
            specific_angle: Optional[str] = None
            format: Optional[str] = "explanation"
            likely_audience: Optional[str] = "general"
            appeal_type: Optional[str] = "balanced_blend"
            appeal_ratio: Optional[float] = 0.5
            confidence: Optional[float] = 0.7
            evidence_source: Optional[Literal["metadata", "captions", "metadata_and_captions", "fallback"]] = "metadata"
            clickbait_score: float = 0.0
            substance_score: float = 0.5
            novelty_score: float = 0.5
            entertainment_score: Optional[float] = 0.5
            content_nature: Optional[Literal["educational", "entertaining", "blend"]] = "blend"
            is_entertainment_first: Optional[bool] = False
            entertainment_format: Optional[Literal[
                "comedy", "gameplay", "challenges", "sports_moments",
                "performance", "animation", "creative_projects", "compelling_story", "spectacle"
            ]] = None
            entertainment_evidence: Optional[str] = None
            connection_note: str = ""
            key_takeaways: List[str] = Field(default_factory=list)
            branch_suggestions: List[str] = Field(default_factory=list)
            relevance_status: Optional[str] = "supported"
            relevance_reason: Optional[str] = ""

        class BatchAssessmentResponse(BaseModel):
            evaluations: List[SingleAssessment]

        candidates_payload = [
            {
                "id": c.id,
                "title": c.title,
                "channel": c.channel,
                "duration": c.duration_formatted,
                "tags": getattr(c, "tags", [])[:8],
                "categories": getattr(c, "categories", [])[:3],
                "description_preview": (getattr(c, "full_description", None) or c.description or "")[:500],
                "has_captions": getattr(c, "has_captions", False),
                "transcript_excerpt": (getattr(c, "transcript_summary", None) or "")[:1200] if getattr(c, "has_captions", False) else None,
                "evidence_source": getattr(c, "evidence_source", None) or ("metadata_and_captions" if getattr(c, "has_captions", False) else "metadata"),
            }
            for c in candidates
        ]

        interest_state_section = ""
        interest_state_summary = summarize_interest_state(preferences)
        if interest_state_summary:
            interest_state_section = f"\n{interest_state_summary}\n"

        followed_section = ""
        if preferences.followed_creators:
            followed_summary = summarize_followed_creators(preferences.followed_creators)
            if followed_summary:
                followed_section = f"\n{followed_summary}\n"

        prompt = f"""
        User Interests: "{preferences.interests}"
        Active Category: {preferences.active_category or 'All / Main Feed'}
        Feed Mode: {preferences.feed_mode}
        Experience Type: {preferences.experience_type}
        Serendipity Level (1-5): {preferences.serendipity_level}
        Depth Target: {preferences.depth_level}
        {followed_section}
        {interest_state_section}
        Evaluate each candidate video in this batch according to the system rubric:
        {json.dumps(candidates_payload, indent=2)}
        """

        interaction = client.interactions.create(
            model="gemini-3.5-flash-lite",
            system_instruction=EVALUATION_SYSTEM_PROMPT,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": BatchAssessmentResponse.model_json_schema()
            }
        )

        clean_text = _clean_json_markdown(interaction.output_text or "")
        batch_res = BatchAssessmentResponse.model_validate_json(clean_text)
        eval_map = {e.video_id: e for e in batch_res.evaluations}

        feed_items: List[FeedItem] = []
        for cand in candidates:
            ev = eval_map.get(cand.id)
            rel = rel_map.get(cand.id) if rel_map else None
            if ev:
                cb = min(max(ev.clickbait_score, 0.0), 1.0)
                sub = min(max(ev.substance_score, 0.0), 1.0)
                nov = min(max(ev.novelty_score, 0.0), 1.0)
                ent = min(max(ev.entertainment_score if ev.entertainment_score is not None else 0.5, 0.0), 1.0)

                cand_meta = f"{cand.title} {cand.channel} {cand.description or ''}".lower()
                edu_matches = [m.group(0) for m in EDUCATIONAL_EXPLAINER_REGEX.finditer(cand_meta)]

                is_ent_first = bool(ev.is_entertainment_first)
                ent_fmt = ev.entertainment_format
                ent_evi = ev.entertainment_evidence

                if edu_matches:
                    # Grounding safeguard: Blatant educational explainers/lectures cannot be entertainment-first
                    is_ent_first = False
                    ent_fmt = None
                    sample_edu = edu_matches[0]
                    ent_evi = f"Metadata exhibits educational explainer / lecture markers ('{sample_edu}'); not entertainment-first."
                    ent = min(ent, 0.35)
                elif not is_ent_first:
                    # Check if metadata provides clear entertainment-first evidence
                    for fmt_name, pats in ENTERTAINMENT_FORMAT_PATTERNS.items():
                        if any(re.search(p, cand_meta, re.IGNORECASE) for p in pats):
                            is_ent_first = True
                            ent_fmt = fmt_name
                            ent_evi = f"Metadata supports entertainment-first appeal in '{fmt_name}'."
                            break

                # Modest viewing signal boost for creators/topics user actually spent time watching
                viewing_boost = 0.0
                if preferences.watched_channels and cand.channel:
                    channel_sec = preferences.watched_channels.get(cand.channel, 0.0)
                    if channel_sec >= 60.0:
                        viewing_boost += min(0.05, 0.02 + (channel_sec / 600.0) * 0.03)
                if preferences.watched_topics:
                    cand_sub = f"{cand.title} {cand.source_domain or ''}".lower()
                    if any(t.lower() in cand_sub for t in preferences.watched_topics if len(t) > 3):
                        viewing_boost += 0.03
                viewing_boost = min(viewing_boost, 0.07)

                # Format taste profile boost
                format_boost = 0.0
                if preferences.living_taste_profile and ent_fmt:
                    pref_fmts = preferences.living_taste_profile.preferred_entertainment_formats
                    if ent_fmt in pref_fmts:
                        format_boost = min(0.06, pref_fmts[ent_fmt] * 0.02)

                # Composite score aligned with feed mode
                if preferences.feed_mode == "entertainment":
                    if not is_ent_first:
                        composite = (ent * 0.35) + (nov * 0.20) - 0.40
                    else:
                        composite = (ent * 0.55) + (nov * 0.30) - (cb * 0.40) + (sub * 0.15) + viewing_boost + format_boost
                elif preferences.feed_mode == "mix":
                    composite = (max(sub, ent) * 0.45) + (nov * 0.35) - (cb * 0.40) + (min(sub, ent) * 0.15) + viewing_boost
                else:
                    composite = (sub * 0.45) + (nov * 0.40) - (cb * 0.50) + viewing_boost

                composite += upload_recency_boost(cand, preferences)

                # Modest score adjustment from dislike feedback without banning entire topics or channels
                dislike_adjustment = 0.0
                if preferences.disliked_records:
                    reasons = [r.chosen_reason for r in preferences.disliked_records if r.chosen_reason]
                    if "Too academic" in reasons and (ev.content_nature == "educational" or not is_ent_first):
                        dislike_adjustment += 0.05
                    if "Too shallow" in reasons and (cand.duration_seconds or 600) < 480:
                        dislike_adjustment += 0.05
                composite = max(0.0, composite - min(dislike_adjustment, 0.08))

                rel_status = ev.relevance_status if ev.relevance_status in ("supported", "uncertain", "rejected") else (rel.status if rel else "supported")
                rel_reason = ev.relevance_reason or (rel.reason if rel else "")

                inferred = _infer_heuristic_structured_fields(
                    cand,
                    is_entertainment_first=is_ent_first,
                    entertainment_format=ent_fmt,
                    content_nature=ev.content_nature or ("entertaining" if is_ent_first else "educational"),
                    substance_score=sub,
                    entertainment_score=ent,
                    has_captions=getattr(cand, "has_captions", False)
                )

                actual_sub = (ev.actual_subject or "").strip() or inferred["actual_subject"]
                specific_ang = (ev.specific_angle or "").strip() or inferred["specific_angle"]
                content_fmt = ev.format or inferred["content_format"]
                likely_aud = ev.likely_audience or inferred["likely_audience"]
                app_type = ev.appeal_type or inferred["appeal_type"]
                app_ratio = ev.appeal_ratio if ev.appeal_ratio is not None else inferred["appeal_ratio"]
                conf = ev.confidence if ev.confidence is not None else inferred["confidence"]
                evi_src = ev.evidence_source or ("metadata_and_captions" if getattr(cand, "has_captions", False) else "metadata")

                assessment = QualityAssessment(
                    clickbait_score=cb,
                    substance_score=sub,
                    novelty_score=nov,
                    composite_score=round(composite, 3),
                    is_sensationalized=(cb > 0.55),
                    entertainment_score=ent,
                    content_nature=ev.content_nature or ("entertaining" if is_ent_first else "educational"),
                    is_entertainment_first=is_ent_first,
                    entertainment_format=ent_fmt,
                    entertainment_evidence=ent_evi or "",
                    actual_subject=actual_sub,
                    specific_angle=specific_ang,
                    content_format=content_fmt,
                    likely_audience=likely_aud,
                    appeal_type=app_type,
                    appeal_ratio=app_ratio,
                    confidence=conf,
                    evidence_source=evi_src,
                    relevance_status=rel_status,
                    relevance_reason=rel_reason,
                    connection_note=ev.connection_note,
                    key_takeaways=ev.key_takeaways,
                    branch_suggestions=ev.branch_suggestions,
                )
            else:
                assessment = self._heuristic_assessment(cand, preferences, rel)

            feed_items.append(FeedItem(video=cand, assessment=assessment, source_query=cand.source_query))

        return feed_items

    def _heuristic_assessment(
        self,
        cand: CandidateVideo,
        preferences: UserPreferences,
        rel: Optional[RelevanceAssessment] = None
    ) -> QualityAssessment:
        """Calculates thoughtful heuristic scores for candidate, separating entertainment-first from engaging education."""
        duration_sec = cand.duration_seconds or 600
        # Longer videos often have higher substance than 2-minute clips
        duration_bonus = min(duration_sec / 1800.0, 0.3)
        metadata_text = f"{cand.title} {cand.channel} {cand.description or ''}".lower()

        # Explicit educational explainer / lecture markers
        edu_matches = [m.group(0) for m in EDUCATIONAL_EXPLAINER_REGEX.finditer(metadata_text)]

        # Archival/lecture/documentary markers
        academic_keywords = [
            "lecture", "documentary", "history", "symposium",
            "breakdown", "analysis", "first principles", "pioneer", "origins",
            "physics", "theory", "mechanics"
        ]
        found_academic = sum(1 for k in academic_keywords if k in metadata_text)

        # Entertainment format detection
        matched_formats: List[str] = []
        for fmt_name, pats in ENTERTAINMENT_FORMAT_PATTERNS.items():
            if any(re.search(pat, metadata_text, re.IGNORECASE) for pat in pats):
                matched_formats.append(fmt_name)

        query_type_fmt_map = {
            "entertainment_comedy": "comedy",
            "entertainment_creative": "creative_projects",
            "entertainment_story": "compelling_story",
            "entertainment_gameplay": "gameplay",
            "entertainment_sports": "sports_moments",
            "entertainment_performance": "performance",
            "entertainment_challenge": "challenges",
        }
        if cand.source_query_type in query_type_fmt_map:
            q_fmt = query_type_fmt_map[cand.source_query_type]
            if q_fmt not in matched_formats:
                matched_formats.append(q_fmt)

        found_entertainment = len(matched_formats)

        recency_boost = upload_recency_boost(cand, preferences)

        rel_status = rel.status if rel else "supported"
        rel_reason = rel.reason if rel else ""

        if rel_status == "uncertain":
            # Sparse or ambiguous metadata: do NOT fabricate confident substance or novelty
            is_entertainment_first = False
            entertainment_format = None
            entertainment_evidence = "Insufficient or ambiguous metadata to confirm entertainment-first appeal."
            content_nature = "blend"
            substance_score = round(min(0.40 + duration_bonus * 0.2, 0.48), 2)
            entertainment_score = 0.40
            novelty_score = 0.50
            clickbait_score = 0.15
            # Deprioritize uncertain candidates so supported candidates are favored
            composite = round((substance_score * 0.40) + (novelty_score * 0.35) - (clickbait_score * 0.50) - 0.15, 3)
            connection_note = (
                f"Surfaced from channel {cand.channel}. Available metadata is minimal or ambiguous; "
                f"connection to '{preferences.interests}' remains unverified from preview details."
            )
            key_takeaways = [
                f"Metadata preview is limited for '{cand.title}'.",
                "Viewer discretion and manual verification recommended."
            ]
        elif edu_matches:
            # Educational explainers, lectures, math breakdowns, or economic essays:
            # Polished visuals or high craft do not qualify them as entertainment-first!
            is_entertainment_first = False
            entertainment_format = None
            sample_edu = edu_matches[0] if isinstance(edu_matches[0], str) else str(edu_matches[0])
            entertainment_evidence = f"Metadata exhibits educational explainer / lecture markers ('{sample_edu}'); not entertainment-first."
            content_nature = "educational"
            substance_score = round(min(0.65 + duration_bonus + (found_academic * 0.05), 0.95), 2)
            entertainment_score = round(min(0.25 + (0.05 if found_entertainment else 0.0), 0.35), 2)
            novelty_score = round(0.65 + min(found_academic * 0.04, 0.25), 2)
            clickbait_score = 0.15
        elif matched_formats:
            # Genuine entertainment-first video matching identified format
            is_entertainment_first = True
            entertainment_format = matched_formats[0]
            entertainment_evidence = f"Metadata supports entertainment-first appeal in '{entertainment_format}' format."
            content_nature = "entertaining"
            entertainment_score = round(min(0.72 + (len(matched_formats) * 0.06) + (0.05 if cand.view_count and cand.view_count > 25000 else 0.0), 0.95), 2)
            substance_score = round(min(0.40 + duration_bonus + (min(found_academic, 1) * 0.04), 0.78), 2)
            novelty_score = round(0.65 + min(len(matched_formats) * 0.05, 0.25), 2)
            clickbait_score = 0.15
        else:
            # Neutral / blend video without specific educational or entertainment markers
            is_entertainment_first = False
            entertainment_format = None
            entertainment_evidence = "Available metadata lacks specific entertainment format markers; tone remains unverified."
            content_nature = "blend"
            substance_score = round(min(0.50 + duration_bonus + (found_academic * 0.06), 0.88), 2)
            entertainment_score = 0.45
            novelty_score = round(0.65 + min(found_academic * 0.04, 0.25), 2)
            clickbait_score = 0.15

        if rel_status != "uncertain":
            # Modest viewing signal boost for creators/topics user actually spent time watching
            viewing_boost = 0.0
            if preferences.watched_channels and cand.channel:
                channel_sec = preferences.watched_channels.get(cand.channel, 0.0)
                # Require >= 60 seconds of real watch time to avoid treating quick exits as interest
                if channel_sec >= 60.0:
                    viewing_boost += min(0.05, 0.02 + (channel_sec / 600.0) * 0.03)

            if preferences.watched_topics:
                cand_sub = f"{cand.title} {cand.source_domain or ''}".lower()
                if any(t.lower() in cand_sub for t in preferences.watched_topics if len(t) > 3):
                    viewing_boost += 0.03

            viewing_boost = min(viewing_boost, 0.07)

            # Modest taste signal for followed creators
            followed_creator_boost = 0.0
            is_followed = False
            if preferences.followed_creators:
                for fc in preferences.followed_creators:
                    if fc.matches(cand.channel, cand.channel_id):
                        is_followed = True
                        break

            if is_followed:
                followed_creator_boost = 0.06

            # Taste alignment boost from living taste profile
            living_profile_boost = 0.0
            if preferences.living_taste_profile:
                lp = preferences.living_taste_profile
                top_q = [q for q, _ in lp.get_top_qualities(limit=3)]
                meta_lower = f"{cand.title} {cand.description or ''}".lower()
                for q in top_q:
                    if q == "visual_explanation" and any(k in meta_lower for k in ("visual", "animation", "geometric", "proof", "demonstration", "manim")):
                        living_profile_boost += 0.03
                        break
                    elif q == "technical_depth" and (substance_score >= 0.65 or duration_sec > 900):
                        living_profile_boost += 0.03
                        break
                    elif q == "storytelling" and any(k in meta_lower for k in ("story", "lore", "narrative", "history", "chronicle")):
                        living_profile_boost += 0.03
                        break
                    elif q == "humor" and entertainment_score >= 0.65:
                        living_profile_boost += 0.03
                        break
                    elif q == "artisan_craft" and any(k in meta_lower for k in ("craft", "making", "smelt", "luthier", "hand")):
                        living_profile_boost += 0.03
                        break

                est_subs = [s.lower() for s, _ in lp.get_established_subjects()]
                if any(es in meta_lower or (cand.source_domain and es in cand.source_domain.lower()) for es in est_subs):
                    living_profile_boost += 0.04

            living_profile_boost = min(living_profile_boost, 0.07)

            # Format preference boost from living taste profile
            format_boost = 0.0
            if preferences.living_taste_profile and entertainment_format:
                pref_fmts = preferences.living_taste_profile.preferred_entertainment_formats
                if entertainment_format in pref_fmts:
                    format_boost = min(0.06, pref_fmts[entertainment_format] * 0.02)

            # Novelty calibration for genuine discovery tiers
            if getattr(cand, "discovery_type", None) == "wildcard":
                novelty_score = max(novelty_score, 0.95)
            elif getattr(cand, "discovery_type", None) == "distant":
                novelty_score = max(novelty_score, 0.90)
            elif getattr(cand, "discovery_type", None) == "adjacent":
                novelty_score = max(novelty_score, 0.85)

            # Calculate composite score based on feed mode:
            if preferences.feed_mode == "entertainment":
                # Pure entertainment: penalize non-entertainment-first items heavily
                if not is_entertainment_first:
                    raw_composite = (entertainment_score * 0.35) + (novelty_score * 0.20) - 0.40
                else:
                    raw_composite = (entertainment_score * 0.55) + (novelty_score * 0.30) + (substance_score * 0.15) - (clickbait_score * 0.40) + recency_boost + viewing_boost + followed_creator_boost + living_profile_boost + format_boost
            elif preferences.feed_mode == "deep_learning":
                raw_composite = (substance_score * 0.50) + (novelty_score * 0.40) - (clickbait_score * 0.50) + recency_boost + viewing_boost + followed_creator_boost + living_profile_boost
            else:
                # Mix (default): allows either high educational value or high entertainment value to excel
                primary_signal = max(substance_score, entertainment_score)
                secondary_signal = min(substance_score, entertainment_score)
                raw_composite = (primary_signal * 0.48) + (novelty_score * 0.35) + (secondary_signal * 0.12) - (clickbait_score * 0.40) + recency_boost + viewing_boost + followed_creator_boost + living_profile_boost

            # Modest score adjustment from dislike feedback without banning entire topics or channels
            dislike_adjustment = 0.0
            if preferences.disliked_records:
                reasons = [r.chosen_reason for r in preferences.disliked_records if r.chosen_reason]
                if "Too academic" in reasons and (content_nature == "educational" or not is_entertainment_first):
                    dislike_adjustment += 0.05
                if "Too shallow" in reasons and (duration_sec < 480 or content_nature == "entertaining"):
                    dislike_adjustment += 0.05
                if "Don’t like this format" in reasons or "Don't like this format" in reasons:
                    disliked_titles = [r.title.lower() for r in preferences.disliked_records]
                    if any(any(w in cand.title.lower() for w in dt.split()[:3] if len(w) > 4) for dt in disliked_titles):
                        dislike_adjustment += 0.04
            raw_composite = max(0.0, raw_composite - min(dislike_adjustment, 0.08))

            composite = round(min(max(raw_composite, 0.0), 1.0), 3)

            # Synthesize metadata-grounded connection note based on relevance reason and content nature
            clean_title = cand.title.split("|")[0].split("—")[0].strip()
            if is_entertainment_first and entertainment_format:
                nature_phrase = f"an engaging entertainment showcase ({entertainment_format})"
            elif content_nature == "entertaining":
                nature_phrase = "an engaging, entertaining, or comedic perspective"
            elif content_nature == "educational":
                nature_phrase = "educational depth and substance"
            else:
                nature_phrase = "a blend of depth and creative engagement"

            if is_followed:
                connection_note = f"From followed creator '{cand.channel}', matching your taste profile while evaluated for substance. Based on metadata, offers {nature_phrase}."
            elif rel_reason:
                connection_note = f"Surfaced from channel {cand.channel}. Based on metadata: {rel_reason} Offers {nature_phrase}."
            else:
                connection_note = f"Surfaced from channel {cand.channel}. Based on title and metadata, offers {nature_phrase} on {clean_title}."

            key_takeaways = [
                f"Metadata indicates presentation by {cand.channel}.",
                f"{'Long-form duration (' + cand.duration_formatted + ') supports sustained depth.' if (cand.duration_seconds or 0) > 600 else 'Concise format provides focused engagement.'}"
            ]

        inferred = _infer_heuristic_structured_fields(
            cand,
            is_entertainment_first=is_entertainment_first,
            entertainment_format=entertainment_format,
            content_nature=content_nature,
            substance_score=substance_score,
            entertainment_score=entertainment_score,
            has_captions=getattr(cand, "has_captions", False)
        )

        return QualityAssessment(
            clickbait_score=clickbait_score,
            substance_score=substance_score,
            entertainment_score=entertainment_score,
            novelty_score=novelty_score,
            composite_score=round(composite, 3),
            content_nature=content_nature,
            is_sensationalized=False,
            is_entertainment_first=is_entertainment_first,
            entertainment_format=entertainment_format,
            entertainment_evidence=entertainment_evidence,
            actual_subject=inferred["actual_subject"],
            specific_angle=inferred["specific_angle"],
            content_format=inferred["content_format"],
            likely_audience=inferred["likely_audience"],
            appeal_type=inferred["appeal_type"],
            appeal_ratio=inferred["appeal_ratio"],
            confidence=inferred["confidence"],
            evidence_source=inferred["evidence_source"],
            relevance_status=rel_status,
            relevance_reason=rel_reason,
            connection_note=connection_note,
            key_takeaways=key_takeaways,
            branch_suggestions=[
                f"Origins of {cand.title.split()[0]} technology",
                "Related creative showcases",
                "Cross-disciplinary exploration"
            ]
        )

    def _evaluate_with_heuristics(
        self,
        candidates: List[CandidateVideo],
        preferences: UserPreferences,
        rel_map: Optional[Dict[str, RelevanceAssessment]] = None
    ) -> List[FeedItem]:
        feed_items = []
        for cand in candidates:
            rel = rel_map.get(cand.id) if rel_map else None
            assessment = self._heuristic_assessment(cand, preferences, rel)
            feed_items.append(FeedItem(video=cand, assessment=assessment, source_query=cand.source_query))
        return feed_items
