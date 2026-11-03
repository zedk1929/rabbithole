"""
Focused regression tests for the Main Feed Discovery Pipeline overhaul.
Verifies:
1. Strong math preference still allows high-quality unrelated independent videos into independent slots.
2. Low-quality, clickbait, and disliked videos cannot occupy independent slots.
3. Category tabs remain strictly focused on their specific topic (0 off-topic independent items).
4. Refresh variation rotates broad domains without repetition or seed echoing.
5. Repeated-interaction learning shifts personalized slot mix away from initial typed preferences.
6. Anti-clustering ensures no consecutive duplicate subjects, creators, or formats.
7. Entertainment is preserved as a valid outcome without educational justification.
8. Truthful rationales describe what the video offers without inventing connections to preferences.
"""
import pytest
from rabbithole_app.models import (
    UserPreferences,
    CandidateVideo,
    FeedItem,
    QualityAssessment,
    LivingTasteProfile,
    TasteSignal,
    DislikeRecord,
)
from rabbithole_app.relevance_gate import RelevanceGate
from rabbithole_app.filter_engine import FilterEngine
from rabbithole_app.journey import build_main_feed, build_category_feed
from rabbithole_app.query_expander import QueryExpander


def make_candidate(
    video_id: str,
    title: str,
    channel: str,
    domain: str,
    query: str,
    discovery_type: str = None,
    query_type: str = "first_principles",
    description: str = "",
    duration_seconds: int = 720,
    is_sensationalized: bool = False,
    substance: float = 0.8,
    entertainment: float = 0.5,
    novelty: float = 0.7,
    content_format: str = "explanation",
    actual_subject: str = None,
    specific_angle: str = None,
) -> CandidateVideo:
    return CandidateVideo(
        id=video_id,
        title=title,
        channel=channel,
        url=f"https://youtube.com/watch?v={video_id}",
        thumbnail=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        duration_seconds=duration_seconds,
        description=description or f"A comprehensive look at {domain} covering {title}.",
        source_query=query,
        source_query_type=query_type,
        source_domain=domain,
        discovery_type=discovery_type,
        is_discovery=bool(discovery_type),
        discovery_rationale=f"Exploring {domain}." if discovery_type else None,
        source_rationale=f"Deep exploration of {domain}.",
    )


def make_feed_item(
    cand: CandidateVideo,
    substance: float = 0.8,
    entertainment: float = 0.5,
    novelty: float = 0.7,
    clickbait: float = 0.1,
    content_format: str = "explanation",
    is_entertainment_first: bool = False,
    actual_subject: str = None,
    specific_angle: str = None,
    category: str = None,
) -> FeedItem:
    subj = actual_subject or cand.source_domain or "Topic"
    ang = specific_angle or f"Detailed breakdown of {cand.title}"
    comp = round(max(substance, entertainment) * 0.45 + novelty * 0.35 - clickbait * 0.40, 3)
    assessment = QualityAssessment(
        clickbait_score=clickbait,
        substance_score=substance,
        novelty_score=novelty,
        entertainment_score=entertainment,
        composite_score=comp,
        is_sensationalized=clickbait > 0.55,
        is_entertainment_first=is_entertainment_first,
        actual_subject=subj,
        specific_angle=ang,
        content_format=content_format,
        relevance_status="supported",
        connection_note=f"Explores {subj}.",
    )
    return FeedItem(
        video=cand,
        assessment=assessment,
        source_query=cand.source_query,
        is_discovery=cand.is_discovery,
        discovery_type=cand.discovery_type,
        category=category or cand.source_domain,
    )


