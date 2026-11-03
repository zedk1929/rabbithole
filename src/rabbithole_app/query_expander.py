"""
Lateral Query Expansion Engine.
Generates search queries designed to avoid mainstream algorithmic echo chambers
and surface high-signal, non-sensationalized, intellectually substantive content.
"""
import os
import re
import json
import logging
import hashlib
import certifi
from typing import List, Optional, Tuple, Dict, Any, Set
from dotenv import load_dotenv

from rabbithole_app.models import UserPreferences, CuriosityQuery, CuriosityQueryPlan, CURIOSITY_ARCHETYPES

# Ensure SSL and network proxy resilience
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

# Load environment variables (.env and ~/.env)
load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()

logger = logging.getLogger(__name__)


EXPANSION_SYSTEM_PROMPT = """
You are the Curiosity Architect for 'Rabbithole', an anti-clickbait discovery engine.
Your purpose is to independently seek and propose interesting, unexpected directions for
the user based on their broad interests, curiosity archetypes, and serendipity dial, rather than
expecting the user to specify what fields to search.

When given the user's broad interests and serendipity dial (1 to 5):
- 1 = Specialized, rigorous deep-dive into the foundational mechanics of the subject.
- 3 = Balanced lateral expansion into fascinating adjacent subfields and unexpected bridges.
- 5 = Radical, imaginative cross-domain conceptual leaps with a clear underlying connection.

CRITICAL SEARCH STRATEGY RULES:
1. Actively propose specific, varied directions on your own. Do not wait for or expect user-supplied exploration fields.
2. Formulate exactly the requested count of queries, covering distinct viewing angles:
   - 'first_principles': Foundational mechanisms, physical laws, or underlying mathematical models.
   - 'archive_lecture': Archival pioneer demonstrations, university lectures, or symposia.
   - 'cross_domain_analogy': Explainable, lateral conceptual bridge connecting the user's interest to an unexpected discipline outside their usual content.
   - 'niche_history': A forgotten historical dispute, technological dead end, or origins that challenge modern framing.
   - 'practitioner_deep_dive': Concrete artisan craftsmanship, workshop case study, or hands-on field practice.
3. NEVER generate shallow, high-competition SEO search terms (e.g. 'top 10 synths', 'learn quantum physics', 'best documentary').
4. ALWAYS generate concise queries (3 to 6 words) using specialized vocabulary that unearths high-signal creator archives.
5. STRICTLY respect avoided topics: never include any topic, keyword, or concept the user asked to avoid.
6. Provide an explainable rationale for each query stating why it branches out meaningfully while remaining grounded.
7. PERSONALITY & ARCHETYPE-FIRST DISCOVERY: When the viewer's curiosity archetypes, intellectual vibes, or curiosity statement are provided, do NOT merely repeat the user's initial seed topic nouns in every query. Instead, interpret the viewer's underlying curiosity personality (e.g. love of physical tolerances, archival disputes, geometric intuition, tactile master craft, hidden incentives) and boldly formulate search queries across UNEXPECTED fields of human endeavor that scratch this exact cognitive drive.
"""


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


