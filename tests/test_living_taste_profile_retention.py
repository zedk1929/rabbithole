import json
import subprocess
from pathlib import Path
from rabbithole_app.models import LivingTasteProfile, TasteSignal


def test_python_living_taste_profile_retains_imported_affinities():
    # 1. Simulate imported YouTube interests seeded directly
    profile = LivingTasteProfile(
        subject_affinities={
            "Astrophysics": 2.0,
            "Quantum Computing": 2.0,
        },
        subject_evidence_counts={
            "Astrophysics": 2,
            "Quantum Computing": 2,
        },
        recent_signals=[
            TasteSignal(
                video_id="vid_wood_1",
                title="Woodworking Chisel Care",
                domain="Woodworking",
                qualities=["artisan craft"],
                action="substantial_watch",
                weight=1.0,
            )
        ],
    )

    # 2. Trigger recalculation
    profile.recalculate_profile()

    # 3. Verify imported subjects are retained and combined with ongoing activity
    assert "Astrophysics" in profile.subject_affinities
    assert profile.subject_affinities["Astrophysics"] == 2.0
    assert "Quantum Computing" in profile.subject_affinities
    assert profile.subject_affinities["Quantum Computing"] == 2.0
    assert "Woodworking" in profile.subject_affinities
    assert profile.subject_affinities["Woodworking"] == 1.0

    # 4. Add another interaction on an imported domain
    profile.recent_signals.insert(
        0,
        TasteSignal(
            video_id="vid_astro_1",
            title="Black Hole Imaging",
            domain="Astrophysics",
            qualities=["visual explanation"],
            action="completed",
            weight=2.5,
        ),
    )
    profile.recalculate_profile()

    # Astrophysics affinity should increase to 4.5
    assert profile.subject_affinities["Astrophysics"] == 4.5
    assert profile.subject_evidence_counts["Astrophysics"] == 3


def test_js_recalculate_living_taste_profile_retention():
    app_js_path = Path(__file__).resolve().parent.parent / "src" / "rabbithole_app" / "static" / "app.js"
    assert app_js_path.exists()

    js_test_code = f"""
    const fs = require('fs');
    const appJs = fs.readFileSync({json.dumps(str(app_js_path))}, 'utf8');

    // Extract recalculateLivingTasteProfile
    const start = appJs.indexOf('function recalculateLivingTasteProfile(profile)');
    const end = appJs.indexOf('function applyPassiveDecayToProfile');
    const fnCode = appJs.substring(start, end);
    eval(fnCode);

    // Initial state: YouTube import seeded subject_affinities directly
    const profile = {{
      subject_affinities: {{
        'Astrophysics': 2.0,
        'Quantum Computing': 2.0
      }},
      subject_evidence_counts: {{
        'Astrophysics': 2,
        'Quantum Computing': 2
      }},
      recent_signals: [
        {{
          video_id: 'wood_1',
          title: 'Hand Plane Restoration',
          domain: 'Woodworking',
          qualities: ['artisan craft'],
          action: 'substantial_watch',
          weight: 1.0,
          timestamp: new Date().toISOString()
        }}
      ]
    }};

    recalculateLivingTasteProfile(profile);

    // Assert imported subjects are NOT wiped
    if (!profile.subject_affinities['Astrophysics']) throw new Error('Astrophysics was wiped!');
    if (!profile.subject_affinities['Quantum Computing']) throw new Error('Quantum Computing was wiped!');
    if (!profile.subject_affinities['Woodworking']) throw new Error('Woodworking was not added!');
    if (profile.subject_affinities['Astrophysics'] !== 2.0) throw new Error('Astrophysics affinity incorrect');
    if (profile.subject_affinities['Woodworking'] !== 1.0) throw new Error('Woodworking affinity incorrect');

    // Next interaction on an imported domain
    profile.recent_signals.unshift({{
      video_id: 'astro_1',
      title: 'Neutron Stars',
      domain: 'Astrophysics',
      qualities: ['visual explanation'],
      action: 'completed',
      weight: 2.5,
      timestamp: new Date().toISOString()
    }});

    recalculateLivingTasteProfile(profile);

    if (profile.subject_affinities['Astrophysics'] !== 4.5) throw new Error('Astrophysics did not combine ongoing activity properly: ' + profile.subject_affinities['Astrophysics']);
    if (profile.subject_evidence_counts['Astrophysics'] !== 3) throw new Error('Astrophysics evidence count incorrect');

    console.log('SUCCESS');
    """

    res = subprocess.run(["node", "-e", js_test_code], capture_output=True, text=True)
    assert res.returncode == 0, f"Node test failed: {res.stderr}\n{res.stdout}"
    assert "SUCCESS" in res.stdout