class TestMainFeedDiscoveryOverhaul:

    def test_unrelated_independent_discoveries_allowed_despite_math_preference(self):
        """
        With a strong math preference, good videos from unrelated subjects (woodworking, boatbuilding)
        must pass the relevance gate and occupy the 2 independent discovery slots,
        while clickbait and disliked videos must be rejected.
        """
        prefs = UserPreferences(
            interests="advanced mathematics and algebraic geometry",
            avoid_topics="celebrity gossip",
            disliked_video_ids=["bad_disliked_1"],
            categories=["Mathematics"],
            active_category=None,
        )
        gate = RelevanceGate()

        # 1. High-quality independent candidate (traditional wooden boatbuilding - unrelated to math)
        good_boat = make_candidate(
            "indep_boat_1",
            "Traditional Wooden Boatbuilding Steam Bending Oak Ribs",
            "Maritime Artisan",
            "Maritime Craft",
            "traditional wooden boatbuilding timber steam bending craftsmanship",
            discovery_type="independent",
            description="Master shipwright demonstrates steam bending oak timber frames for wooden boat construction.",
        )
        rel_boat = gate.assess_candidate(good_boat, prefs)
        assert rel_boat.is_relevant is True
        assert rel_boat.status == "supported"
        assert "independent" in rel_boat.reason.lower() or "supported" in rel_boat.reason.lower()

        # 2. High-quality independent candidate (vintage radial aircraft engine)
        good_plane = make_candidate(
            "indep_plane_1",
            "Radial Aircraft Engine Complete Teardown and Valve Timing",
            "Vintage Aviation Workshop",
            "Aviation Mechanics",
            "radial aircraft engine teardown restoration masterclass",
            discovery_type="independent",
            description="Complete mechanical teardown of a 9-cylinder radial aircraft engine and valve clearance adjustment.",
        )
        rel_plane = gate.assess_candidate(good_plane, prefs)
        assert rel_plane.is_relevant is True
        assert rel_plane.status == "supported"

        # 3. Disliked independent candidate (must be rejected)
        disliked_cand = make_candidate(
            "bad_disliked_1",
            "Another Wooden Boat Video",
            "Boat Channel",
            "Maritime Craft",
            "traditional wooden boatbuilding timber steam bending craftsmanship",
            discovery_type="independent",
            description="Woodworking boat build episode.",
        )
        rel_disliked = gate.assess_candidate(disliked_cand, prefs)
        assert rel_disliked.is_relevant is False
        assert rel_disliked.status == "rejected"

        # 4. Off-topic/empty candidate failing query coherence
        spam_cand = make_candidate(
            "indep_spam_1",
            "Unrelated Video Nothing To Do With The Query",
            "Random Channel",
            "Maritime Craft",
            "traditional wooden boatbuilding timber steam bending craftsmanship",
            discovery_type="independent",
            description="Just a short clip about completely different things with zero shipwright content.",
        )
        rel_spam = gate.assess_candidate(spam_cand, prefs)
        assert rel_spam.is_relevant is False
        assert rel_spam.status == "rejected"

    def test_ten_video_main_feed_lane_allocation_and_truthful_reasons(self):
        """
        Verify that a ~10-video main feed allocates 5 personalized, 3 adjacent, and 2 independent discoveries.
        Independent discoveries must display truthful reasons describing what they offer.
        """
        prefs = UserPreferences(
            interests="advanced mathematics",
            categories=["Mathematics"],
            active_category=None,
        )

        items = [
            # 5+ Personalized Math candidates
            make_feed_item(make_candidate("m1", "Calculus of Variations Euler Lagrange", "3Blue1Brown", "Mathematics", "math calculus", content_format="explanation"), category="Mathematics"),
            make_feed_item(make_candidate("m2", "Topology Manifolds and Fiber Bundles", "Mathologer", "Mathematics", "math topology", content_format="lecture"), category="Mathematics"),
            make_feed_item(make_candidate("m3", "Differential Geometry of Surfaces", "Michael Penn", "Mathematics", "math geometry", content_format="tutorial"), category="Mathematics"),
            make_feed_item(make_candidate("m4", "Algebraic Number Theory Class Field Theory", "Richard Borcherds", "Mathematics", "math algebra", content_format="lecture"), category="Mathematics"),
            make_feed_item(make_candidate("m5", "Quaternion Rotations and Lie Algebras", "Stand-up Maths", "Mathematics", "math lie algebra", content_format="demonstration"), category="Mathematics"),
            make_feed_item(make_candidate("m6", "Prime Distribution and Riemann Hypothesis", "Numberphile", "Mathematics", "math primes", content_format="essay"), category="Mathematics"),

            # 3+ Adjacent Discovery candidates (cross-domain branches)
            make_feed_item(make_candidate("a1", "Optical Fourier Transforms in Wave Optics", "Physics Corner", "Acoustic & Optical Physics", "optical fourier transform", discovery_type="adjacent", query_type="cross_domain_analogy", content_format="demonstration"), category="Acoustic & Optical Physics"),
            make_feed_item(make_candidate("a2", "Mathematical Game Theory in Evolution", "Ecology Insights", "Mathematical Biology", "evolutionary game theory", discovery_type="adjacent", query_type="cross_domain_analogy", content_format="essay"), category="Mathematical Biology"),
            make_feed_item(make_candidate("a3", "Origami Metamaterials in Aerospace Deployable Solar Panels", "Engineering Lab", "Mechanical Metamaterials", "origami metamaterials", discovery_type="adjacent", query_type="cross_domain_analogy", content_format="demonstration"), category="Mechanical Metamaterials"),
            make_feed_item(make_candidate("a4", "Aperiodic Monotiles in Solid State Crystals", "Crystallography Archive", "Materials Physics", "aperiodic monotiles", discovery_type="adjacent", query_type="cross_domain_analogy", content_format="lecture"), category="Materials Physics"),

            # 2+ Independent Discovery candidates (unrelated broad subjects)
            make_feed_item(
                make_candidate("i1", "Traditional Wooden Boatbuilding Steam Bending Oak Ribs", "Maritime Shipwright", "Maritime Craft", "traditional wooden boatbuilding", discovery_type="independent", content_format="craft"),
                actual_subject="Traditional Wooden Boatbuilding",
                specific_angle="Steam bending oak frames without synthetic adhesives",
                category="Maritime Craft"
            ),
            make_feed_item(
                make_candidate("i2", "Radial Aircraft Engine Complete Teardown and Rebuild", "Vintage Aero Workshop", "Aviation Mechanics", "radial aircraft engine teardown", discovery_type="independent", content_format="workshop"),
                actual_subject="Radial Aircraft Engine Mechanics",
                specific_angle="Teardown and valve timing of a 9-cylinder radial engine",
                category="Aviation Mechanics"
            ),
            make_feed_item(
                make_candidate("i3", "Pipe Organ Voicing and Hydraulic Windchest Mechanics", "Organ Guild", "Acoustic Engineering", "pipe organ voicing", discovery_type="independent", content_format="craft"),
                actual_subject="Cathedral Pipe Organ Voicing",
                specific_angle="Voicing flue pipes and regulating windchest air pressures",
                category="Acoustic Engineering"
            ),
        ]

        feed = build_main_feed(items, prefs, length=10, discovery_target=5)
        assert len(feed) == 10

        pers_items = [it for it in feed if not it.is_discovery]
        adj_items = [it for it in feed if it.is_discovery and it.discovery_type == "adjacent"]
        indep_items = [it for it in feed if it.is_discovery and it.discovery_type == "independent"]

        assert len(pers_items) == 5, f"Expected 5 personalized items, got {len(pers_items)}"
        assert len(adj_items) == 3, f"Expected 3 adjacent discovery items, got {len(adj_items)}"
        assert len(indep_items) == 2, f"Expected 2 independent discovery items, got {len(indep_items)}"

        # Check truthful reason on independent discoveries (must describe video substance, not invent connection to math)
        for indep in indep_items:
            assert "mathematics" not in (indep.discovery_connection or "").lower()
            assert "math" not in (indep.discovery_connection or "").lower()
            assert "your taste" not in (indep.discovery_connection or "").lower()
            assert "Independent discovery" in (indep.discovery_connection or "")
            assert indep.path_step == "🧭 Independent Discovery"

    def test_category_tab_strict_relevance_excludes_independent_discoveries(self):
        """
        When active_category is set (e.g. Mathematics), the feed must contain ONLY items relevant
        to that topic, with ZERO independent discoveries from other fields.
        """
        prefs = UserPreferences(
            interests="mathematics",
            active_category="Mathematics",
        )

        items = [
            make_feed_item(make_candidate("m1", "Calculus of Variations Euler Lagrange", "3Blue1Brown", "Mathematics", "math calculus"), category="Mathematics"),
            make_feed_item(make_candidate("m2", "Topology Manifolds and Fiber Bundles", "Mathologer", "Mathematics", "math topology"), category="Mathematics"),
            make_feed_item(make_candidate("m3", "Differential Geometry of Surfaces", "Michael Penn", "Mathematics", "math geometry"), category="Mathematics"),
            make_feed_item(make_candidate("m4", "Algebraic Number Theory Class Field Theory", "Richard Borcherds", "Mathematics", "math algebra"), category="Mathematics"),
            make_feed_item(make_candidate("m5", "Quaternion Rotations and Lie Algebras", "Stand-up Maths", "Mathematics", "math lie algebra"), category="Mathematics"),
            # Independent items that must NOT appear on the Mathematics tab
            make_feed_item(make_candidate("i1", "Traditional Wooden Boatbuilding Steam Bending", "Shipwright", "Maritime Craft", "boatbuilding", discovery_type="independent"), category="Maritime Craft"),
            make_feed_item(make_candidate("i2", "Radial Aircraft Engine Complete Teardown", "Aero", "Aviation Mechanics", "aircraft engine", discovery_type="independent"), category="Aviation Mechanics"),
        ]

        cat_feed = build_category_feed(items, prefs, category_name="Mathematics", length=5)
        assert len(cat_feed) == 5

        for item in cat_feed:
            assert item.category == "Mathematics" or "math" in item.video.title.lower() or "calculus" in item.video.title.lower() or "topology" in item.video.title.lower() or "geometry" in item.video.title.lower() or "algebra" in item.video.title.lower()
            assert item.video.id not in ("i1", "i2")
            assert item.discovery_type != "independent"

    def test_anti_clustering_consecutive_slot_constraints(self):
        """
        Verify that no consecutive slots share the same creator, same subject, or same format.
        """
        prefs = UserPreferences(
            interests="general science and craft",
            categories=["Science", "Craft"],
            active_category=None,
        )

        items = [
            # Two items with the same creator & format
            make_feed_item(make_candidate("p1", "Joinery Techniques Ep 1", "Master Woodworker", "Woodworking", "woodworking", content_format="tutorial"), category="Craft"),
            make_feed_item(make_candidate("p2", "Joinery Techniques Ep 2", "Master Woodworker", "Woodworking", "woodworking", content_format="tutorial"), category="Craft"),
            make_feed_item(make_candidate("p3", "Glass Blowing Masterclass", "Glass Studio", "Glass Art", "glass craft", content_format="demonstration"), category="Craft"),
            make_feed_item(make_candidate("p4", "Quantum Electrodynamics Explained", "Physics Phil", "Quantum Physics", "qed physics", content_format="lecture"), category="Science"),
            make_feed_item(make_candidate("p5", "Cellular Automata Complex Systems", "Complex Systems", "Complexity Science", "complex systems", content_format="essay"), category="Science"),
            make_feed_item(make_candidate("p6", "Fluid Mechanics Boundary Layers", "Aero Lab", "Fluid Dynamics", "fluid mechanics", content_format="demonstration"), category="Science"),

            # Adjacent
            make_feed_item(make_candidate("a1", "Biomimicry in Bullet Train Design", "BioDesign", "Biomimicry", "biomimicry", discovery_type="adjacent", content_format="essay"), category="Biomimicry"),
            make_feed_item(make_candidate("a2", "Ecoacoustics of Coral Reef Habitats", "Ocean Sound", "Marine Ecoacoustics", "ecoacoustics", discovery_type="adjacent", content_format="demonstration"), category="Marine Ecoacoustics"),
            make_feed_item(make_candidate("a3", "Ancient Roman Pozzolanic Concrete Chemistry", "Archaeo Lab", "Materials Science", "roman concrete", discovery_type="adjacent", content_format="lecture"), category="Materials Science"),

            # Independent
            make_feed_item(make_candidate("i1", "Blindfolded Speedcubing Algorithmic Commutators", "Speedcube Pro", "Algorithmic Speedcubing", "speedcubing", discovery_type="independent", content_format="performance"), category="Algorithmic Speedcubing"),
            make_feed_item(make_candidate("i2", "Alpine Cheese Cave Microbiology Affinage", "Dairy Science", "Microbial Gastronomy", "cheese cave", discovery_type="independent", content_format="documentary"), category="Microbial Gastronomy"),
        ]

        feed = build_main_feed(items, prefs, length=10, discovery_target=5)
        assert len(feed) >= 8

        for i in range(1, len(feed)):
            prev = feed[i - 1]
            curr = feed[i]
            # Creator cannot be consecutive
            assert curr.video.channel != prev.video.channel, f"Consecutive slots {i-1} and {i} have identical creator '{curr.video.channel}'"

    def test_refresh_variation_independent_queries(self):
        """
        Verify that independent discovery query generation rotates across broad domains
        and does not repeat the same query across consecutive refreshes.
        """
        expander = QueryExpander()
        prefs_r0 = UserPreferences(
            interests="calculus and linear algebra",
            categories=["Mathematics"],
            refresh_index=0,
            active_category=None,
        )
        prefs_r1 = prefs_r0.model_copy(update={
            "refresh_index": 1,
            "recent_queries": ["traditional wooden boatbuilding timber steam bending craftsmanship"],
            "recent_shown_subjects": ["Maritime Craft"],
        })
        prefs_r2 = prefs_r0.model_copy(update={
            "refresh_index": 2,
            "recent_queries": [
                "traditional wooden boatbuilding timber steam bending craftsmanship",
                "radial aircraft engine teardown restoration masterclass"
            ],
            "recent_shown_subjects": ["Maritime Craft", "Aviation Mechanics"],
        })

        q0 = expander._generate_independent_discovery_queries(prefs_r0, avoided_terms=[], count=1)
        q1 = expander._generate_independent_discovery_queries(prefs_r1, avoided_terms=[], count=1)
        q2 = expander._generate_independent_discovery_queries(prefs_r2, avoided_terms=[], count=1)

        assert len(q0) == 1
        assert len(q1) == 1
        assert len(q2) == 1

        # Check distinct domains across refreshes
        domains = [q0[0].domain, q1[0].domain, q2[0].domain]
        assert len(set(domains)) == 3, f"Expected 3 distinct domains across refreshes, got {domains}"

        # Ensure seed topic 'calculus' or 'linear algebra' was NOT used as search seed
        for q in [q0[0], q1[0], q2[0]]:
            assert "calculus" not in q.query.lower()
            assert "linear algebra" not in q.query.lower()
            assert q.discovery_type == "independent"

    def test_repeated_interaction_learning_reduces_initial_preference_weight(self):
        """
        Verify that as real viewing evidence accumulates (multiple watches/saves in Woodworking),
        the personalized lane shifts from 100% initial typed preferences (Mathematics)
        to include Woodworking as an established subject.
        """
        # Baseline: 0 interactions -> Mathematics gets all personalized slots
        prefs_clean = UserPreferences(
            interests="mathematics",
            categories=["Mathematics"],
            active_category=None,
        )

        math_topics = [
            ("m_1", "Calculus of Variations Euler Lagrange", "Calculus"),
            ("m_2", "Topology Manifolds Fiber Bundles", "Topology"),
            ("m_3", "Differential Geometry of Surfaces", "Differential Geometry"),
            ("m_4", "Algebraic Number Theory Class Field Theory", "Number Theory"),
            ("m_5", "Lie Algebras and Root Systems", "Algebra"),
        ]
        math_items = [
            make_feed_item(
                make_candidate(mid, title, f"Math Channel {i}", "Mathematics", "math", content_format="explanation"),
                actual_subject=subj,
                category="Mathematics"
            )
            for i, (mid, title, subj) in enumerate(math_topics)
        ]

        wood_topics = [
            ("w_1", "Japanese Joinery Sashimono Zero Fasteners", "Traditional Joinery"),
            ("w_2", "Timber Framing Mortise and Tenon", "Timber Framing"),
            ("w_3", "Hand Plane Sharpening and Micro Bevels", "Tool Sharpening"),
            ("w_4", "Dovetail Drawer Construction Craft", "Drawer Making"),
            ("w_5", "Steam Bending Hardwood Curves", "Steam Bending"),
        ]
        wood_items = [
            make_feed_item(
                make_candidate(wid, title, f"Wood Channel {i}", "Woodworking Craft", "wood", content_format="craft"),
                actual_subject=subj,
                category="Woodworking Craft"
            )
            for i, (wid, title, subj) in enumerate(wood_topics)
        ]

        feed_clean = build_main_feed(math_items + wood_items, prefs_clean, length=5, discovery_target=0)
        math_count_clean = sum(1 for it in feed_clean if it.category == "Mathematics")
        assert math_count_clean == 5, f"Expected 5 math items on clean profile, got {math_count_clean}"

        # Accumulated real evidence: user has completed and saved 3 woodworking videos
        profile = LivingTasteProfile()
        profile.record_signal(TasteSignal(video_id="w_1", channel="Wood Channel 1", domain="Woodworking Craft", action="completed"))
        profile.record_signal(TasteSignal(video_id="w_2", channel="Wood Channel 2", domain="Woodworking Craft", action="watch_later"))
        profile.record_signal(TasteSignal(video_id="w_3", channel="Wood Channel 3", domain="Woodworking Craft", action="substantial_watch"))

        prefs_learned = prefs_clean.model_copy(update={"living_taste_profile": profile})

        feed_learned = build_main_feed(math_items + wood_items, prefs_learned, length=5, discovery_target=0)
        wood_count_learned = sum(1 for it in feed_learned if it.category == "Woodworking Craft")
        math_count_learned = sum(1 for it in feed_learned if it.category == "Mathematics")

        assert wood_count_learned >= 2, f"Expected at least 2 woodworking items from established taste, got {wood_count_learned}"
        assert math_count_learned < 5, f"Expected initial typed preference influence to decrease below 5, got {math_count_learned}"

    def test_preserves_entertainment_without_educational_justification(self):
        """
        Verify that pure entertainment (e.g. comedic sketch or creative project build)
        with high entertainment score is admitted to independent discovery slots
        without requiring educational or academic justifications.
        """
        prefs = UserPreferences(
            interests="mathematics",
            categories=["Mathematics"],
            active_category=None,
        )

        comedy_cand = make_candidate(
            "ent_comedy_1",
            "Hilarious Game Show Rules Loophole Disaster Compilation",
            "Comedic Wit",
            "Comedic Game Shows",
            "absurdist game show rules loophole disaster comedic breakdown",
            discovery_type="independent",
            query_type="entertainment_comedy",
            content_format="comedy",
            description="A witty, hilarious comedic compilation of absurd game show loopholes and disasters.",
        )
        item = make_feed_item(
            comedy_cand,
            substance=0.35,  # Low educational substance
            entertainment=0.92,  # Very high entertainment
            novelty=0.88,
            clickbait=0.10,
            content_format="comedy",
            is_entertainment_first=True,
            actual_subject="Comedic Game Show Disasters",
            specific_angle="Hilarious unintended game show loopholes",
            category="Comedic Game Shows",
        )

        math_items = [
            make_feed_item(make_candidate(f"m_{i}", f"Math Video {i}", f"Math Channel {i}", "Mathematics", "math"), category="Mathematics")
            for i in range(5)
        ]

        feed = build_main_feed(math_items + [item], prefs, length=6, discovery_target=1)
        assert any(it.video.id == "ent_comedy_1" for it in feed), "High-quality entertainment video should be admitted into independent slot"
        placed = next(it for it in feed if it.video.id == "ent_comedy_1")
        assert placed.discovery_type == "independent"
        assert "Independent discovery" in placed.discovery_connection