def _extract_tokens(text: str) -> Set[str]:
    """Extract clean alphanumeric tokens of length >= 3, excluding stopwords."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in STOPWORDS}


def _clean_json_markdown(text: str) -> str:
    """Strip markdown code fence if model wrapped output in ```json."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


# Comprehensive domain definitions for deterministic query expansion
DOMAIN_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "sports_strategy": {
        "match_keywords": [
            "baseball", "strategy", "sabermetrics", "tactics", "pitching",
            "batting", "coaching"
        ],
        "first_principles": [
            (
                "baseball sabermetrics statistical decision analysis",
                "Quantitative Analytics & Sabermetrics",
                "Examines the mathematical models, run-expectancy matrices, and statistical decision principles governing modern baseball strategy."
            ),
            (
                "pitch sequencing game theory mechanics breakdown",
                "Tactical Game Theory",
                "Analyzes sequential decision trees, count leverage, and pitcher-batter anticipation from foundational principles."
            ),
            (
                "baseball pitching biomechanics kinetics analysis",
                "Biomechanics & Kinetics",
                "Deconstructs the kinetic chain and physical torque mechanics of pitching from first principles."
            ),
        ],
        "archive_lecture": [
            (
                "baseball tactical theory pioneer lecture archive",
                "Archival Tactical Theory",
                "Unearths archival lectures, manager strategy discussions, and early analytical treatises on baseball tactics."
            ),
            (
                "sabermetrics revolution Bill James archival lecture",
                "Pioneer Analytics Archive",
                "Surfaces early masterclasses and foundational talks on the statistical revolution in sports strategy."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "catcher pitch framing valuation economic analysis",
                "Economic Valuation & Microeconomics",
                "Connects catcher pitch framing and hidden run prevention directly to microeconomic marginal value theory."
            ),
            "balanced": (
                "game theory pitch sequencing minimax strategy",
                "Game Theory & Decision Science",
                "Connects pitch calling and tactical deception in baseball directly to game-theoretic minimax equilibria and asymmetric information."
            ),
            "radical": (
                "fastball reaction time psychophysics visual cognition",
                "Cognitive Neuroscience & Visual Psychophysics",
                "Bridges hitting strategy against 95mph fastballs to human visual processing latency and predictive motor control."
            )
        },
        "niche_history": [
            (
                "baseball dead ball era tactical evolution history",
                "Historical Tactical Evolution",
                "Explores how forgotten dead-ball era small-ball tactics, equipment changes, and rule revisions transformed strategic framing."
            ),
            (
                "defensive shift baseball history controversy retrospective",
                "Tactical Controversies",
                "Examines the historical evolution and controversies surrounding defensive positioning shifts from Ted Williams to modern bans."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "baseball advance scouting report tactical methodology",
                "Advance Scouting Methodology",
                "Investigates how major league advance scouting departments synthesize spray charts, release points, and dugout tactical cards."
            ),
            (
                "pitch design high speed camera player development case study",
                "Applied Pitch Design Craft",
                "Examines hands-on lab pitch design using edgertronic high-speed cameras and seam-shifted wake aerodynamics."
            ),
        ],
        "entertainment_comedy": [
            (
                "baseball weirdest plays funny strategic blunders comedy",
                "Comedic Strategic Blunders",
                "Hilarious and bizarre strategic mishaps, umpire confusion, and quirky baseball plays."
            ),
            (
                "funny baseball mic'd up dugout banter player antics",
                "Dugout Wit & Player Banter",
                "Entertaining mic'd up banter, player antics, and comedic dugout culture."
            ),
        ],
        "entertainment_creative": [
            (
                "creative pitch movement trick pitch visual breakdown",
                "Creative Pitch Artistry",
                "Entertaining and visually stunning showcase of impossible pitch movement, wiffle ball curves, and creative physics."
            ),
            (
                "most unbelievable walk off moments dramatic animation storytelling",
                "Dramatic Climax Storytelling",
                "Compelling animated and narrative storytelling of legendary baseball walk-off drama."
            ),
        ],
        "entertainment_story": [
            (
                "bizarre baseball history Jon Bois weird stories",
                "Weird Baseball Lore",
                "Entertaining, unconventional narrative documentaries exploring bizarre historical moments in baseball lore."
            ),
            (
                "the art of the hidden ball trick baseball deception stories",
                "The Art of Deception",
                "Delightful storytelling detailing the craft, history, and hilarity of the hidden ball trick."
            ),
        ]
    },

    "basketball": {
        "match_keywords": [
            "basketball", "nba", "wnba", "hoops", "ncaa basketball"
        ],
        "first_principles": [
            (
                "basketball pick and roll spacing tactical decision breakdown",
                "Tactical Schemes & Floor Spacing",
                "Deconstructs modern offensive floor spacing, pick-and-roll coverage, and defensive rotations from first principles."
            ),
            (
                "basketball shooting mechanics kinetics shot doctor analysis",
                "Biomechanical Shooting Kinetics",
                "Analyzes shooting pocket mechanics, kinetic sequencing, and release dynamics in elite shooters."
            ),
            (
                "spatial basketball analytics shot chart efficiency tracking",
                "Spatial Tracking & Analytics",
                "Examines expected possession value, shot quality modeling, and defensive impact metrics."
            ),
        ],
        "archive_lecture": [
            (
                "history of the triangle offense Phil Jackson tactical archive",
                "Tactical System Archives",
                "Archival footage and breakdown of Tex Winter and Phil Jackson's read-and-react triangle offense."
            ),
            (
                "nba coaching clinic defensive schemes masterclass",
                "Coaching Clinic Archives",
                "Foundational coaching clinics breaking down shell drill principles and modern drop vs switch coverages."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "basketball player tracking computer vision spatial geometry",
                "Computer Vision & Spatial Geometry",
                "Connects NBA optical tracking to computational geometry and spatial density algorithms."
            ),
            "balanced": (
                "basketball shot selection stochastic optimization decision theory",
                "Operations Research & Decision Theory",
                "Connects shot distribution to stochastic resource allocation and expected value optimization."
            ),
            "radical": (
                "basketball transition offense fluid dynamics swarm intelligence",
                "Swarm Intelligence & Fluid Dynamics",
                "Bridges fast-break transition passing and lane-filling to flocking algorithms and particle swarm dynamics."
            )
        },
        "niche_history": [
            (
                "aba basketball three point line and red white blue ball history",
                "Basketball Heritage & ABA Evolution",
                "The untold history of the American Basketball Association and how small innovations revolutionized the modern NBA."
            ),
            (
                "evolution of the zone defense ban and illegal defense rules NBA",
                "Defensive Rule Evolution",
                "Explores how historical illegal defense guidelines shaped isolation basketball before the 2001 rule changes."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "nba player development film study and footwork breakdown",
                "Player Development & Film Study",
                "In-depth film study analyzing footwork on closeouts, post moves, and off-ball relocation."
            ),
            (
                "nba advance scouting game plan preparation methodology",
                "Advance Scouting & Game Planning",
                "Examines how NBA analytics and video coordinators construct opponent scouting reports and tendency charts."
            ),
        ],
        "entertainment_gameplay": [
            (
                "greatest clutch fourth quarter moments NBA playoff history",
                "Clutch Playoff Highlights",
                "Thrilling clutch fourth-quarter performances, walk-off buzzer beaters, and game-winners."
            ),
            (
                "insane NBA handles crossovers and ankle breakers mixtape",
                "Playmaking & Ball Handling",
                "Highlight showcase of dazzling crossover dribbles and court vision."
            ),
        ],
        "entertainment_performance": [
            (
                "slam dunk contest most creative dunks all time",
                "Aerial Dunk Craftsmanship",
                "Creative athleticism, vertical leap technique, and contest-winning dunks."
            ),
        ],
        "entertainment_comedy": [
            (
                "funniest NBA bloopers hilarious bench reactions and outtakes",
                "Comedic Moments & Bloopers",
                "Lighthearted comedy, hilarious bench celebrations, and unforgettable bloopers."
            ),
        ],
        "entertainment_story": [
            (
                "untold stories of the 1990s NBA legendary rivalries",
                "Lore & Hardwood Tales",
                "Dramatic storytelling and oral history behind legendary team rivalries."
            ),
        ],
        "entertainment_creative": [
            (
                "court design and aesthetic evolution of NBA uniforms",
                "Basketball Aesthetics & Design",
                "Visual history and creative craft behind iconic court aesthetics and jersey designs."
            ),
        ]
    },

    "cinema_storytelling": {
        "match_keywords": [
            "movies", "movie", "film", "cinema", "storytelling", "narrative",
            "screenwriting", "cinematography", "directing", "editing", "montage", "screenplay"
        ],
        "first_principles": [
            (
                "visual grammar cinematography narrative theory",
                "Cinematic Grammar & Visual Theory",
                "Deconstructs how mise-en-scène, framing geometry, and shot composition construct narrative meaning from first principles."
            ),
            (
                "dramatic narrative structure three act mechanics",
                "Narrative Structure & Dramaturgy",
                "Examines narrative progression, inciting incidents, and structural tension curves from foundational principles."
            ),
            (
                "film editing rhythm pacing montage principles",
                "Film Editing Grammar",
                "Analyzes the temporal pacing, spatial continuity, and rhythmic cadence of film cuts."
            ),
        ],
        "archive_lecture": [
            (
                "Alfred Hitchcock visual storytelling masterclass archive",
                "Pioneer Filmmaking Archive",
                "Surfaces foundational archival masterclasses and pioneer lectures on visual suspense and cinematic storytelling."
            ),
            (
                "Akira Kurosawa film directing editing retrospective",
                "Directorial Masterclass Retrospective",
                "Unearths archival masterclasses on dynamic weather staging, character choreography, and rhythmic cutting."
            ),
            (
                "Soviet montage theory Eisenstein archival lecture",
                "Historical Montage Theory",
                "Examines foundational lectures on intellectual montage and associative visual juxtaposition."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "screenplay dramatic pacing literary narrative mechanics",
                "Comparative Literary Dramaturgy",
                "Connects cinematic scene construction to dramatic literature and classical narrative mechanics."
            ),
            "balanced": (
                "cognitive film theory eye tracking edit cuts",
                "Cognitive Science & Visual Perception",
                "Bridges film editing rhythm and continuity cuts to cognitive eye-tracking research and saccadic suppression psychology."
            ),
            "radical": (
                "architectural space production design narrative psychology",
                "Spatial Architecture & Environmental Psychology",
                "Bridges cinematic world-building to architectural theory and psychological spatial orientation."
            )
        },
        "niche_history": [
            (
                "silent film sound transition visual grammar history",
                "Cinematic Evolutionary History",
                "Examines the historical disruption and creative controversies when silent visual pantomime transitioned to synchronized sound."
            ),
            (
                "French New Wave continuity editing rule breaking controversy",
                "Avant-Garde Film History",
                "Explores how French New Wave directors shattered classical Hollywood continuity editing with intentional jump cuts."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "film scene editing timeline pacing case study",
                "Artisan Editing Timeline Craft",
                "Analyzes concrete timeline decisions, cut points, and audio-visual transitions in complex dramatic sequences."
            ),
            (
                "storyboard to screen shot composition case study",
                "Visual Staging & Storyboard Execution",
                "Follows the hands-on practitioner progression from initial thumbnail sketches to finalized cinematography."
            ),
        ],
        "entertainment_comedy": [
            (
                "hilarious movie bloopers set stories funny director commentary",
                "Set Bloopers & Filmmaking Humor",
                "Hilarious behind-the-scenes set mishaps, funny director commentary, and comedic set culture."
            ),
            (
                "funny movie tropes parody sketch comedy",
                "Cinematic Trope Parodies",
                "Creative sketches and comedic satire deconstructing familiar movie storytelling clichés."
            ),
        ],
        "entertainment_creative": [
            (
                "creative animated short film award winning storytelling",
                "Creative Animation & Short Form Craft",
                "Visually captivating independent short films and innovative animated storytelling."
            ),
            (
                "practical movie effects miniatures behind the scenes craftsmanship",
                "Practical Effects & Movie Magic",
                "Delightful showcase of miniature craftsmanship, stop-motion, and tangible practical effects."
            ),
        ],
        "entertainment_story": [
            (
                "strangest chaotic movie productions wild Hollywood stories",
                "Chaotic Hollywood Lore",
                "Gripping and entertaining narrative tales from the most chaotic and bizarre film productions in history."
            ),
            (
                "genius scene improvisations that made the final movie",
                "Legendary Screen Improvisations",
                "Entertaining stories of spontaneous, unscripted moments that became cinematic history."
            ),
        ]
    },

    "gardening_cooking": {
        "match_keywords": [
            "gardening", "garden", "cooking", "cook", "culinary", "food",
            "soil", "fermentation", "heirloom", "agriculture", "horticulture", "yeast"
        ],
        "first_principles": [
            (
                "soil food web microbiology nutrient uptake principles",
                "Soil Biology & Plant Physiology",
                "Examines how mycorrhizal fungi and rhizosphere bacterial communities convert minerals into plant nutrient density."
            ),
            (
                "Maillard reaction culinary chemistry food science breakdown",
                "Culinary Chemistry & Thermodynamics",
                "Deconstructs non-enzymatic browning, volatile aromatic synthesis, and thermal food transformation from first principles."
            ),
            (
                "plant volatile organic compounds flavor development physiology",
                "Flavor Biochemistry",
                "Analyzes the biochemical pathways producing aromatic terpenes and esters in garden crops."
            ),
        ],
        "archive_lecture": [
            (
                "Harold McGee culinary science kitchen chemistry lecture",
                "Pioneer Food Science Archive",
                "Surfaces foundational university lectures on culinary physics, protein coagulation, and emulsion science."
            ),
            (
                "Ruth Stout no till mulch gardening archival demonstration",
                "Archival Horticulture Masterclass",
                "Unearths early archival demonstrations and lectures on regenerative permanent mulch soil building."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "culinary emulsion food science colloid physics",
                "Colloid Physics & Food Chemistry",
                "Connects cooking sauces and textures directly to colloid chemistry and surface tension thermodynamics."
            ),
            "balanced": (
                "microbial ecology vegetable lacto fermentation biochemistry",
                "Microbial Ecology & Preservation",
                "Connects home harvest preservation directly to microbial succession ecology and lactic acid bacterial pathways."
            ),
            "radical": (
                "ethnobotany heirloom crop genetics domestication history",
                "Ethnobotany & Evolutionary Genetics",
                "Bridges garden crops and kitchen flavors to ancestral plant genetics and cultural domestication trajectories."
            )
        },
        "niche_history": [
            (
                "heirloom seed biodiversity agricultural extinction history",
                "Agricultural Botanical History",
                "Explores the historical disappearance of diverse heirloom vegetable cultivars and the grassroots seed-saver revival."
            ),
            (
                "columbian exchange crop migration culinary history",
                "Global Food Migration History",
                "Examines how transatlantic botanical exchanges fundamentally transformed traditional cuisines worldwide."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "thermophilic hot composting soil biology case study",
                "Thermal Composting Craft",
                "Investigates hands-on temperature management, carbon-nitrogen ratios, and microbial succession in living compost."
            ),
            (
                "artisan solid state fermentation koji culinary technique",
                "Applied Artisan Fermentation",
                "Examines the concrete workshop craft of inoculating grains with Aspergillus oryzae for enzymatic seasoning production."
            ),
        ],
        "entertainment_comedy": [
            (
                "chaotic kitchen cooking experiments funny chef comedy",
                "Kitchen Chaos & Culinary Humor",
                "Lighthearted, funny cooking experiments and comedic kitchen trials."
            ),
            (
                "funny gardening fails and relatable plant comedy",
                "Relatable Gardening Comedy",
                "Humorous and relatable garden mishaps, stubborn pests, and comedic plant struggles."
            ),
        ],
        "entertainment_creative": [
            (
                "satisfying artisan pastry decoration sugar craft showcase",
                "Artisan Pastry & Sugar Craft",
                "Deeply satisfying and creative pastry artistry, sugar sculpting, and culinary craft."
            ),
            (
                "creative guerilla gardening transform ugly spaces botanical art",
                "Creative Botanical Transformations",
                "Inspiring and creative botanical transformations of unexpected urban spaces."
            ),
        ],
        "entertainment_story": [
            (
                "bizarre ancient foods and weird historical feast stories",
                "Bizarre Culinary Lore",
                "Entertaining narrative storytelling about bizarre ancient banquets, forgotten foods, and royal culinary obsessions."
            ),
            (
                "the obsessive search for the world's hottest pepper story",
                "The Quest for the Hottest Pepper",
                "Captivating and entertaining story of obsessive breeders and wild botanical rivalries."
            ),
        ]
    },

    "acoustics_music": {
        "match_keywords": [
            "acoustic", "acoustics", "sound", "music", "timbre", "instrument",
            "synthesizer", "synth", "harmony", "resonance", "resonance chambers"
        ],
        "first_principles": [
            (
                "acoustic standing waves resonance chamber physics",
                "Acoustic Wave Physics",
                "Deconstructs standing wave formation, resonant nodes, and harmonic overtone series in physical instruments."
            ),
            (
                "physical acoustics timbre harmonic series breakdown",
                "Physical Acoustics & Timbre",
                "Analyzes how boundary conditions and material elasticity determine harmonic spectrum distribution."
            ),
        ],
        "archive_lecture": [
            (
                "pipe organ flue aerodynamics acoustic standing waves lecture",
                "Aerodynamic Acoustics Archive",
                "Unearths archival engineering lectures on fluid-structure oscillation and flue pipe resonance."
            ),
            (
                "Stradivarius wood acoustic resonance pioneer lecture archive",
                "Material Acoustics Archive",
                "Surfaces early university lectures investigating cellular wood structure and acoustic varnish properties."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "chladni plate modal vibration structural resonance",
                "Structural Dynamics",
                "Bridges acoustic frequencies to physical modal vibration and visible nodal geometries."
            ),
            "balanced": (
                "Chladni plate cymatic fluid resonance patterns",
                "Fluid & Structural Resonance",
                "Bridges acoustic vibration directly to physical wave mechanics and fluid nodal patterns."
            ),
            "radical": (
                "pythagorean tuning celestial monochord ratio history",
                "Historical Mathematical Cosmology",
                "Explores ancient mathematical models connecting acoustic ratios to astronomical orbital periods."
            )
        },
        "niche_history": [
            (
                "temperament tuning controversies historical evolution",
                "Historical Musicology",
                "Explores historical disputes between mean-tone, Pythagorean, and equal temperament tuning systems."
            ),
            (
                "analog tape magnetic hysteresis saturation physics history",
                "Electromagnetic Audio History",
                "Examines the physics and recording controversies of early magnetic tape formulation."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "violin luthier acoustic tap tone voicing case study",
                "Luthier Artisan Craft",
                "Investigates the hands-on practitioner craft of plate graduation, tap tone resonance, and acoustic tuning."
            ),
            (
                "pipe organ flue voicing workshop craftsmanship demonstration",
                "Acoustic Voicing Workshop Craft",
                "Examines the tactile techniques organ builders use to shape mouth geometry and harmonic cut-up."
            ),
        ],
        "entertainment_comedy": [
            (
                "musician reacts funny music theory memes comedy",
                "Music Theory Wit & Comedy",
                "Hilarious comedic breakdowns, musical memes, and playful instrument satire."
            ),
            (
                "funny musical instrument fails and quirky historical inventions",
                "Quirky Instrument Misadventures",
                "Entertaining looks at bizarre failed instruments and comedic acoustic accidents."
            ),
        ],
        "entertainment_creative": [
            (
                "satisfying modular synthesizer ambient creative soundscape",
                "Creative Modular Soundscapes",
                "Mesmerizing creative modular patching, visual generative art, and hypnotic synthesizer craft."
            ),
            (
                "building instruments from unusual junk creative luthier",
                "Inventive Junk Luthier Craft",
                "Delightful artisan craft creating unexpectedly beautiful instruments from bizarre scrap materials."
            ),
        ],
        "entertainment_story": [
            (
                "unbelievable stories behind legendary recording studio accidents",
                "Studio Lore & Accidental Magic",
                "Fascinating narrative tales of legendary recording accidents that shaped iconic sounds."
            ),
            (
                "the strange mystery of ghost pipe organs and abandoned acoustics",
                "Acoustic Lore & Hidden Spaces",
                "Atmospheric storytelling uncovering forgotten instruments in bizarre acoustic environments."
            ),
        ]
    },

    "biology_computing": {
        "match_keywords": [
            "slime mold", "physarum", "cellular computation", "biological computing",
            "mycelium", "hyphae", "biocomputing", "biological network", "non-neural"
        ],
        "first_principles": [
            (
                "Physarum polycephalum protoplasmic streaming mechanics",
                "Cellular Biophysics",
                "Deconstructs peristaltic contractility, shuttling protoplasm, and spatial network optimization from first principles."
            ),
            (
                "cellular computation decentralized biological routing algorithms",
                "Decentralized Bio-Algorithms",
                "Analyzes how non-neural organisms solve the shortest path problem using positive feedback tubular dynamics."
            ),
        ],
        "archive_lecture": [
            (
                "cellular computation pioneer demonstration lecture",
                "Biocomputing Archive",
                "Unearths foundational demonstrations and university lectures on biological non-silicon computing models."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "mycelial nutrient transport network topology graph theory",
                "Network Graph Optimization",
                "Connects fungal nutrient distribution to mathematical graph optimization and resilient network topology."
            ),
            "balanced": (
                "non neural cellular computation biocomputing memristor",
                "Unconventional Computing",
                "Bridges cellular bio-memristive properties to distributed analog computer architectures."
            ),
            "radical": (
                "slime mold spatial optimization transportation networks",
                "Bio-Inspired Infrastructure Planning",
                "Connects plasmodial foraging networks to municipal transit routing and topological resilience."
            )
        },
        "niche_history": [
            (
                "history of non neural organism intelligence controversy",
                "Biological Philosophy & History",
                "Examines the historical scientific controversy surrounding cognitive behavior in single-celled organisms."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "slime mold maze solving laboratory protocol case study",
                "Experimental Biophysics Protocol",
                "Investigates concrete laboratory culture protocols, agar nutrient gradients, and bio-network measurement."
            ),
        ]
    },

    "metallurgy_pyrotechnology": {
        "match_keywords": [
            "metallurgy", "bronze", "casting", "iron smelting", "bloomery",
            "archaeometallurgy", "lost wax", "crucible", "damascus"
        ],
        "first_principles": [
            (
                "bloomery iron smelting slag reduction thermodynamics",
                "Pyrotechnology & Reduction Chemistry",
                "Deconstructs solid-state carbon reduction, slag viscosity, and iron bloom thermodynamics from first principles."
            ),
            (
                "bronze alloy lost wax investment casting metallurgy",
                "Alloy Solidification Metallurgy",
                "Analyzes copper-tin eutectic crystallization, shrinkage porosity, and ceramic shell heat dissipation."
            ),
        ],
        "archive_lecture": [
            (
                "ancient metallurgy pioneer masterclass archival lecture",
                "Archaeometallurgical Lecture Archive",
                "Unearths archival university lectures and pioneer demonstrations on ancient thermal technology."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "lost wax investment bronze casting metallurgical history",
                "Historical Materials Science",
                "Connects ancient ceramic crucible recipes to modern refractory materials science."
            ),
            "balanced": (
                "crucible steel wootz damascus carbide banding metallurgy",
                "Phase Transformations & Microstructure",
                "Bridges ancient crucible steel patterns to ultra-high-carbon phase diagrams and carbide band segregation."
            ),
            "radical": (
                "ancient smelting slag geochemical mineralogy crystallization",
                "Geochemical Thermodynamics",
                "Connects metallurgical furnace slag formation to igneous petrology and mineral crystallization."
            )
        },
        "niche_history": [
            (
                "history bloomery iron transition blast furnace controversy",
                "Technological History of Metallurgy",
                "Examines the historical technological disruption from intermittent bloomeries to continuous blast furnaces."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "experimental bloomery iron smelt charcoal ratio case study",
                "Experimental Archaeometallurgy",
                "Examines field reconstruction parameters, air blast rates, and temperature gradients in traditional furnaces."
            ),
        ]
    },

    "mathematics": {
        "match_keywords": [
            "math", "mathematics", "calculus", "linear algebra", "topology", "geometry",
            "number theory", "algebra", "probabilities", "analysis", "fourier", "3blue1brown"
        ],
        "first_principles": [
            (
                "visual linear algebra matrix geometric transformations",
                "Visual Mathematics & Linear Algebra",
                "Deconstructs matrix operations, linear mappings, and eigenspaces through geometric intuition and visual transformations."
            ),
            (
                "calculus fundamentals rate of change limits geometric intuition",
                "Foundational Calculus & Analysis",
                "Examines derivatives, integrals, and infinitesimal rates of change from foundational geometric mechanics."
            ),
            (
                "fourier transform visual decomposition intuitive mathematics",
                "Harmonic Analysis & Fourier Series",
                "Deconstructs complex cyclical wave decomposition into intuitive rotating vector circles and frequencies."
            ),
        ],
        "archive_lecture": [
            (
                "richard feynman character of physical law mathematics lecture archive",
                "Classic Physical Mathematics Lectures",
                "Classic archival lecture illustrating why mathematical abstraction provides the natural language for physical law."
            ),
            (
                "mit opencourseware topology differential equations masterclass",
                "Higher Mathematics & Topology Archive",
                "Archival university masterclass exploring multidimensional vector fields, manifolds, and topological invariants."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "topology and geometric phase in condensed matter physics",
                "Topological Physics & Geometry",
                "Bridges abstract algebraic topology and non-trivial manifold geometry directly to quantum Berry phase."
            ),
            "balanced": (
                "fourier analysis harmonic acoustics musical intervals physics",
                "Acoustic Mathematics & Wave Mechanics",
                "Bridges mathematical trigonometric decomposition to acoustic resonance, overtone series, and musical timbre."
            ),
            "radical": (
                "information theory entropy statistical mechanics shannon boltzmann",
                "Information Theory & Statistical Physics",
                "Connects Claude Shannon's mathematical bits directly to Ludwig Boltzmann's thermodynamic microstate entropy."
            )
        },
        "niche_history": [
            (
                "banach tarski paradox axiom of choice historical controversy",
                "Foundations of Mathematics & Set Theory",
                "Examines the startling early-20th-century historical dispute surrounding non-measurable sets and mathematical axioms."
            ),
            (
                "invention of non euclidean geometry gauss bolyai riemann",
                "History of Differential Geometry",
                "Explores the dramatic mathematical revolution that broke free from Euclid's parallel postulate to curved manifolds."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "manim mathematical animation engine programmatic craft breakdown",
                "Mathematical Visualization Craft",
                "Hands-on case study breaking down programmatic mathematical rendering, LaTeX alignment, and motion design."
            ),
            (
                "computational origami geometry physical theorem folding",
                "Computational Geometry & Folding",
                "Hands-on architectural demonstration proving mathematical theorems through rigorous physical origami folding."
            ),
        ],
        "entertainment_creative": [
            (
                "creative visual proofs without words elegant mathematical animations",
                "Aesthetic Proofs & Visual Mathematics",
                "Captivating visual demonstrations and creative geometric animations explaining complex mathematical theorems without words."
            ),
        ],
        "entertainment_comedy": [
            (
                "counterintuitive mathematical paradoxes bizarre logic puzzle breakdown",
                "Mathematical Paradoxes & Humor",
                "Witty, mind-bending explorations of counterintuitive mathematical brainteasers, probability paradoxes, and bizarre logic."
            ),
        ],
        "entertainment_story": [
            (
                "srinivasa ramanujan partition formula mathematical genius mystery story",
                "Mathematical Lore & Biography",
                "Dramatic historical narrative tracing Srinivasa Ramanujan's notebooks, miraculous partition formulas, and collaborative breakthroughs."
            ),
        ]
    },

    "tilings_geometry": {
        "match_keywords": [
            "geometry", "tiling", "tilings", "penrose", "girih", "quasicrystal",
            "quasicrystals", "aperiodic", "symmetry", "strapwork"
        ],
        "first_principles": [
            (
                "aperiodic Penrose tiling geometric inflation rules",
                "Aperiodic Discrete Geometry",
                "Deconstructs non-periodic tessellation, matching rules, and self-similar deflation from first principles."
            ),
            (
                "Girih strapwork decagonal geometry construction principles",
                "Decagonal Geometric Strapwork",
                "Analyzes the underlying decagram, pentagon, and bow-tie tile sets governing medieval geometric patterns."
            ),
        ],
        "archive_lecture": [
            (
                "crystallography pioneer lecture quasicrystal discovery archive",
                "Nobel Crystallography Archive",
                "Surfaces Dan Shechtman's pioneer lectures on discovering five-fold icosahedral symmetry."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "hyperbolic geometry poincare disk architectural projection",
                "Non-Euclidean Architectural Geometry",
                "Connects two-dimensional non-repeating tilings to hyperbolic geometry and curved space projections."
            ),
            "balanced": (
                "aperiodic Penrose tiling quasicrystal diffraction crystallography",
                "Aperiodic Crystallography & Solid State Physics",
                "Bridges medieval Islamic Girih geometry directly to modern icosahedral quasicrystal electron diffraction."
            ),
            "radical": (
                "fourier transform optical diffraction spatial frequency lens",
                "Fourier Optics & Wave Interference",
                "Connects mathematical tile symmetry to optical Fourier transforms and constructive wave interference."
            )
        },
        "niche_history": [
            (
                "Girih strapwork medieval Islamic geometry discovery history",
                "History of Mathematics & Architecture",
                "Explores the historical discovery that medieval artisans used sophisticated aperiodic tile sets centuries before Penrose."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "compass and straightedge Girih tile construction methodology",
                "Hands-On Traditional Geometry",
                "Investigates practical drafting techniques using compass and straightedge to generate intricate strapwork."
            ),
        ]
    },

    "fluid_dynamics": {
        "match_keywords": [
            "fluid dynamics", "fluid", "vortex", "turbulence", "turbulent",
            "laminar", "navier stokes", "aerodynamics", "hydrodynamics"
        ],
        "first_principles": [
            (
                "Navier Stokes boundary layer laminar turbulent transition",
                "Theoretical Fluid Mechanics",
                "Deconstructs shear stress, vorticity transport, and boundary layer separation from first principles."
            ),
            (
                "vortex shedding and Karman streets fluid mechanics",
                "Vortex Dynamics & Instability",
                "Analyzes visual non-linear fluid dynamics and oscillatory wake formation."
            ),
        ],
        "archive_lecture": [
            (
                "Taylor Couette flow hydrodynamic stability pioneer lecture",
                "Hydrodynamic Stability Archive",
                "Unearths foundational laboratory lectures and visual demonstrations on centrifugal flow instabilities."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "laminar to turbulent pipe flow Reynolds experiment",
                "Experimental Fluid Dynamics",
                "Examines the foundational fluid physics governing dye streak breakdown in pipe flow."
            ),
            "balanced": (
                "atmospheric gravity wave cloud kelvin helmholtz instability",
                "Geophysical Fluid Dynamics",
                "Connects laboratory fluid shear layers to large-scale atmospheric billow cloud formation."
            ),
            "radical": (
                "ocean thermohaline circulation turbulent mixing oceanography",
                "Oceanographic Physical Dynamics",
                "Bridges microscale vortex dissipation to global ocean heat transport networks."
            )
        },
        "niche_history": [
            (
                "turbulence theory historical controversy Kolmogorov Richardson",
                "History of Theoretical Physics",
                "Examines historical theoretical disputes over turbulent energy cascades and fractal eddy dissipation."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "wind tunnel smoke flow visualization aerodynamic case study",
                "Experimental Flow Visualization",
                "Investigates hands-on wind tunnel techniques for visualizing streamline separation around airfoils."
            ),
        ]
    },

    "cognition_navigation": {
        "match_keywords": [
            "spatial", "navigation", "wayfinding", "cognitive map", "memory architecture",
            "polynesian", "star compass", "loci", "orientation", "memory"
        ],
        "first_principles": [
            (
                "hippocampal place grid cells cognitive map architecture",
                "Cognitive Neurobiology",
                "Deconstructs internal coordinate systems, entorhinal grid firing, and spatial memory indexing from first principles."
            ),
            (
                "non instrument wayfinding dead reckoning vector geometry",
                "Navigational Kinematics",
                "Analyzes dead-reckoning vector integration without instruments from foundational geometric principles."
            ),
        ],
        "archive_lecture": [
            (
                "polynesian star compass ocean swell navigation documentary archive",
                "Ethno-Navigation Archive",
                "Unearths archival masterclasses and pioneer demonstrations on celestial star tracks and swell navigation."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "method of loci spatial memory palace architectural mnemonic",
                "Historical Mnemonics & Spatial Cognition",
                "Connects environmental spatial mapping directly to classical memory palace architectures."
            ),
            "balanced": (
                "polynesian star compass ocean swell navigation wayfinding",
                "Maritime Ocean Wave Dynamics",
                "Connects mental spatial models directly to physical ocean swell reflection and refraction physics."
            ),
            "radical": (
                "animal magnetoreception cryptochrome quantum biology navigation",
                "Quantum Sensory Biology",
                "Bridges intuitive organism orientation to radical-pair quantum spin states in retinal cryptochromes."
            )
        },
        "niche_history": [
            (
                "history of maritime celestial navigation instruments controversy",
                "Navigational Technology History",
                "Explores the historical shift from sensory environmental reading to mechanical octants and chronometers."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "traditional ocean swell reading navigator field methodology",
                "Master Navigator Field Craft",
                "Investigates practical tactile techniques used by Polynesian navigators to detect island swell shadows."
            ),
        ]
    },

    "infrastructure_grids": {
        "match_keywords": [
            "infrastructure", "municipal", "water distribution", "electric grid",
            "power grid", "scada", "wastewater", "aqueduct", "substation", "utility"
        ],
        "first_principles": [
            (
                "gravity flow aqueduct hydraulic grade line civil engineering",
                "Hydraulic Civil Engineering",
                "Deconstructs hydrostatic pressure, open channel friction, and grade lines in water distribution."
            ),
            (
                "substation transformer magnetic core hysteresis power grid",
                "Electromagnetic Power Engineering",
                "Analyzes three-phase power distribution, reactive power compensation, and core hysteresis."
            ),
        ],
        "archive_lecture": [
            (
                "SCADA industrial control systems network architecture lecture",
                "Industrial Automation Archive",
                "Unearths foundational university lectures on distributed telemetry protocols in critical utilities."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "power grid frequency regulation inertia synchronous condensers",
                "Grid Frequency Dynamics",
                "Examines rotational inertia and frequency stability in large interconnected electrical grids."
            ),
            "balanced": (
                "wastewater biological aeration activated sludge microbial digestion",
                "Environmental Biotechnology",
                "Connects civil sanitation directly to microbial community bio-oxidation and respirometry."
            ),
            "radical": (
                "Roman aqueduct hydraulic engineering siphon construction",
                "Ancient Civil Hydraulic Engineering",
                "Bridges modern municipal water distribution to inverted siphons and Roman ashlar masonry aqueducts."
            )
        },
        "niche_history": [
            (
                "evolution of electrical grid AC DC war currents history",
                "History of Power Engineering",
                "Examines the engineering controversies and transformation of centralized electrical networks."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "municipal water pumping station pressure surge water hammer case study",
                "Municipal Water Works Craft",
                "Investigates practical diagnostic methods for transient pressure surges and water hammer mitigation."
            ),
        ]
    },

    "ancient_scripts_epigraphy": {
        "match_keywords": [
            "ancient scripts", "decipherment", "paleography", "epigraphy", "linear b",
            "hieroglyphs", "writing systems", "cryptanalysis", "inscription"
        ],
        "first_principles": [
            (
                "Linear B grid cryptanalysis paleography methodology",
                "Cryptanalytic Epigraphy",
                "Deconstructs syllabic grid construction, phonetic value deduction, and statistical glyph distribution."
            ),
            (
                "structural linguistics phonotactics ancient script decipherment",
                "Structural Phonotactics",
                "Analyzes consonant-vowel syllable structures and morphological inflection patterns from first principles."
            ),
        ],
        "archive_lecture": [
            (
                "Michael Ventris Linear B decipherment archival radio documentary",
                "Historical Decipherment Archive",
                "Surfaces foundational archival audio and lectures detailing the 1952 breakthrough cracking of Linear B."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "cuneiform administrative tablet accounting paleography",
                "Economic Epigraphy",
                "Connects early written tablets directly to ancient agricultural inventory accounting systems."
            ),
            "balanced": (
                "computational epigraphy ancient language cryptanalysis",
                "Computational Linguistics & Information Theory",
                "Connects ancient script decipherment directly to modern cipher breaking and Shannon entropy analysis."
            ),
            "radical": (
                "stone inscription squeeze photogrammetry epigraphy case study",
                "Optical Surface Metrology",
                "Bridges physical epigraphy to 3D reflectance transformation imaging and micro-topography."
            )
        },
        "niche_history": [
            (
                "Indus Valley script undeciphered controversies history",
                "Epigraphic Historical Controversies",
                "Explores the long-standing academic disputes surrounding whether the Indus symbols represent spoken language."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "epigraphic latex squeeze archival preservation case study",
                "Hands-On Archival Epigraphy",
                "Investigates the tactile field techniques epigraphers use to capture eroded stone incisions."
            ),
        ]
    },

    "deep_sea_chemosynthesis": {
        "match_keywords": [
            "hydrothermal", "chemosynthesis", "abyssal", "deep sea", "black smoker",
            "extremophile", "trench", "serpentinization", "vents"
        ],
        "first_principles": [
            (
                "hydrothermal vent chemosynthesis hydrogen sulfide bioenergetics",
                "Chemosynthetic Bioenergetics",
                "Deconstructs sulfur-oxidizing bacterial endosymbiosis and ATP synthesis completely without sunlight."
            ),
            (
                "abyssal serpentinization hydrothermal vent mantle geology",
                "Mantle Geochemistry",
                "Analyzes olivine hydration, abiotic hydrogen generation, and alkaline hydrothermal chimney formation."
            ),
        ],
        "archive_lecture": [
            (
                "Alvin submersible hydrothermal vents discovery archival documentary",
                "Deep Submersible Discovery Archive",
                "Unearths foundational archival footage and scientific lectures from the 1977 Galapagos Rift discovery."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "deep sea gigantism physiological hydrostatic pressure adaptations",
                "Abyssal Comparative Physiology",
                "Examines cell membrane fluidity and piezophilic enzyme adaptations under extreme hydrostatic pressure."
            ),
            "balanced": (
                "alkaline hydrothermal vents origin of life proton gradient",
                "Prebiotic Geochemistry & Proton Gradients",
                "Bridges deep-sea hydrothermal chemistry directly to early prebiotic thermodynamics and proto-metabolism."
            ),
            "radical": (
                "chemosynthetic cold seep methane hydrate ecology oceanography",
                "Gas Hydrate Biogeochemistry",
                "Connects volcanic hydrothermal biology to seafloor methane clathrates and carbonate crust ecosystems."
            )
        },
        "niche_history": [
            (
                "discovery of hydrothermal vents paradigm shift marine biology",
                "History of Oceanographic Science",
                "Examines how finding sunlight-independent ecosystems upended classical biological paradigms in 1977."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "deep sea benthic lander oceanographic sampling case study",
                "Oceanographic Deep-Sea Craft",
                "Investigates autonomous benthic landers, titanium pressure vessels, and in-situ chemical sensor arrays."
            ),
        ]
    },

    "economics": {
        "match_keywords": [
            "economics", "economy", "macroeconomics", "microeconomics", "econometrics", "monetary policy", "fiscal policy"
        ],
        "first_principles": [
            (
                "macroeconomics monetary policy interest rates central banking mechanics",
                "Monetary Economics & Central Banking",
                "Deconstructs the transmission mechanism of interest rates, reserve ratios, and quantitative easing from first principles."
            ),
            (
                "game theory mechanism design and auction theory mathematical models",
                "Mechanism Design & Game Theory",
                "Explores how incentive-compatible mechanisms and Vickrey auctions align private incentives with social welfare."
            ),
            (
                "microeconomics supply demand equilibrium market failure externalities",
                "Market Structures & Price Theory",
                "Examines marginal cost pricing, deadweight loss, public goods, and market equilibrium."
            ),
        ],
        "archive_lecture": [
            (
                "nobel laureate economics lecture Milton Friedman Keynesian debate archive",
                "Archival Economic Debates",
                "Pioneer lectures and foundational debates on monetarism versus Keynesian fiscal intervention."
            ),
            (
                "history of economic thought Adam Smith to modern macroeconomics",
                "Economic History Archives",
                "Historical trajectory of economic doctrine from classical political economy to the neoclassical synthesis."
            ),
        ],
        "cross_domain_analogy": {
            "focused": (
                "econometrics natural experiments causal inference methodology",
                "Empirical Causal Inference",
                "Connects observational economic data to natural experiments, instrumental variables, and difference-in-differences."
            ),
            "balanced": (
                "behavioral economics heuristics cognitive biases decision theory",
                "Cognitive Psychology & Behavioral Economics",
                "Bridges Kahneman and Tversky's prospect theory to market anomalies and heuristics."
            ),
            "radical": (
                "evolutionary economics complex adaptive systems Santa Fe institute",
                "Complex Adaptive Systems & Economics",
                "Connects economic market interactions to self-organizing complexity and non-equilibrium statistical mechanics."
            )
        },
        "niche_history": [
            (
                "bretton woods conference and the forgotten bancor currency dispute",
                "International Monetary History",
                "Explores Keynes's proposal for an international clearing union and the diplomatic dispute that created the modern financial architecture."
            ),
            (
                "history of central banking Bank of England early panics retrospective",
                "Financial History & Early Crises",
                "Examines the origins of lender of last resort doctrine during Victorian liquidity crises."
            ),
        ],
        "practitioner_deep_dive": [
            (
                "econometrics causal inference natural experiments methodology",
                "Applied Econometric Methodology",
                "Examines difference-in-differences, regression discontinuity, and instrumental variables in empirical economic research."
            ),
            (
                "supply chain economics global trade containerization case study",
                "Global Trade Logistics & Trade Theory",
                "Investigates the microeconomics of container shipping, port congestion, and tariff incidence."
            ),
        ],
        "entertainment_story": [
            (
                "bizarre economic bubbles Dutch tulip mania to South Sea company lore",
                "Economic History & Frenzy Lore",
                "Entertaining, narrative exploration of bizarre speculative bubbles, financial oddities, and market mania."
            ),
        ],
        "entertainment_comedy": [
            (
                "satirical economics standup comedic explanations of inflation and markets",
                "Satirical Economic Commentary",
                "Witty and humorous breakdowns of everyday economic absurdity without sensationalist clickbait."
            ),
        ],
        "entertainment_creative": [
            (
                "visual data storytelling global economy flowcharts and interactive maps",
                "Visual Economic Storytelling",
                "Satisfying visual animation and dynamic data storytelling depicting international trade flows."
            ),
        ],
        "entertainment_gameplay": [
            (
                "video game virtual economies Eve Online market warfare breakdown",
                "Virtual Economies & Player Markets",
                "Fascinating look at player-driven market cartels, arbitrage, and virtual stock exchanges in MMO games."
            ),
        ],
        "entertainment_performance": [
            (
                "economic satire musical and stage performance revue",
                "Satirical Stage Performance",
                "Witty musical performance and theatrical satire dissecting finance and markets."
            ),
        ]
    }
}


# High-signal discovery queries matched to viewer qualities
QUALITY_TO_DISTANT_QUERIES: Dict[str, List[Tuple[str, str, str]]] = {
    "visual explanation": [
        (
            "aperiodic monotile geometry tilings visual proof",
            "Geometric Topology",
            "Distant discovery matching your affinity for visual explanation: exploring aperiodic tilings and geometric proofs."
        ),
        (
            "visual proof non euclidean geometry hyperbolic space",
            "Non-Euclidean Geometry",
            "Distant discovery matching your affinity for visual explanation: visual proofs in hyperbolic geometry."
        ),
        (
            "chladni figures acoustic standing wave patterns visual physics",
            "Acoustic Physics",
            "Distant discovery matching your affinity for visual explanation: visual nodal patterns formed by acoustic resonance."
        ),
    ],
    "storytelling": [
        (
            "Linear B decipherment Michael Ventris linguistic mystery",
            "Archaeolinguistics",
            "Distant discovery matching your affinity for narrative storytelling: the dramatic decipherment of Linear B."
        ),
        (
            "Antikythera mechanism mechanical decoding ancient astronomy",
            "History of Technology",
            "Distant discovery matching your affinity for narrative storytelling: unravelling the ancient astronomical computer."
        ),
        (
            "Voynich manuscript cryptanalysis historical decoding attempts",
            "Historical Cryptography",
            "Distant discovery matching your affinity for narrative storytelling: historical codebreaking attempts on the Voynich manuscript."
        ),
    ],
    "technical depth": [
        (
            "fluid dynamics laminar turbulent boundary layer transition",
            "Fluid Dynamics",
            "Distant discovery matching your preference for technical depth: boundary layer mechanics in fluid dynamics."
        ),
        (
            "metallurgy quenching martensite microscopic crystal structure",
            "Physical Metallurgy",
            "Distant discovery matching your preference for technical depth: microscopic phase transformations in crystalline metals."
        ),
        (
            "semiconductor photolithography extreme ultraviolet optics precision",
            "Semiconductor Physics",
            "Distant discovery matching your preference for technical depth: extreme ultraviolet photolithography and optical diffraction limits."
        ),
    ],
    "artisan craft": [
        (
            "traditional japanese joinery sashimono woodworking craftsmanship",
            "Woodworking Craft",
            "Distant discovery matching your preference for artisan craft: traditional Japanese joint design without nails or adhesives."
        ),
        (
            "mechanical watch movement tourbillon escapement hand assembly",
            "Horology Craft",
            "Distant discovery matching your preference for artisan craft: micro-mechanical hand fabrication of mechanical watch escapements."
        ),
        (
            "traditional bookbinding leather marbling handbinding demonstration",
            "Archival Bookbinding",
            "Distant discovery matching your preference for artisan craft: master hand-binding and archival leather marbling."
        ),
    ],
    "humor": [
        (
            "unintended historical consequences weird policy blunders documentary",
            "Historical Ironies",
            "Distant discovery matching your appreciation for humor and wit: bizarre historical unintended consequences."
        ),
        (
            "game show push your luck statistical flaw scandal breakdown",
            "Game Show Mathematics",
            "Distant discovery matching your appreciation for humor and wit: how a contestant memorized game board patterns."
        ),
    ],
    "unusual connections": [
        (
            "plant phyllotaxis fibonacci golden ratio developmental biology",
            "Mathematical Biology",
            "Distant discovery matching your interest in unusual connections: mathematical spiraling in botanical growth."
        ),
        (
            "origami mathematics mechanical metamaterials deployable structures",
            "Mathematical Origami",
            "Distant discovery matching your interest in unusual connections: origami fold patterns in space exploration structures."
        ),
        (
            "slime mold network optimization tokyo rail simulation",
            "Biological Computation",
            "Distant discovery matching your interest in unusual connections: biological amoeba simulating efficient transport networks."
        ),
    ],
}

# High-substance independent discovery catalog across varied broad domains and formats.
# Generated without using user seed interests, saved categories, followed creators, or learned affinities.
INDEPENDENT_DISCOVERY_CATALOG: List[Tuple[str, str, str, str, str]] = [
    # (query, domain, query_type, rationale, format_label)
    # 1. Maritime Craft & Ocean Engineering
    (
        "traditional wooden boatbuilding timber steam bending craftsmanship",
        "Maritime Craft",
        "first_principles",
        "Exploring traditional wooden boatbuilding and steam-bent oak framing without synthetic fasteners.",
        "craft"
    ),
    (
        "deep sea submersible pressure hull exploration titanium",
        "Deep Ocean Engineering",
        "first_principles",
        "Titanium spherical pressure hull mechanics for abyssal ocean exploration.",
        "engineering"
    ),
    # 2. Aviation & Aerospace Mechanics
    (
        "radial aircraft engine teardown restoration masterclass",
        "Aviation Mechanics",
        "practitioner_deep_dive",
        "Deep dive into vintage multi-row radial aircraft engine mechanics and valve timing.",
        "workshop"
    ),
    (
        "glider ridge lift soaring aerodynamics meteorological physics",
        "Aviation Aerodynamics",
        "first_principles",
        "Analyzing mountain wave meteorological dynamics and motorless soaring physics.",
        "demonstration"
    ),
    # 3. Ecology, Mycology & Animal Cognition
    (
        "mycelial underground networks forest nutrient exchange ecology",
        "Mycology & Forest Ecology",
        "first_principles",
        "Fungal mycorrhizal networks mediating biochemical exchange among trees in old-growth forests.",
        "science"
    ),
    (
        "corvid problem solving puzzle cognition behavioral experiment",
        "Animal Cognition",
        "first_principles",
        "Multi-step causal reasoning and tool use in wild crows and ravens.",
        "experiment"
    ),
    # 4. Metallurgy & Precision Horology
    (
        "crucible wootz damascus steel forging pattern metallurgy",
        "Historical Metallurgy",
        "practitioner_deep_dive",
        "Crucible steel crystal formation, carbide banding, and high-carbon blade forging.",
        "craft"
    ),
    (
        "tower clock pendulum escapement historical mechanical restoration",
        "Horology Restoration",
        "practitioner_deep_dive",
        "Restoring antique church tower clock gear trains and deadbeat escapements.",
        "restoration"
    ),
    # 5. Civil Engineering & Heavy Logistics
    (
        "electrical grid frequency synchronization rotational inertia black start",
        "Power Grid Infrastructure",
        "first_principles",
        "Rotational inertia and dynamic frequency stabilization in regional high-voltage power grids.",
        "infrastructure"
    ),
    (
        "tunnel boring machine earth pressure balance subterranean shield",
        "Civil Engineering",
        "practitioner_deep_dive",
        "Earth pressure balance shield mechanics and segmental lining in deep underwater tunneling.",
        "engineering"
    ),
    (
        "rotary car dumper train unloading coal infrastructure bulk logistics",
        "Industrial Logistics",
        "practitioner_deep_dive",
        "Heavy bulk train car rotators and rail freight logistics systems in modern ports.",
        "spectacle"
    ),
    # 6. Archaeology & Ancient Materials
    (
        "Linear B decipherment Michael Ventris linguistic mystery archival",
        "Archaeolinguistics",
        "niche_history",
        "The dramatic decipherment of Bronze Age Linear B syllabic script from clay tablets.",
        "narrative"
    ),
    (
        "Antikythera mechanism mechanical gear train bronze decoding",
        "Ancient Technology",
        "niche_history",
        "Differential gearing and astronomical cycles in the ancient Hellenistic computer.",
        "history"
    ),
    (
        "Roman pozzolanic marine concrete lime clasts self-healing chemistry",
        "Ancient Materials Science",
        "first_principles",
        "Hot mixing chemistry and volcanic ash self-healing in 2,000-year-old Roman harbor concrete.",
        "science"
    ),
    # 7. Acoustic Physics & Musical Craft
    (
        "pipe organ voicing acoustic resonance windchest mechanics",
        "Acoustic Engineering",
        "practitioner_deep_dive",
        "Voicing flue and reed pipes, windchest hydraulics, and acoustic resonance in cathedrals.",
        "craft"
    ),
    (
        "chladni figures acoustic standing wave patterns visual resonance",
        "Acoustic Physics",
        "first_principles",
        "Visualizing two-dimensional nodal lines and standing wave resonance in metal plates.",
        "demonstration"
    ),
    (
        "unbelievable street musician virtuosic live acoustic performance",
        "Live Performance",
        "entertainment_performance",
        "Virtuosic acoustic performance and improvised live musical craft.",
        "performance"
    ),
    # 8. Animation & Creative Automata
    (
        "kinetic sculpture mechanical automaton intricate wooden gear train",
        "Artisan Automata",
        "entertainment_creative",
        "Hand-crafted wooden mechanical automata, linkages, and kinetic sculptures.",
        "creative_projects"
    ),
    (
        "stop motion claymation puppet armature fabrication behind the scenes",
        "Animation Craft",
        "entertainment_creative",
        "Precision ball-and-socket armatures and claymation character mechanics.",
        "animation"
    ),
    (
        "marble run complex rube goldberg kinetic physics machine",
        "Kinetic Engineering",
        "entertainment_creative",
        "Intricate chain reactions and mechanical kinetic marble runs.",
        "creative_projects"
    ),
    # 9. Eccentric Sports & Physics
    (
        "blindfolded speedcubing 3x3 memory tracing algorithmic execution",
        "Algorithmic Speedcubing",
        "practitioner_deep_dive",
        "Algorithmic commutators and spatial memory tracing in blindfolded cube solving.",
        "feat"
    ),
    (
        "competitive stone skipping hydrodynamics spin angle world record",
        "Hydrodynamics & Sport",
        "first_principles",
        "Hydrodynamic bounce angles, gyroscopic spin stability, and stone aerodynamics across water.",
        "physics"
    ),
    (
        "curling ice pebble sweeping friction thermal breakdown",
        "Sports Physics",
        "first_principles",
        "Pebbled ice friction manipulation and directional curling broom physics.",
        "sports"
    ),
    # 10. Gastronomy & Food Science
    (
        "traditional alpine cheese cave aging microbiology affinage",
        "Microbial Gastronomy",
        "first_principles",
        "Cave affinage biochemistry, surface molds, and proteolytic aging in alpine cheeses.",
        "food_science"
    ),
    (
        "bean to bar chocolate conching crystal tempering food chemistry",
        "Culinary Science",
        "first_principles",
        "Polymorphic cocoa butter crystal structures and bean-to-bar conching kinetics.",
        "food_science"
    ),
    # 11. Witty Deconstructions & Comedy
    (
        "absurdist game show rules loophole disaster comedic breakdown",
        "Comedic Game Shows",
        "entertainment_comedy",
        "Hilarious breakdown of flawed television game show rules and unintended loopholes.",
        "comedy"
    ),
    (
        "unintended historical consequences weird policy blunders documentary",
        "Historical Ironies",
        "entertainment_story",
        "Bizarre historical unintended consequences and administrative blunders.",
        "story"
    ),
    (
        "standup comedy sharp witty observational sketches funniest moments",
        "Standup & Wit",
        "entertainment_comedy",
        "Witty observational standup comedy and sharp humorous commentary.",
        "comedy"
    ),
]

# High-substance wildcard discovery queries from outside domains
WILDCARD_DISCOVERY_QUERIES: List[Tuple[str, str, str]] = [
    (
        "deep sea hydrothermal vent chemosynthesis ecosystem",
        "Marine Astrobiology",
        "High-quality wildcard exploration: life sustained by hydrothermal chemosynthesis in the deep ocean."
    ),
    (
        "ancient scripts Indus Valley undeciphered epigraphy",
        "Ancient Epigraphy",
        "High-quality wildcard exploration: decipherment methodologies and inscription analyses of undeciphered ancient scripts."
    ),
    (
        "electrical grid frequency synchronization black start engineering",
        "Infrastructure Systems",
        "High-quality wildcard exploration: rotational inertia and dynamic frequency stabilization in power grids."
    ),
    (
        "antarctic ice core paleoclimatology atmospheric trapped gas",
        "Paleoclimatology",
        "High-quality wildcard exploration: extracting million-year-old atmospheric air bubbles from Antarctic ice cores."
    ),
    (
        "gravitational wave laser interferometry precision optical measurement",
        "Experimental Physics",
        "High-quality wildcard exploration: subatomic laser displacement measurement in gravitational wave detectors."
    ),
    (
        "mycelial underground networks forest resource allocation ecology",
        "Mycology & Forest Ecology",
        "High-quality wildcard exploration: fungal mycorrhizal networks mediating biochemical exchange among trees."
    ),
    (
        "stained glass medieval chemistry gold nanoparticle optical physics",
        "Historical Nanotechnology",
        "High-quality wildcard exploration: localized surface plasmon resonance in medieval stained glass chemistry."
    ),
    (
        "submarine fiber optic communications cable laying oceanic engineering",
        "Telecommunications Infrastructure",
        "High-quality wildcard exploration: deep-ocean repeaters and abyssal cable deployment engineering."
    ),
]

ENTERTAINMENT_WILDCARD_QUERIES: List[Tuple[str, str, str]] = [
    (
        "absurdist game show rules loophole disaster breakdown",
        "Comedic Game Shows",
        "Wildcard entertainment discovery: hilarious breakdown of flawed television game show rules and loopholes."
    ),
    (
        "bizarre real life heist stories animated narrative",
        "Bizarre Lore & Storytelling",
        "Wildcard entertainment discovery: strange unbelievable real-life heist tale with vivid narrative."
    ),
    (
        "ingenious creative maker hacks satisfying build",
        "Creative Engineering Showcase",
        "Wildcard entertainment discovery: inventive mechanical and creative projects built with DIY flair."
    ),
    (
        "hilarious live orchestra musical comedy performance",
        "Musical Comedy Performance",
        "Wildcard entertainment discovery: humorous comedic orchestral performance and musical wit."
    ),
    (
        "stop motion claymation short film creative showcase",
        "Animation & Visual Craft",
        "Wildcard entertainment discovery: delightful artisan claymation and creative storytelling."
    ),
    (
        "impossible trick shots compilation sports moments",
        "Spectacular Stunts & Highlights",
        "Wildcard entertainment discovery: mind-bending trick shot compilation and athletic timing."
    ),
]

ENTERTAINMENT_FORMAT_TO_DISTANT_QUERIES: Dict[str, List[Tuple[str, str, str]]] = {
    "comedy": [
        ("standup comedy improv sketches funniest moments", "Comedy & Improv", "Distant entertainment find: sharp standup and improvised comedic scenes."),
        ("parody movie sketches hilarious comedic satire", "Comedic Satire", "Distant entertainment find: brilliant parody sketches and comedic satire."),
    ],
    "gameplay": [
        ("legendary speedrun clutch gameplay moments showcase", "Speedrunning & Gaming", "Distant entertainment find: high-stakes gaming clutches and world record runs."),
        ("blind playthrough unexpected chaotic gameplay moments", "Chaos Gameplay", "Distant entertainment find: hilarious blind play and unexpected gaming reactions."),
    ],
    "challenges": [
        ("hardcore survival challenge tournament world record", "Extreme Challenges", "Distant entertainment find: endurance survival tournaments and creative challenges."),
    ],
    "sports_moments": [
        ("greatest buzzer beaters walk off sports highlights", "Clutch Sports Moments", "Distant entertainment find: unforgettable last-second athletic triumphs."),
    ],
    "performance": [
        ("unbelievable street musician live performance crowd reaction", "Live Music Performance", "Distant entertainment find: virtuosic street performance and live acoustic improv."),
    ],
    "animation": [
        ("award winning 3d animated short film CGI storytelling", "Animated Short Films", "Distant entertainment find: award-winning independent animation and creative visual craft."),
    ],
    "creative_projects": [
        ("kinetic sculpture mechanical automaton build showcase", "Artisan Automata", "Distant entertainment find: mesmerizing hand-crafted mechanical automata."),
    ],
    "compelling_story": [
        ("strange unbelievable historical misadventures bizarre tales", "Unbelievable Misadventures", "Distant entertainment find: funny, stranger-than-fiction true stories."),
    ],
}


class QueryExpander:
    def __init__(self, api_key: Optional[str] = None):
        if api_key is not None:
            self.api_key = api_key.strip() if api_key.strip() else None
        else:
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.last_mode: str = "uninitialized"
        self.last_error: Optional[str] = None

    def is_text_avoided(self, text: Optional[str], avoided_terms: List[str]) -> bool:
        """
        Checks whether text contains any avoided topics or their common inflectional forms.
        Uses word boundaries to prevent false positives (e.g., 'pop' in 'protoplasmic' or 'betting' in 'better').
        """
        if not text or not avoided_terms:
            return False
        t_lower = text.lower()
        for term in avoided_terms:
            term_clean = term.strip().lower()
            if not term_clean:
                continue

            # Special case for history: match history, historic, historical, historian
            if term_clean in ("history", "historic", "historical"):
                if re.search(r"\bhistor\w*\b", t_lower, re.IGNORECASE):
                    return True
            else:
                # Word boundary match with optional plural or possessive
                pattern = r"\b" + re.escape(term_clean) + r"(?:s|es|'s)?\b"
                if re.search(pattern, t_lower, re.IGNORECASE):
                    return True
                # Direct phrase match for multi-word phrases
                if " " in term_clean and term_clean in t_lower:
                    return True
                # Substring check for longer terms
                if len(term_clean) > 3 and term_clean in t_lower:
                    return True
        return False

    def is_query_avoided(self, query: CuriosityQuery, avoided_terms: List[str]) -> bool:
        """Checks whether query string, domain, or rationale contains any avoided topic."""
        return (
            self.is_text_avoided(query.query, avoided_terms)
            or self.is_text_avoided(query.domain, avoided_terms)
            or self.is_text_avoided(query.rationale, avoided_terms)
        )

    def _generate_independent_discovery_queries(
        self,
        preferences: UserPreferences,
        avoided_terms: List[str],
        count: int = 1
    ) -> List[CuriosityQuery]:
        """
        Generates independent discovery queries without using initial interests,
        saved categories, followed creators, or learned subject affinities as search seeds.
        Rotates across broad areas and formats over refreshes using recent query and impression history.
        """
        # 1. User stated interests and categories to exclude from seeds
        seed_words = set()
        raw_seed = f"{preferences.interests} {' '.join(preferences.categories)}".lower()
        for w in re.findall(r"[a-z0-9]+", raw_seed):
            if len(w) >= 3 and w not in STOPWORDS:
                seed_words.add(w)

        # 2. Followed creators to exclude
        creator_names = {fc.name.lower() for fc in (preferences.followed_creators or []) if fc.name}

        # 3. Learned subject affinities from living taste profile to exclude
        learned_domains = set()
        if preferences.living_taste_profile:
            lp = preferences.living_taste_profile
            for s, _ in (lp.get_established_subjects() + lp.get_tentative_subjects()):
                learned_domains.add(s.lower())
            for sig in lp.recent_signals:
                if sig.domain:
                    learned_domains.add(sig.domain.lower())

        # 4. Recent queries and recently shown subjects (to prevent repetition across refreshes)
        recent_searches = {q.lower() for q in (preferences.recent_queries or [])}
        recent_shown = {s.lower() for s in (preferences.recent_shown_subjects or [])}

        eligible_items = []
        for q_str, domain, q_type, rationale, fmt in INDEPENDENT_DISCOVERY_CATALOG:
            # Check avoided topics
            if self.is_text_avoided(q_str, avoided_terms) or self.is_text_avoided(domain, avoided_terms):
                continue

            # Check overlap with seed interests / categories
            q_tokens = _extract_tokens(f"{q_str} {domain}")
            if q_tokens & seed_words:
                continue

            # Check followed creators
            if any(cn in domain.lower() for cn in creator_names):
                continue

            # Check learned subject affinities
            if any(ld in domain.lower() or ld in q_str.lower() for ld in learned_domains):
                continue

            # Check recent queries and recently shown subjects
            if any(rs in q_str.lower() or rs in domain.lower() for rs in recent_searches):
                continue
            if any(sh in domain.lower() for sh in recent_shown):
                continue

            eligible_items.append((q_str, domain, q_type, rationale, fmt))

        # Fallback if filters were too restrictive (relaxing recent history but keeping avoided/seed exclusions)
        if not eligible_items:
            for q_str, domain, q_type, rationale, fmt in INDEPENDENT_DISCOVERY_CATALOG:
                if self.is_text_avoided(q_str, avoided_terms) or self.is_text_avoided(domain, avoided_terms):
                    continue
                q_tokens = _extract_tokens(f"{q_str} {domain}")
                if q_tokens & seed_words:
                    continue
                eligible_items.append((q_str, domain, q_type, rationale, fmt))

        if not eligible_items:
            for q_str, domain, q_type, rationale, fmt in INDEPENDENT_DISCOVERY_CATALOG:
                if not (self.is_text_avoided(q_str, avoided_terms) or self.is_text_avoided(domain, avoided_terms)):
                    eligible_items.append((q_str, domain, q_type, rationale, fmt))

        if not eligible_items:
            return []

        selected_queries: List[CuriosityQuery] = []
        start_idx = (preferences.refresh_index * 2) % len(eligible_items)
        for i in range(count):
            item = eligible_items[(start_idx + i) % len(eligible_items)]
            selected_queries.append(CuriosityQuery(
                query=item[0],
                domain=item[1],
                query_type=item[2],
                rationale=item[3],
                discovery_type="independent",
                discovery_rationale=f"Independent discovery exploring {item[1]}."
            ))

        return selected_queries

    def _generate_discovery_queries(
        self,
        preferences: UserPreferences,
        avoided_terms: List[str],
        seed: int
    ) -> List[CuriosityQuery]:
        """
        Generates genuine discovery queries:
        1. Adjacent: connected to an established interest or learned taste subject as a lateral branch.
        2. Independent: completely independent broad areas outside user's seed interests and affinities.
        3. Wildcard / Distant: exploratory high-substance find.
        """
        if preferences.active_category:
            return []

        discovery_queries: List[CuriosityQuery] = []
        lp = preferences.living_taste_profile

        # 1. Adjacent Discovery Query (lateral branch branching from user interest)
        adjacent_candidate = None
        adjacent_subject = None
        if preferences.feed_mode == "entertainment":
            raw_text = (preferences.categories[0] if preferences.categories else preferences.interests).strip()
            adjacent_subject = raw_text
            adj_ent_opts = [
                (f"{adjacent_subject} bloopers outtakes funny commentary comedy", f"{adjacent_subject.title()} Comedy", f"Adjacent comedic find branching from {adjacent_subject}."),
                (f"{adjacent_subject} creative challenge tournament epic moments", f"{adjacent_subject.title()} Challenges", f"Adjacent high-stakes challenge and tournament in {adjacent_subject}."),
                (f"{adjacent_subject} satisfying creative build showcase animation", f"{adjacent_subject.title()} Creative Showcase", f"Adjacent creative showcase and visual craft in {adjacent_subject}."),
            ]
            adj_chosen = self._select_safe_option(adj_ent_opts, avoided_terms, seed)
            if adj_chosen:
                discovery_queries.append(CuriosityQuery(
                    query=adj_chosen[0],
                    domain=adj_chosen[1],
                    query_type="entertainment_comedy" if "bloopers" in adj_chosen[0] else "entertainment_creative",
                    rationale=adj_chosen[2],
                    discovery_type="adjacent",
                    discovery_rationale=f"Adjacent entertainment discovery branching from '{adjacent_subject}'."
                ))
        else:
            if lp:
                candidates = [s for s, _ in (lp.get_established_subjects() + lp.get_tentative_subjects())]
                for s in candidates:
                    s_lower = s.lower()
                    for dom_name, dom_data in DOMAIN_DEFINITIONS.items():
                        if any(kw in s_lower for kw in dom_data["match_keywords"]):
                            cd_map = dom_data.get("cross_domain_analogy", {})
                            cand = cd_map.get("balanced") or cd_map.get("focused") or cd_map.get("radical")
                            if cand and not (self.is_text_avoided(cand[0], avoided_terms) or self.is_text_avoided(cand[1], avoided_terms) or self.is_text_avoided(cand[2], avoided_terms)):
                                adjacent_candidate = cand
                                adjacent_subject = s
                                break
                    if adjacent_candidate:
                        break

            if not adjacent_candidate and lp:
                candidates = [s for s, _ in (lp.get_established_subjects() + lp.get_tentative_subjects())]
                if candidates:
                    top_sub = candidates[0]
                    if not self.is_text_avoided(top_sub, avoided_terms):
                        q_str = f"{top_sub} interdisciplinary systems analysis"
                        if not self.is_text_avoided(q_str, avoided_terms):
                            adjacent_candidate = (
                                q_str,
                                f"{top_sub.title()} Systems",
                                f"Adjacent exploration branching from your ongoing engagement with {top_sub}."
                            )
                            adjacent_subject = top_sub

            if not adjacent_candidate:
                raw_text = (preferences.categories[0] if preferences.categories else preferences.interests).strip()
                raw_lower = raw_text.lower()
                adjacent_subject = raw_text
                for dom_name, dom_data in DOMAIN_DEFINITIONS.items():
                    if any(kw in raw_lower for kw in dom_data["match_keywords"]):
                        cd_map = dom_data.get("cross_domain_analogy", {})
                        cand = cd_map.get("balanced") or cd_map.get("focused") or cd_map.get("radical")
                        if cand and not (self.is_text_avoided(cand[0], avoided_terms) or self.is_text_avoided(cand[1], avoided_terms) or self.is_text_avoided(cand[2], avoided_terms)):
                            adjacent_candidate = cand
                            break

            if adjacent_candidate:
                discovery_queries.append(CuriosityQuery(
                    query=adjacent_candidate[0],
                    domain=adjacent_candidate[1],
                    query_type="cross_domain_analogy",
                    rationale=adjacent_candidate[2],
                    discovery_type="adjacent",
                    discovery_rationale=f"Adjacent discovery branching from your interest in '{adjacent_subject}'."
                ))
            elif adjacent_subject and not self.is_text_avoided(adjacent_subject, avoided_terms):
                q_str = f"{adjacent_subject} interdisciplinary systems analysis"
                if not self.is_text_avoided(q_str, avoided_terms):
                    discovery_queries.append(CuriosityQuery(
                        query=q_str,
                        domain=f"{adjacent_subject.title()} Systems",
                        query_type="cross_domain_analogy",
                        rationale=f"Interdisciplinary analysis branching from '{adjacent_subject}'.",
                        discovery_type="adjacent",
                        discovery_rationale=f"Adjacent exploration connected to '{adjacent_subject}'."
                    ))

        # 2. Independent Discovery Query (completely independent from seed topics)
        indep_queries = self._generate_independent_discovery_queries(preferences, avoided_terms, count=1)
        discovery_queries.extend(indep_queries)

        # 3. Distant / Wildcard Discovery Query
        if preferences.feed_mode == "entertainment":
            wild_opt = self._select_safe_option(
                ENTERTAINMENT_WILDCARD_QUERIES,
                avoided_terms,
                seed + (preferences.refresh_index * 2) + 1
            )
            if wild_opt:
                discovery_queries.append(CuriosityQuery(
                    query=wild_opt[0],
                    domain=wild_opt[1],
                    query_type="entertainment_creative",
                    rationale=wild_opt[2],
                    discovery_type="wildcard",
                    discovery_rationale="High-quality entertainment wildcard outside your current subjects and tabs."
                ))
        else:
            top_quality = None
            if lp:
                top_q_list = lp.get_top_qualities(top_n=2)
                if top_q_list:
                    top_quality = top_q_list[0][0]

            if not top_quality:
                exp = (preferences.experience_type or "mix").lower()
                if "fun" in exp or "entertain" in exp:
                    top_quality = "humor"
                elif "thought" in exp or "deep" in exp:
                    top_quality = "technical depth"
                elif "surpris" in exp:
                    top_quality = "unusual connections"
                else:
                    top_quality = "visual explanation"

            options = QUALITY_TO_DISTANT_QUERIES.get(top_quality, QUALITY_TO_DISTANT_QUERIES["visual explanation"])
            distant_opt = self._select_safe_option(options, avoided_terms, seed + preferences.refresh_index)
            if not distant_opt:
                for alt_q, alt_opts in QUALITY_TO_DISTANT_QUERIES.items():
                    distant_opt = self._select_safe_option(alt_opts, avoided_terms, seed + preferences.refresh_index)
                    if distant_opt:
                        top_quality = alt_q
                        break

            if distant_opt:
                discovery_queries.append(CuriosityQuery(
                    query=distant_opt[0],
                    domain=distant_opt[1],
                    query_type="cross_domain_analogy",
                    rationale=distant_opt[2],
                    discovery_type="distant",
                    discovery_rationale=f"Distant discovery matched to your affinity for '{top_quality}'."
                ))

            wild_opt = self._select_safe_option(
                WILDCARD_DISCOVERY_QUERIES,
                avoided_terms,
                seed + (preferences.refresh_index * 2) + 1
            )
            if wild_opt:
                discovery_queries.append(CuriosityQuery(
                    query=wild_opt[0],
                    domain=wild_opt[1],
                    query_type="cross_domain_analogy",
                    rationale=wild_opt[2],
                    discovery_type="wildcard",
                    discovery_rationale="High-quality wildcard exploration outside your current subjects and tabs."
                ))

        return discovery_queries

    def _select_safe_option(
        self,
        options: List[Tuple[str, str, str]],
        avoided_terms: List[str],
        seed: int
    ) -> Optional[Tuple[str, str, str]]:
        """
        Returns a genuinely clean option from candidate tuples where query, domain,
        and rationale are all safe from avoided topics. Returns None if all options conflict.
        """
        clean_options = [
            opt for opt in options
            if not (self.is_text_avoided(opt[0], avoided_terms)
                    or self.is_text_avoided(opt[1], avoided_terms)
                    or self.is_text_avoided(opt[2], avoided_terms))
        ]
        if clean_options:
            return clean_options[seed % len(clean_options)]
        return None

    def sanitize_and_filter_avoided(
        self,
        queries: List[CuriosityQuery],
        preferences: UserPreferences
    ) -> List[CuriosityQuery]:
        """
        One final avoidance check applied to all generated queries (Gemini or heuristic).
        Verifies query string, domain, and rationale against avoided topics.
        Omits conflicting directions, prevents duplicate queries, and ensures no prohibited
        searches are silently produced.
        """
        avoided_terms = [part.strip().lower() for part in re.split(r"[,;\n|]", preferences.avoid_topics) if part.strip()]
        if not avoided_terms:
            seen = set()
            deduped = []
            for q in queries:
                qn = q.query.strip().lower()
                if qn not in seen:
                    seen.add(qn)
                    deduped.append(q)
            return deduped

        safe_queries: List[CuriosityQuery] = []
        seen = set()

        for q in queries:
            if self.is_query_avoided(q, avoided_terms):
                logger.info(
                    f"Final avoidance check omitted query '{q.query}' (domain: '{q.domain}') "
                    f"matching avoided topics: {preferences.avoid_topics}"
                )
                continue

            qn = q.query.strip().lower()
            if qn in seen:
                continue
            seen.add(qn)
            safe_queries.append(q)

        return safe_queries

    def expand_queries(self, preferences: UserPreferences, count: int = 5) -> List[CuriosityQuery]:
        """Generate high-entropy curiosity queries based on user preferences."""
        raw_queries: List[CuriosityQuery] = []
        if self.api_key:
            try:
                raw_queries = self._expand_with_gemini(preferences, count)
                self.last_mode = "gemini"
                self.last_error = None
            except Exception as e:
                self.last_mode = "gemini_failed_fallback"
                self.last_error = str(e)
                logger.warning(f"Gemini API query expansion failed: {e}. Falling back to heuristic expansion.")
                raw_queries = self._expand_with_heuristics(preferences, count)
        else:
            self.last_mode = "no_key_heuristic"
            self.last_error = None
            raw_queries = self._expand_with_heuristics(preferences, count)

        # Apply final avoidance check across all generated queries (Gemini or heuristic)
        return self.sanitize_and_filter_avoided(raw_queries, preferences)

    def _expand_with_gemini(self, preferences: UserPreferences, count: int = 5) -> List[CuriosityQuery]:
        """Use Gemini to generate lateral curiosity queries without requiring user-specified explore fields."""
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                client_args={"trust_env": False, "timeout": 15.0}
            )
        )

        avoid_instruction = f'Avoided topics: "{preferences.avoid_topics}"' if preferences.avoid_topics else "No topics to avoid."
        familiar_instruction = f'Viewer background / familiar topics: "{preferences.familiar_topics}"' if preferences.familiar_topics else "No specific background specified."
        goal_instruction = f'Session goal: "{preferences.learning_goal}"' if preferences.learning_goal else "Open curiosity session."

        dislike_instruction = ""
        if preferences.disliked_records:
            from rabbithole_app.filter_engine import summarize_dislike_patterns
            dislike_summary = summarize_dislike_patterns(preferences.disliked_records)
            if dislike_summary:
                dislike_instruction = f"User Dislike Feedback / Patterns (from current session history):\n{dislike_summary}\n"

        interest_state_section = ""
        from rabbithole_app.filter_engine import summarize_interest_state
        interest_state_summary = summarize_interest_state(preferences)
        if interest_state_summary:
            interest_state_section = f"\n{interest_state_summary}\n"

        if preferences.feed_mode == "entertainment":
            mode_guidance = f"""
        Feed Mode: 'entertainment' (PURE ENTERTAINMENT & ENGAGING VIEWING).
        CRITICAL ENTERTAINMENT RULES:
        - NEVER generate queries for dry university lectures, academic symposia, classroom tutorials, or formal textbook proofs.
        - Formulate queries for genuinely engaging, funny, thrilling, or creative videos:
          * 'entertainment_comedy': Comedic commentary, sketches, witty satire, hilarious deconstructions, or funny outtakes.
          * 'entertainment_gameplay': Thrilling gameplay, clever gaming challenges, speedruns, or creative mechanics.
          * 'entertainment_challenge': High-stakes competitions, strategic challenges, tournament showdowns, or fun feats.
          * 'entertainment_creative': Creative projects, chaotic inventions, inventive animations, or satisfying showcases.
          * 'entertainment_performance': Musical performances, covers, comedic songs, or athletic highlights.
          * 'entertainment_story': Stranger-than-fiction lore, bizarre historical tales, or entertaining narrative journeys.
        - When the viewer has followed creators (e.g. from YouTube), actively formulate 1 or 2 queries around those creators or their entertaining comedy/gaming/storytelling niche.
        - DO NOT default to repetitive gimmick tropes like marble runs, domino drops, or rube goldberg machines.
            """
        elif preferences.feed_mode == "mix":
            mode_guidance = f"""
        Feed Mode: 'mix' (BALANCED DISCOVERY FEED).
        Blend substantive exploration with pure creative entertainment matching their taste.
        Formulate exactly {count} distinct queries using:
        - 'first_principles': Accessible core highlight or foundational mechanism.
        - 'entertainment_creative': Creative craft, visual animation, or entertaining showcase.
        - 'cross_domain_analogy': Lateral bridge or unexpected analogy.
        - 'entertainment_comedy': Comedic take, bizarre story, or witty breakdown.
        - 'practitioner_deep_dive': Concrete artisan technique or field practice.
            """
        else:
            mode_guidance = f"""
        Feed Mode: 'deep_learning' (5-STEP PROGRESSIVE PATH).
        Formulate exactly {count} distinct search queries for an exploratory viewing path:
        1. 'first_principles': Foundational mechanisms or fundamental principles of the core subject.
        2. 'archive_lecture': Archival pioneer lecture, symposium, masterclass, or demonstration.
        3. 'cross_domain_analogy': Surprising lateral bridge to an unexpected discipline outside the user's usual content.
        4. 'niche_history': Forgotten historical dispute, origin story, or technological counter-narrative.
        5. 'practitioner_deep_dive': Concrete artisan craftsmanship, workshop case study, or field practice.
            """

        followed_instruction = ""
        if preferences.followed_creators:
            from rabbithole_app.filter_engine import summarize_followed_creators
            followed_summary = summarize_followed_creators(preferences.followed_creators)
            if followed_summary:
                followed_instruction = f"{followed_summary}\n"

        category_instruction = ""
        if preferences.active_category:
            category_instruction = f"""
        CRITICAL FOCUSED CATEGORY TAB RULE:
        Active Tab: '{preferences.active_category}'.
        You are generating queries for a focused category tab dedicated exclusively to '{preferences.active_category}'.
        - Every single query MUST be strictly about {preferences.active_category} or its recognized core subfields, terminology, and tactical/analytical depth.
        - DO NOT generate cross-domain lateral leaps to unrelated disciplines (e.g. NEVER leap from Baseball to corporate business strategy, and NEVER leap from Mathematics to cooking recipes or woodworking).
        - DO NOT generate discovery queries (discovery_type should be null) because this is a focused category tab, not the main feed.
        - Ensure all {count} queries search for substantive videos that genuinely belong inside the '{preferences.active_category}' tab.
            """

        discovery_instruction = ""
        if not preferences.active_category:
            if preferences.feed_mode == "entertainment":
                discovery_instruction = """
        GENUINE DISCOVERY REQUIREMENT (Entertainment Main Feed):
        Reserve 2 to 3 of your queries specifically for genuine entertainment discovery outside familiar bounds:
        - 1 'adjacent' query (discovery_type: "adjacent"): an adjacent entertainment direction branching from their taste (e.g. comedic commentary, game highlights, or creative builds in an adjacent space).
        - 1 'distant' query (discovery_type: "distant"): an unfamiliar entertainment realm guided by their preferred entertainment format, curiosity style, or comedic wit.
        - 1 'wildcard' query (discovery_type: "wildcard"): a delightful, unexpected entertainment find completely outside their usual subjects (e.g. hilarious game show rules loop, bizarre historical impostors, creative maker hacks).
        - DO NOT default to repetitive gimmick tropes like marble runs, domino drops, or rube goldberg machines.
        - DO NOT generate dry academic lectures or classroom tutorials for entertainment discovery.
        For all discovery queries, provide discovery_type ("adjacent", "distant", or "wildcard") and discovery_rationale.
                """
            else:
                discovery_instruction = """
        GENUINE DISCOVERY REQUIREMENT (Main Feed):
        Reserve 2 to 3 of your queries specifically for genuine discovery outside familiar category bounds:
        - 1 'adjacent' query (discovery_type: "adjacent"): an adjacent idea or subfield branching laterally from established or emerging taste subjects (or initial interests if taste profile is new).
        - 1 'distant' query (discovery_type: "distant"): an unfamiliar domain guided by the viewer's preferred viewing qualities / style (e.g. visual explanation, storytelling, artisan craft, technical depth, unusual connections) rather than their initial typed interests.
        - 1 'wildcard' query (discovery_type: "wildcard"): an outside domain completely beyond both the initial categories and established taste subjects.

        CRITICAL TASTE PROFILE EVOLUTION RULE:
        When a Living Taste Profile is present (demonstrated subject affinities, preferred viewing qualities, or followed creators), DO NOT allow the initial typed interests to permanently anchor distant and wildcard searches.
        The viewer's learned qualities and demonstrated affinities must steer discovery into new directions.
        For all discovery queries, provide discovery_type ("adjacent", "distant", or "wildcard") and discovery_rationale explaining honestly why this direction was chosen.
                """

        personality_section = ""
        if preferences.has_personality_preferences:
            arch_descs = []
            for a_id in preferences.curiosity_archetypes:
                if a_id in CURIOSITY_ARCHETYPES:
                    arch_info = CURIOSITY_ARCHETYPES[a_id]
                    arch_descs.append(f"  • {arch_info['name']}: {arch_info['description']}")
            p_parts = []
            if arch_descs:
                p_parts.append("Curiosity Archetypes & Cognitive Drives:\n" + "\n".join(arch_descs))
            if preferences.curiosity_statement:
                p_parts.append(f"Viewer's Curiosity Statement: \"{preferences.curiosity_statement.strip()}\"")
            if preferences.intellectual_vibes:
                vibes_str = ", ".join(v.replace('_', ' ').title() for v in preferences.intellectual_vibes)
                p_parts.append(f"Desired Intellectual Vibes: {vibes_str}")
            if preferences.living_taste_profile and preferences.living_taste_profile.subject_affinities:
                top_subs = [s for s, _ in sorted(preferences.living_taste_profile.subject_affinities.items(), key=lambda x: x[1], reverse=True)[:5]]
                if top_subs:
                    p_parts.append(f"Inferred Latent Discovery Subjects (from YouTube/History): {', '.join(top_subs)}")

            joined_p = "\n".join(p_parts)
            personality_section = f"""
        CRITICAL PERSONALITY-FIRST DISCOVERY INSTRUCTION:
        The viewer has defined their curiosity style by personality archetype and cognitive drive rather than rigid topic bounds:
{joined_p}

        CRITICAL SEARCH STRATEGY:
        - Do NOT simply repeat old seed interest keywords ("{preferences.interests or 'None specified'}") as literal search terms.
        - Treat seed topics purely as calibration of their mental style, NOT as a boundary.
        - Actively originate searches across UNEXPECTED, DIVERSE disciplines tailored to their exact curiosity archetypes, latent subjects, and intellectual vibes.
        - DO NOT default to generic craft tropes like woodworking, Japanese joinery, or pottery unless the viewer specifically has 'craft_purist' selected.
        - Synthesize novel cross-disciplinary connections (e.g. applying game theory or algorithmic mechanics to unexpected fields, or finding visual proofs and physical demonstrations in novel sciences).
        - Ensure every refresh explores fresh, unrepeated angles and creators.
            """

        # Collect recent searches, recently shown subjects, and recently shown creators
        recent_queries_list = preferences.recent_queries[-20:] if preferences.recent_queries else []
        if recent_queries_list:
            recent_queries_str = "\n".join(f"  • {q}" for q in recent_queries_list)
        else:
            recent_queries_str = "None yet (initial exploration)"

        shown_subjects = []
        if getattr(preferences, "recent_shown_subjects", None):
            shown_subjects.extend([s for s in preferences.recent_shown_subjects if s and s not in shown_subjects])
        if preferences.watched_topics:
            shown_subjects.extend([t for t in preferences.watched_topics if t and t not in shown_subjects])
        if preferences.living_taste_profile and preferences.living_taste_profile.recent_signals:
            for s in preferences.living_taste_profile.recent_signals[:20]:
                if s.domain and s.domain not in shown_subjects:
                    shown_subjects.append(s.domain)
        shown_subjects_str = ", ".join(shown_subjects[:15]) if shown_subjects else "None recorded yet"

        shown_creators = []
        if getattr(preferences, "recent_shown_creators", None):
            shown_creators.extend([c for c in preferences.recent_shown_creators if c and c not in shown_creators])
        if preferences.watched_channels:
            shown_creators.extend([ch for ch in preferences.watched_channels.keys() if ch and ch not in shown_creators])
        if preferences.living_taste_profile and preferences.living_taste_profile.recent_signals:
            for s in preferences.living_taste_profile.recent_signals[:20]:
                if s.channel and s.channel not in shown_creators:
                    shown_creators.append(s.channel)
        if preferences.followed_creators:
            for fc in preferences.followed_creators:
                if fc.name and fc.name not in shown_creators:
                    shown_creators.append(fc.name)
        shown_creators_str = ", ".join(shown_creators[:15]) if shown_creators else "None recorded yet"

        refresh_section = f"""
        FEED REFRESH & DIVERSITY PLANNING (Turn #{preferences.refresh_index}):
        - Previous search directions already explored:
{recent_queries_str}
        - Recently shown subjects/domains: {shown_subjects_str}
        - Recently shown creators/channels: {shown_creators_str}
        - MANDATORY REFRESH DIRECTIVE: The user has refreshed the feed (Turn #{preferences.refresh_index}). You must NOT repeat, recycle, or marginally reword previous search directions. Plan completely different specific angles, alternative sub-disciplines, distinct creator pools, and novel entry points while preserving the active category ('{preferences.active_category or "Main Feed"}') and viewing mode ('{preferences.feed_mode}').
        """

        prompt = f"""
        User's Curiosity Profile: {preferences.curiosity_summary}
        Seed Interests (if any): "{preferences.interests or 'None specified'}"
        Feed Refresh Count: {preferences.refresh_index}
        Upload Recency Preference: {preferences.recency_preference}
        Active Category: {preferences.active_category or 'All / Main Feed'}
        Experience Type: {preferences.experience_type}
        {refresh_section}
        {familiar_instruction}
        {avoid_instruction}
        {goal_instruction}
        {dislike_instruction}
        {followed_instruction}
        {interest_state_section}
        {personality_section}
        Serendipity Dial (1 to 5): {preferences.serendipity_level}
        Depth Target: {preferences.depth_level}
        Seed Topic / Current Rabbit Hole (if any): {preferences.seed_topic or 'None'}

        {category_instruction}
        {mode_guidance}
        {discovery_instruction}

        CRITICAL SEARCH PLANNING REQUIREMENTS:
        1. Prioritize exploring different specific angles from recent searches and recently shown subjects ({shown_subjects_str}) / creators ({shown_creators_str}).
        2. For recent uploads, include timely subjects and current-year searches; avoid generic best-of archives.
        3. DO NOT expect the user to have specified what fields to search. Actively originate specific, creative directions on your own.
        4. Strictly respect any avoided topics. Keep each query string between 3 and 6 words.
        """

        interaction = client.interactions.create(
            model="gemini-3.5-flash-lite",
            system_instruction=EXPANSION_SYSTEM_PROMPT,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": CuriosityQueryPlan.model_json_schema()
            }
        )

        clean_text = _clean_json_markdown(interaction.output_text or "")
        plan = CuriosityQueryPlan.model_validate_json(clean_text)

        avoided_terms = [part.strip().lower() for part in re.split(r"[,;\n|]", preferences.avoid_topics) if part.strip()]
        seed_str = f"{preferences.interests}_{preferences.serendipity_level}_{preferences.refresh_index}"
        seed = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16)

        # 1. Validate returned query items
        valid_queries: List[CuriosityQuery] = []
        for q in plan.queries:
            q_text = (q.query or "").strip()
            if not q_text or len(q_text.split()) < 2:
                continue
            if not q.domain:
                q.domain = "Curiosity Exploration"
            if q.discovery_type and q.discovery_type not in ("adjacent", "distant", "wildcard", "independent"):
                q.discovery_type = None
            if preferences.active_category:
                q.discovery_type = None
                q.discovery_rationale = None
            valid_queries.append(q)

        # 2. On main feed, ensure discovery tiers are present and valid
        if not preferences.active_category:
            disc_types_present = {q.discovery_type for q in valid_queries if q.discovery_type}
            needed_tiers = ["adjacent", "distant", "wildcard", "independent"]
            missing_tiers = [t for t in needed_tiers if t not in disc_types_present]
            if missing_tiers:
                safe_disc_queries = self._generate_discovery_queries(preferences, avoided_terms, seed)
                safe_by_type = {q.discovery_type: q for q in safe_disc_queries if q.discovery_type}
                for tier in missing_tiers:
                    if tier in safe_by_type:
                        valid_queries.append(safe_by_type[tier])
                    elif tier == "independent":
                        indep_gen = self._generate_independent_discovery_queries(preferences, avoided_terms, count=1)
                        if indep_gen:
                            valid_queries.append(indep_gen[0])

        # 3. If still short of count, backfill with deterministic queries
        if len(valid_queries) < count:
            fallback_queries = self._expand_with_heuristics(preferences, count)
            existing_q_texts = {q.query.lower() for q in valid_queries}
            for fq in fallback_queries:
                if fq.query.lower() not in existing_q_texts:
                    valid_queries.append(fq)
                    existing_q_texts.add(fq.query.lower())
                if len(valid_queries) >= count:
                    break

        return valid_queries[:count]

    def _expand_with_heuristics(self, preferences: UserPreferences, count: int = 5) -> List[CuriosityQuery]:
        """
        Deterministic, high-quality curiosity query generator for broad inputs in no-key mode.
        Produces varied, searchable angles across interests and serendipity levels.
        Strictly respects avoided topics: omits directions when all options conflict rather than
        reusing avoided options or relabeling them.
        """
        raw_text = (preferences.active_category or preferences.interests).strip()
        interests_lower = raw_text.lower()
        avoided_terms = [part.strip().lower() for part in re.split(r"[,;\n|]", preferences.avoid_topics) if part.strip()]

        # Extract clean content tokens from user interests (filtering out common stop words)
        stop_words = {"and", "the", "for", "with", "from", "about", "into", "over", "under", "all", "our"}
        all_tokens = [
            t.strip(",.!?\"' ") for t in raw_text.split()
            if len(t.strip(",.!?\"' ")) > 2 and t.strip(",.!?\"' ").lower() not in stop_words
        ]
        clean_tokens = [t for t in all_tokens if not self.is_text_avoided(t, avoided_terms)]

        # If the user's entire interest is listed as avoided:
        # Do NOT silently produce prohibited searches for the forbidden interest,
        # unless they have curiosity archetypes that can guide safe alternative searches!
        if not clean_tokens:
            if not (preferences.has_personality_preferences and not preferences.active_category):
                logger.warning(
                    f"User interest '{preferences.interests}' conflicts entirely with avoided topics '{preferences.avoid_topics}'. "
                    "Returning 0 queries to prevent prohibited searches."
                )
                return []

        # Archetype-driven queries for Main Feed when personality preferences are present
        if not preferences.active_category and preferences.curiosity_archetypes:
            arch_queries: List[CuriosityQuery] = []
            seen_arch_texts: set[str] = set()
            for a_id in preferences.curiosity_archetypes:
                if a_id in CURIOSITY_ARCHETYPES:
                    arch_info = CURIOSITY_ARCHETYPES[a_id]
                    for idx, angle in enumerate(arch_info["search_angles"]):
                        if not self.is_text_avoided(angle, avoided_terms) and angle.lower() not in seen_arch_texts:
                            seen_arch_texts.add(angle.lower())
                            q_type = "cross_domain_analogy" if idx % 2 == 1 else "first_principles"
                            disc_type = "distant" if idx > 1 else "adjacent"
                            arch_queries.append(
                                CuriosityQuery(
                                    query=angle,
                                    domain=arch_info["name"],
                                    query_type=q_type,
                                    rationale=f"Selected for curiosity archetype '{arch_info['name']}'.",
                                    discovery_type=disc_type,
                                    discovery_rationale=f"Cross-disciplinary search exploring {arch_info['name']}."
                                )
                            )
            if arch_queries:
                rot_offset = (preferences.refresh_index * 2) % len(arch_queries)
                rotated_arch = arch_queries[rot_offset:] + arch_queries[:rot_offset]
                # If no seed interests were given or seed was avoided, use archetype queries directly
                if not raw_text or not clean_tokens:
                    return rotated_arch[:count]

        # Deterministic seed for reproducible selection across runs, rotating on refresh
        if preferences.refresh_index == 0 and preferences.feed_mode == "deep_learning":
            seed_str = f"{raw_text.lower().strip()}_{preferences.serendipity_level}"
        else:
            seed_str = f"{raw_text.lower().strip()}_{preferences.serendipity_level}_{preferences.refresh_index}_{preferences.feed_mode}"
        seed = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16)

        # Check dislike feedback patterns for heuristic tuning
        dislike_reasons = [r.chosen_reason for r in (preferences.disliked_records or []) if r.chosen_reason]
        too_academic_bias = "Too academic" in dislike_reasons
        too_shallow_bias = "Too shallow" in dislike_reasons
        # If user disliked items as "Too academic", shift seed offset to favor more applied/practitioner angles
        if too_academic_bias:
            seed += 3
        elif too_shallow_bias:
            seed += 1

        # If multiple categories exist or a living taste profile is present, expand for Main Feed
        if not preferences.active_category and (len(preferences.categories) > 1 or preferences.living_taste_profile is not None):
            multi_queries: List[CuriosityQuery] = []
            seen_query_strings: set[str] = set()
            
            initial_cats = [c.strip() for c in preferences.categories if c.strip()]
            if not initial_cats and preferences.interests.strip():
                initial_cats = [preferences.interests.strip()]

            established_subs = []
            if preferences.living_taste_profile:
                established_subs = [s for s, _ in preferences.living_taste_profile.get_established_subjects(min_count=2)]

            lp_signals_count = len(preferences.living_taste_profile.recent_signals) if preferences.living_taste_profile else 0

            # Dynamic weighting: Reduce the influence of initial typed preferences as real viewing evidence accumulates
            if established_subs and lp_signals_count >= 2:
                decay_weight = max(0.20, 1.0 / (1.0 + 0.25 * lp_signals_count))
                if decay_weight < 0.5:
                    categories = established_subs[:2] + initial_cats[:1]
                else:
                    categories = initial_cats[:1] + established_subs[:1]
            else:
                categories = initial_cats or [preferences.interests.strip() or "Curiosity Exploration"]

            # Generate 2 to 3 genuine discovery queries across tiers
            disc_queries = self._generate_discovery_queries(preferences, avoided_terms, seed)
            disc_count = len(disc_queries)
            cat_target = max(len(categories), count - disc_count)
            queries_per_cat = max(1, (cat_target + len(categories) - 1) // len(categories))

            for cat in categories:
                cat_prefs = preferences.model_copy(update={"active_category": cat, "categories": []})
                cat_res = self._expand_with_heuristics(cat_prefs, count=queries_per_cat)
                for q in cat_res:
                    if q.query not in seen_query_strings:
                        seen_query_strings.add(q.query)
                        multi_queries.append(q)

            # Ensure discovery queries are present
            for dq in disc_queries:
                if dq.query not in seen_query_strings:
                    seen_query_strings.add(dq.query)
                    multi_queries.append(dq)

            if multi_queries:
                if len(multi_queries) > count:
                    disc_in_multi = [q for q in multi_queries if q.discovery_type]
                    non_disc_in_multi = [q for q in multi_queries if not q.discovery_type]
                    num_disc = min(len(disc_in_multi), count - 1)
                    num_non = count - num_disc
                    sorted_disc = sorted(
                        disc_in_multi,
                        key=lambda x: 0 if x.discovery_type in ("distant", "wildcard") else (1 if x.discovery_type == "adjacent" else 2)
                    )
                    return (non_disc_in_multi[:num_non] + sorted_disc[:num_disc])[:count]
                return multi_queries[:count]

        # Match domain definition, but only match on keywords that are not themselves avoided!
        matched_domain = None
        for domain_name, data in DOMAIN_DEFINITIONS.items():
            active_keywords = [kw for kw in data["match_keywords"] if not self.is_text_avoided(kw, avoided_terms)]
            if any(kw in interests_lower for kw in active_keywords):
                matched_domain = domain_name
                break

        # Generate queries if matched in comprehensive domain ontology
        if matched_domain:
            domain_cfg = DOMAIN_DEFINITIONS[matched_domain]
            queries: List[CuriosityQuery] = []

            # Determine exploration angles based on feed_mode:
            # - 'entertainment': pure entertainment, humor, craft, and creative storytelling
            # - 'mix': balanced blend of educational and entertaining directions
            # - 'deep_learning': the progressive 5-step deep discovery path
            if preferences.feed_mode == "entertainment":
                # 1. Comedy / Funny
                com_opts = domain_cfg.get("entertainment_comedy", [
                    (f"{raw_text} funny bloopers comedic commentary comedy", "Comedic Wit & Humor", f"Lighthearted and comedic look at {raw_text}.")
                ])
                com_chosen = self._select_safe_option(com_opts, avoided_terms, seed)
                if com_chosen:
                    queries.append(CuriosityQuery(query=com_chosen[0], domain=com_chosen[1], query_type="entertainment_comedy", rationale=com_chosen[2]))

                # 2. Creative Craft & Animation
                crt_opts = domain_cfg.get("entertainment_creative", [
                    (f"{raw_text} creative animation craft satisfying showcase", "Creative Craft & Showcase", f"Creative showcase and animation in {raw_text}.")
                ])
                crt_chosen = self._select_safe_option(crt_opts, avoided_terms, seed + 1)
                if crt_chosen:
                    queries.append(CuriosityQuery(query=crt_chosen[0], domain=crt_chosen[1], query_type="entertainment_creative", rationale=crt_chosen[2]))

                # 3. Lore & Narrative Stories
                sty_opts = domain_cfg.get("entertainment_story", [
                    (f"{raw_text} bizarre strange stories unbelievable history", "Lore & Narrative Tales", f"Engaging stories and lore in {raw_text}.")
                ])
                sty_chosen = self._select_safe_option(sty_opts, avoided_terms, seed + 2)
                if sty_chosen:
                    queries.append(CuriosityQuery(query=sty_chosen[0], domain=sty_chosen[1], query_type="entertainment_story", rationale=sty_chosen[2]))

                # 4. Gameplay & Challenges
                gp_opts = domain_cfg.get("entertainment_gameplay", [
                    (f"{raw_text} challenges tournament epic clutch moments", "Gameplay & Challenges", f"Exciting challenges and gameplay in {raw_text}.")
                ])
                gp_chosen = self._select_safe_option(gp_opts, avoided_terms, seed + 3)
                if gp_chosen:
                    queries.append(CuriosityQuery(query=gp_chosen[0], domain=gp_chosen[1], query_type="entertainment_gameplay", rationale=gp_chosen[2]))

                # 5. Performance & Sports Highlights
                perf_opts = domain_cfg.get("entertainment_performance", [
                    (f"{raw_text} live performance greatest highlights showcase", "Performance & Highlights", f"Thrilling live performance and athletic moments in {raw_text}.")
                ])
                perf_chosen = self._select_safe_option(perf_opts, avoided_terms, seed + 4)
                if perf_chosen:
                    queries.append(CuriosityQuery(query=perf_chosen[0], domain=perf_chosen[1], query_type="entertainment_performance", rationale=perf_chosen[2]))

            elif preferences.feed_mode == "mix":
                # 1. First Principles (Educational)
                fp_chosen = self._select_safe_option(domain_cfg.get("first_principles", []), avoided_terms, seed)
                if fp_chosen:
                    queries.append(CuriosityQuery(query=fp_chosen[0], domain=fp_chosen[1], query_type="first_principles", rationale=fp_chosen[2]))

                # 2. Creative Craft (Entertainment)
                crt_opts = domain_cfg.get("entertainment_creative", [
                    (f"{raw_text} creative animation craft satisfying showcase", "Creative Craft & Showcase", f"Creative showcase and animation in {raw_text}.")
                ])
                crt_chosen = self._select_safe_option(crt_opts, avoided_terms, seed + 1)
                if crt_chosen:
                    queries.append(CuriosityQuery(query=crt_chosen[0], domain=crt_chosen[1], query_type="entertainment_creative", rationale=crt_chosen[2]))

                # 3. Cross-Domain Analogy (Surprise Bridge)
                cd_map = domain_cfg.get("cross_domain_analogy", {})
                if cd_map:
                    tier_order = ["balanced", "focused", "radical"] if preferences.serendipity_level in (3, 4) else (["focused", "balanced", "radical"] if preferences.serendipity_level <= 2 else ["radical", "balanced", "focused"])
                    bridge_candidate = None
                    for tier in tier_order:
                        cand = cd_map.get(tier)
                        if cand and not (self.is_text_avoided(cand[0], avoided_terms) or self.is_text_avoided(cand[1], avoided_terms) or self.is_text_avoided(cand[2], avoided_terms)):
                            bridge_candidate = cand
                            break
                    if bridge_candidate:
                        queries.append(CuriosityQuery(
                            query=bridge_candidate[0],
                            domain=bridge_candidate[1],
                            query_type="cross_domain_analogy",
                            rationale=bridge_candidate[2],
                            discovery_type="adjacent" if not preferences.active_category else None,
                            discovery_rationale=f"Adjacent lateral discovery connecting to {bridge_candidate[1]}." if not preferences.active_category else None
                        ))

                # 4. Comedy / Lore (Entertainment)
                com_opts = domain_cfg.get("entertainment_comedy", []) or domain_cfg.get("entertainment_story", [])
                if not com_opts:
                    com_opts = [(f"{raw_text} funny bloopers comedic commentary comedy", "Comedic Wit & Humor", f"Lighthearted look at {raw_text}.")]
                com_chosen = self._select_safe_option(com_opts, avoided_terms, seed + 2)
                if com_chosen:
                    queries.append(CuriosityQuery(query=com_chosen[0], domain=com_chosen[1], query_type="entertainment_comedy", rationale=com_chosen[2]))

                # 5. Practitioner Deep-Dive (Educational Craft)
                prac_chosen = self._select_safe_option(domain_cfg.get("practitioner_deep_dive", []), avoided_terms, seed + 3)
                if prac_chosen:
                    queries.append(CuriosityQuery(query=prac_chosen[0], domain=prac_chosen[1], query_type="practitioner_deep_dive", rationale=prac_chosen[2]))

            else:
                # 'deep_learning' mode: the 5 progressive exploratory learning steps
                # 1. First Principles
                fp_chosen = self._select_safe_option(domain_cfg["first_principles"], avoided_terms, seed)
                if fp_chosen:
                    queries.append(CuriosityQuery(query=fp_chosen[0], domain=fp_chosen[1], query_type="first_principles", rationale=fp_chosen[2]))

                # 2. Archive / Pioneer Lecture
                arch_chosen = self._select_safe_option(domain_cfg["archive_lecture"], avoided_terms, seed + 1)
                if arch_chosen:
                    queries.append(CuriosityQuery(query=arch_chosen[0], domain=arch_chosen[1], query_type="archive_lecture", rationale=arch_chosen[2]))

                # 3. Cross-Domain Analogy (tuned by serendipity level)
                cd_map = domain_cfg["cross_domain_analogy"]
                if preferences.serendipity_level <= 2:
                    tier_order = ["focused", "balanced", "radical"]
                elif preferences.serendipity_level in (3, 4):
                    tier_order = ["balanced", "focused", "radical"]
                else:
                    tier_order = ["radical", "balanced", "focused"]

                bridge_candidate = None
                for tier in tier_order:
                    cand = cd_map[tier]
                    if not (self.is_text_avoided(cand[0], avoided_terms)
                            or self.is_text_avoided(cand[1], avoided_terms)
                            or self.is_text_avoided(cand[2], avoided_terms)):
                        bridge_candidate = cand
                        break

                if bridge_candidate:
                    queries.append(CuriosityQuery(
                        query=bridge_candidate[0],
                        domain=bridge_candidate[1],
                        query_type="cross_domain_analogy",
                        rationale=bridge_candidate[2],
                        discovery_type="adjacent" if not preferences.active_category else None,
                        discovery_rationale=f"Adjacent lateral bridge connecting to {bridge_candidate[1]}." if not preferences.active_category else None
                    ))

                # 4. Niche History
                hist_chosen = self._select_safe_option(domain_cfg["niche_history"], avoided_terms, seed + 2)
                if hist_chosen:
                    queries.append(CuriosityQuery(query=hist_chosen[0], domain=hist_chosen[1], query_type="niche_history", rationale=hist_chosen[2]))

                # 5. Practitioner Deep-Dive
                prac_chosen = self._select_safe_option(domain_cfg["practitioner_deep_dive"], avoided_terms, seed + 3)
                if prac_chosen:
                    queries.append(CuriosityQuery(query=prac_chosen[0], domain=prac_chosen[1], query_type="practitioner_deep_dive", rationale=prac_chosen[2]))

            if queries:
                return queries[:count]

        # Grounded Fallback for Unrecognized General Topics or when domain options are completely exhausted:
        # Derives topics strictly from clean, non-avoided parts of the user input
        parts = [p.strip() for p in re.split(r"\b(?:and|&|,)\b", raw_text, flags=re.IGNORECASE) if p.strip()]
        clean_parts = [" ".join([w for w in p.split() if not self.is_text_avoided(w, avoided_terms)]) for p in parts if p.strip()]
        clean_parts = [cp for cp in clean_parts if cp]

        if len(clean_parts) >= 2:
            core_topic = clean_parts[0]
            secondary_topic = clean_parts[1]
        elif len(clean_tokens) >= 2:
            mid = max(1, len(clean_tokens) // 2)
            core_topic = " ".join(clean_tokens[:mid])
            secondary_topic = " ".join(clean_tokens[mid:])
        else:
            core_topic = clean_tokens[0]
            secondary_topic = core_topic

        candidate_fallback: List[Tuple[str, str, str, str]] = []

        if preferences.feed_mode == "entertainment":
            candidate_fallback.append((
                f"{core_topic} funniest moments comedic commentary comedy",
                f"Comedic {core_topic.title()} Moments",
                "entertainment_comedy",
                f"Entertaining and humorous takes on {core_topic} without sensationalist clickbait."
            ))
            candidate_fallback.append((
                f"{core_topic} creative artisan craft satisfying showcase",
                f"Creative {core_topic.title()} Showcase",
                "entertainment_creative",
                f"Creative showcase, animation, or artisan craftsmanship centered on {core_topic}."
            ))
            candidate_fallback.append((
                f"{core_topic} bizarre unbelievable stories engaging narrative",
                f"{core_topic.title()} Lore & Tales",
                "entertainment_story",
                f"Engaging narrative storytelling exploring bizarre lore and dramatic moments in {core_topic}."
            ))
            candidate_fallback.append((
                f"{core_topic} challenges tournament epic gameplay moments",
                f"{core_topic.title()} Challenges & Gameplay",
                "entertainment_gameplay",
                f"Exciting challenges, tournament competitions, and gameplay moments in {core_topic}."
            ))
            candidate_fallback.append((
                f"{secondary_topic} live performance greatest highlights showcase",
                f"{secondary_topic.title()} Performance & Highlights",
                "entertainment_performance",
                f"Thrilling athletic highlights and live performance in {secondary_topic}."
            ))
        elif preferences.feed_mode == "mix":
            candidate_fallback.append((
                f"{core_topic} theoretical models structural principles",
                "Foundational Principles",
                "first_principles",
                f"Deconstructs the underlying mechanisms and foundational theories of {core_topic}."
            ))
            candidate_fallback.append((
                f"{core_topic} creative artisan craft satisfying showcase",
                f"Creative {core_topic.title()} Showcase",
                "entertainment_creative",
                f"Creative showcase, animation, or artisan craftsmanship centered on {core_topic}."
            ))
            if preferences.active_category:
                candidate_fallback.append((
                    f"{core_topic} advanced concepts practical breakdown analysis",
                    f"{core_topic.title()} Advanced Analysis",
                    "cross_domain_analogy",
                    f"In-depth analysis and advanced breakdown of core mechanisms in {core_topic}."
                ))
            elif secondary_topic != core_topic and len(secondary_topic) > 2:
                candidate_fallback.append((
                    f"{core_topic} {secondary_topic} interdisciplinary connection analysis",
                    f"{core_topic.title()} & {secondary_topic.title()} Intersection",
                    "cross_domain_analogy",
                    f"Connects {core_topic} directly to {secondary_topic}, investigating their shared structural principles while staying strictly grounded in the user's stated curiosity."
                ))
            else:
                candidate_fallback.append((
                    f"{core_topic} adjacent systems analysis",
                    "Adjacent Interdisciplinary Analysis (Direct Connection)",
                    "cross_domain_analogy",
                    f"Stays close to {core_topic} with an adjacent systems-level perspective, honestly maintaining direct relevance."
                ))
            candidate_fallback.append((
                f"{core_topic} funniest moments comedic commentary comedy",
                f"Comedic {core_topic.title()} Moments",
                "entertainment_comedy",
                f"Entertaining and humorous takes on {core_topic} without sensationalist clickbait."
            ))
            candidate_fallback.append((
                f"{secondary_topic} applied methodology case study",
                "Practitioner Deep-Dive",
                "practitioner_deep_dive",
                f"Examines hands-on applied techniques, field practice, and concrete methodology in {secondary_topic}."
            ))
        else:
            # 1. First principles
            candidate_fallback.append((
                f"{core_topic} theoretical models structural principles",
                "Foundational Principles",
                "first_principles",
                f"Deconstructs the underlying mechanisms and foundational theories of {core_topic}."
            ))

            # 2. Archive lecture
            candidate_fallback.append((
                f"{core_topic} pioneer masterclass lecture symposium archive",
                "Archival Masterclasses",
                "archive_lecture",
                f"Surfaces foundational talks, archival demonstrations, and pioneer masterclasses in {core_topic}."
            ))

            # 3. Cross domain
            if preferences.active_category:
                candidate_fallback.append((
                    f"{core_topic} advanced concepts practical breakdown analysis",
                    f"{core_topic.title()} Advanced Analysis",
                    "cross_domain_analogy",
                    f"In-depth analysis and advanced breakdown of core mechanisms in {core_topic}."
                ))
            elif secondary_topic != core_topic and len(secondary_topic) > 2:
                candidate_fallback.append((
                    f"{core_topic} {secondary_topic} interdisciplinary connection analysis",
                    f"{core_topic.title()} & {secondary_topic.title()} Intersection",
                    "cross_domain_analogy",
                    f"Connects {core_topic} directly to {secondary_topic}, investigating their shared structural principles while staying strictly grounded in the user's stated curiosity."
                ))
            else:
                candidate_fallback.append((
                    f"{core_topic} adjacent systems analysis",
                    "Adjacent Interdisciplinary Analysis (Direct Connection)",
                    "cross_domain_analogy",
                    f"Stays close to {core_topic} with an adjacent systems-level perspective, honestly maintaining direct relevance."
                ))

            # 4. Niche History (omitted if 'history' or historical framing is avoided)
            candidate_fallback.append((
                f"{core_topic} historical evolution controversies documentary",
                "Historical Origins",
                "niche_history",
                f"Explores historical controversies, evolutionary shifts, and forgotten developments of {core_topic}."
            ))

            # 5. Practitioner deep dive
            candidate_fallback.append((
                f"{secondary_topic} applied methodology case study",
                "Practitioner Deep-Dive",
                "practitioner_deep_dive",
                f"Examines hands-on applied techniques, field practice, and concrete methodology in {secondary_topic}."
            ))

        # Only add candidates that are completely safe across query, domain, and rationale
        fallback_safe_queries: List[CuriosityQuery] = []
        for q_str, dom, q_type, rat in candidate_fallback:
            if (self.is_text_avoided(q_str, avoided_terms)
                    or self.is_text_avoided(dom, avoided_terms)
                    or self.is_text_avoided(rat, avoided_terms)):
                # Do NOT relabel or force a fake query to fill slots! Omit direction!
                continue
            fallback_safe_queries.append(CuriosityQuery(
                query=q_str, domain=dom, query_type=q_type, rationale=rat
            ))

        return fallback_safe_queries[:count]
