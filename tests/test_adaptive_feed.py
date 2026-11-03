"""Regression checks for preference learning, recency, and impression cooldowns."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from rabbithole_app.models import CandidateVideo, UserPreferences, LivingTasteProfile, TasteSignal
from rabbithole_app.filter_engine import FilterEngine, upload_recency_boost


def test_seen_video_returns_after_cooldown_but_dislike_does_not():
    video = CandidateVideo(id='fixture_video', title='Baseball pitching mechanics', channel='Pitch Lab', url='http://fixture', duration_seconds=900)
    engine = FilterEngine(api_key='')
    prefs = UserPreferences(interests='baseball', seen_video_ids=[video.id])
    assert engine.filter_and_rank([video], prefs, enable_enrichment=False)[0]
    assert not engine.filter_and_rank([video], prefs.model_copy(update={'recent_shown_video_ids':[video.id]}), enable_enrichment=False)[0]
    assert not engine.filter_and_rank([video], prefs.model_copy(update={'disliked_video_ids':[video.id]}), enable_enrichment=False)[0]


def test_recency_uses_upload_date_not_title_year():
    prefs = UserPreferences(interests='math', recency_preference='recent')
    video = CandidateVideo(id='x', title='Best math 2026', channel='Math', url='http://fixture')
    assert upload_recency_boost(video, prefs) == 0
    video.upload_date = (datetime.now(timezone.utc)-timedelta(days=7)).strftime('%Y%m%d')
    fresh = upload_recency_boost(video,prefs)
    video.upload_date = '20100101'
    assert fresh > upload_recency_boost(video,prefs)


def test_imported_subjects_survive_python_recalculation():
    profile = LivingTasteProfile(imported_subject_affinities={'Music':2}, imported_subject_evidence_counts={'Music':2})
    profile.record_signal(TasteSignal(video_id='x', domain='Math', action='substantial_watch'))
    assert profile.subject_affinities['Music'] == 2
    assert profile.subject_evidence_counts['Music'] == 2


def test_browser_watch_identity_imports_and_idempotence():
    app = Path(__file__).parents[1] / 'src/rabbithole_app/static/app.js'
    script = r'''
const fs=require('fs'),vm=require('vm'); const s=fs.readFileSync(process.argv[1],'utf8');
function fn(name){const start=s.indexOf('function '+name+'(');return s.slice(start,s.indexOf('\nfunction ',start+1));}
let profile={subject_affinities:{Music:2},subject_evidence_counts:{Music:2},recent_signals:[]};
const c={getLivingTasteProfile:()=>profile,saveLivingTasteProfile:p=>profile=p,updateTasteProfileBadge:()=>{},extractVideoQualities:()=>[]};vm.createContext(c);vm.runInContext(fn('recalculateLivingTasteProfile')+'\n'+fn('recordTasteAction'),c);
for(const video_id of ['a','b','b']) c.recordTasteAction({video_id,title:'Video '+video_id,channel:'Creator',source_domain:'Search label'},'substantial_watch',{actual_subject:'Math'});
console.log(JSON.stringify(profile));
'''
    result = subprocess.run(['node','-e',script,str(app)], capture_output=True,text=True,check=True)
    profile=json.loads(result.stdout)
    assert profile['subject_evidence_counts']['Math']==2
    assert profile['subject_affinities']['Music']==2
    assert len(profile['recent_signals'])==2
    assert {x['video_id'] for x in profile['recent_signals']}=={'a','b'}
