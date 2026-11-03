// Rabbithole Frontend Client — Non-Predatory Discovery Homepage with Real Viewing History & Resume Playback

let currentTrail = [];
let currentPreferences = null;

const PREFS_STORAGE_KEY = "rabbithole_taste_preferences";
const DRAFT_STORAGE_KEY = "rabbithole_taste_draft";
const FEED_STORAGE_KEY = "rabbithole_current_feed";
const SEEN_STORAGE_KEY = "rabbithole_seen_video_ids";
const HISTORY_STORAGE_KEY = "rabbithole_viewing_history";
const WATCH_LATER_STORAGE_KEY = "rabbithole_watch_later";
const CATEGORIES_STORAGE_KEY = "rabbithole_interest_categories";
const ACTIVE_CATEGORY_STORAGE_KEY = "rabbithole_active_category";
const EMERGING_STORAGE_KEY = "rabbithole_emerging_interests";
const DISMISSED_CATEGORIES_STORAGE_KEY = "rabbithole_dismissed_categories";
const FOLLOWED_CREATORS_STORAGE_KEY = "rabbithole_followed_creators";
const TASTE_PROFILE_STORAGE_KEY = "rabbithole_living_taste_profile";
const CATEGORY_VIEWS_STORAGE_KEY = "rabbithole_category_views";

// In-memory feed cache per tab and refresh indices
let tabFeedsCache = {};
let perTabRefreshIndex = readLocalJSON("rabbithole_refresh_indices") || {};
let perCategoryView = readLocalJSON(CATEGORY_VIEWS_STORAGE_KEY) || {};
let activeEditingCategory = null;
let latestFeedRequestId = 0;

function getActiveCategoryView(categoryName) {
  if (!categoryName) return "new";
  return perCategoryView[categoryName] || "new";
}

function setActiveCategoryView(categoryName, view) {
  if (!categoryName) return;
  perCategoryView[categoryName] = view;
  saveLocalJSON(CATEGORY_VIEWS_STORAGE_KEY, perCategoryView);
}

function getTabCacheKey(categoryNameOrNull, view) {
  if (!categoryNameOrNull) return "main";
  const currentView = view || getActiveCategoryView(categoryNameOrNull);
  return `${categoryNameOrNull}::${currentView}`;
}

// Curiosity Archetypes & YouTube State
let selectedArchetypes = new Set();
let stagedYouTubeResult = null;

const ARCHETYPE_INFO = {
  mechanist: { icon: "⚙️", label: "The Mechanist", desc: "Physical logic & first principles" },
  detective: { icon: "🕵️", label: "Archival Detective", desc: "Historical & technical mysteries" },
  visual_synthesizer: { icon: "📐", label: "Visual Synthesizer", desc: "Spatial intuition & diagrams" },
  craft_purist: { icon: "🪵", label: "Craft Purist", desc: "Mastery & workshop flow" },
  game_theorist: { icon: "♟️", label: "Strategic Theorist", desc: "Incentives & asymmetric exploits" },
  playful_deconstructor: { icon: "🎭", label: "Playful Deconstructor", desc: "Clever wit & inventive builds" }
};

// YouTube Player state
let ytPlayer = null;
let isYTAPIReady = false;

// --- Server-Side State Persistence & Multi-Device Sync ---
let _syncDebounceTimer = null;
let _pendingSyncState = {};

function scheduleServerSync(partialState, immediate = false) {
  _pendingSyncState = { ..._pendingSyncState, ...partialState };
  if (_syncDebounceTimer) clearTimeout(_syncDebounceTimer);
  const doSync = async () => {
    try {
      const payload = { ..._pendingSyncState };
      _pendingSyncState = {};
      if (Object.keys(payload).length === 0) return;
      await fetch("/api/user/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      console.warn("Background user state sync failed:", e);
    }
  };
  if (immediate) {
    doSync();
  } else {
    _syncDebounceTimer = setTimeout(doSync, 600);
  }
}

async function syncUserStateWithServer() {
  try {
    const res = await fetch("/api/user/state");
    if (!res.ok) return;
    const data = await res.json();
    if (data.has_state && data.state) {
      const s = data.state;
      // 1. Watch Later
      if (Array.isArray(s.watch_later) && s.watch_later.length > 0) {
        const local = getWatchLaterRecords();
        if (local.length === 0) {
          localStorage.setItem(WATCH_LATER_STORAGE_KEY, JSON.stringify(s.watch_later));
          updateWatchLaterNavBadge();
        } else {
          const existingIds = new Set(local.map(r => r.video_id));
          let changed = false;
          for (const item of s.watch_later) {
            if (!existingIds.has(item.video_id)) {
              local.push(item);
              changed = true;
            }
          }
          if (changed) {
            localStorage.setItem(WATCH_LATER_STORAGE_KEY, JSON.stringify(local));
            updateWatchLaterNavBadge();
          }
        }
      }
      // 2. Taste Profile
      if (s.living_taste_profile) {
        const localProfRaw = localStorage.getItem(TASTE_PROFILE_STORAGE_KEY);
        if (!localProfRaw || localProfRaw === "{}" || Object.keys(JSON.parse(localProfRaw).subject_affinities || {}).length === 0) {
          localStorage.setItem(TASTE_PROFILE_STORAGE_KEY, JSON.stringify(s.living_taste_profile));
          updateTasteProfileBadge();
        }
      }
      // 3. Followed Creators
      if (Array.isArray(s.followed_creators) && s.followed_creators.length > 0) {
        const localFollows = getFollowedCreators();
        if (localFollows.length === 0) {
          localStorage.setItem(FOLLOWED_CREATORS_STORAGE_KEY, JSON.stringify(s.followed_creators));
          updateFollowedCreatorsNavBadge();
        }
      }
      // 4. Categories
      if (Array.isArray(s.categories) && s.categories.length > 0) {
        const localCats = getCategories();
        if (localCats.length === 0) {
          localStorage.setItem(CATEGORIES_STORAGE_KEY, JSON.stringify(s.categories));
          renderCategoryTabs();
        }
      }
      // 5. Preferences
      if (s.preferences) {
        const localPrefsRaw = localStorage.getItem(PREFS_STORAGE_KEY);
        let shouldSet = false;
        if (!localPrefsRaw || localPrefsRaw === "{}") {
          shouldSet = true;
        } else {
          try {
            const p = JSON.parse(localPrefsRaw);
            if (!p.interests && (!p.curiosity_archetypes || p.curiosity_archetypes.length === 0)) {
              shouldSet = true;
            }
          } catch (_) {
            shouldSet = true;
          }
        }
        if (shouldSet) {
          localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(s.preferences));
        }
      }
    } else {
      // Server is empty! Upload existing local storage data to back it up on server
      const localWatchLater = getWatchLaterRecords();
      const localProf = getLivingTasteProfile();
      const localFollows = getFollowedCreators();
      const localCats = getCategories();
      const localPrefs = getSavedPreferences();
      const payload = {};
      if (localWatchLater.length > 0) payload.watch_later = localWatchLater;
      if (localProf && Object.keys(localProf.subject_affinities || {}).length > 0) payload.living_taste_profile = localProf;
      if (localFollows.length > 0) payload.followed_creators = localFollows;
      if (localCats.length > 0) payload.categories = localCats;
      if (localPrefs) payload.preferences = localPrefs;
      if (Object.keys(payload).length > 0) {
        scheduleServerSync(payload, true);
      }
    }
  } catch (err) {
    console.warn("User state sync error:", err);
  }
}
let playbackTrackerInterval = null;
let activeVideoRecord = null;
let activeVideoAssessment = null;
let lastTickTimestamp = null;
let currentHistoryFilter = "all";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function safeThumbnail(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:") return url.href;
  } catch (_) { /* use fallback */ }
  return "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800";
}

const serendipityDescriptions = {
  1: "1 (Deep Dive) — Rigorous focus on core subject without lateral branching.",
  2: "2 (Specialized) — Foundational principles and closely related techniques.",
  3: "3 (Balanced) — Explores direct topics plus intriguing adjacent tangents.",
  4: "4 (Cross-Disciplinary) — Bridges to other fields through analogy and history.",
  5: "5 (Radical Serendipity) — Unexpected, high-entropy conceptual leaps."
};

// Global YouTube IFrame API Ready Callback
window.onYouTubeIframeAPIReady = function() {
  isYTAPIReady = true;
};

// --- Local Storage Management ---

function getSeenVideoIds() {
  try {
    const raw = localStorage.getItem(SEEN_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveSeenVideoIds(ids) {
  try {
    localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify(ids));
  } catch (_) {}
}

function getSavedPreferences() {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function savePreferencesLocally(prefs) {
  try {
    localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
    scheduleServerSync({ preferences: prefs });
    return true;
  } catch (_) {
    return false;
  }
}

function readLocalJSON(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function saveLocalJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (_) {
    return false;
  }
}

// --- Living Taste Profile Management ---

function getLivingTasteProfile() {
  const defaultProfile = {
    imported_subject_affinities: {},
    imported_subject_evidence_counts: {},
    subject_affinities: {},
    subject_evidence_counts: {},
    preferred_qualities: {
      "visual explanation": 0,
      "storytelling": 0,
      "technical depth": 0,
      "artisan craft": 0,
      "humor": 0,
      "unusual connections": 0
    },
    preferred_entertainment_formats: {},
    followed_creators: [],
    disliked_patterns: [],
    recent_signals: [],
    last_updated_at: new Date().toISOString()
  };
  try {
    const raw = localStorage.getItem(TASTE_PROFILE_STORAGE_KEY);
    if (!raw) return defaultProfile;
    const parsed = JSON.parse(raw);
    return {
      ...defaultProfile,
      ...parsed,
      imported_subject_affinities: {
        ...(defaultProfile.imported_subject_affinities || {}),
        ...(parsed.imported_subject_affinities || {})
      },
      imported_subject_evidence_counts: {
        ...(defaultProfile.imported_subject_evidence_counts || {}),
        ...(parsed.imported_subject_evidence_counts || {})
      },
      preferred_qualities: {
        ...defaultProfile.preferred_qualities,
        ...(parsed.preferred_qualities || {})
      },
      preferred_entertainment_formats: {
        ...defaultProfile.preferred_entertainment_formats,
        ...(parsed.preferred_entertainment_formats || {})
      }
    };
  } catch (_) {
    return defaultProfile;
  }
}

function saveLivingTasteProfile(profile) {
  try {
    profile.last_updated_at = new Date().toISOString();
    tabFeedsCache = {};
    localStorage.removeItem(FEED_STORAGE_KEY);
    localStorage.setItem(TASTE_PROFILE_STORAGE_KEY, JSON.stringify(profile));
    updateTasteProfileBadge();
    scheduleServerSync({ living_taste_profile: profile });
    return true;
  } catch (_) {
    return false;
  }
}

function resetLivingTasteProfile() {
  try {
    localStorage.removeItem(TASTE_PROFILE_STORAGE_KEY);
    updateTasteProfileBadge();
    renderTasteProfileModal();
    return true;
  } catch (_) {
    return false;
  }
}

function updateTasteProfileBadge() {
  const badge = document.getElementById("tasteProfileNavBadge");
  if (!badge) return;
  const profile = getLivingTasteProfile();
  const count = (profile.recent_signals || []).length;
  badge.textContent = count;
  if (count > 0) {
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function extractVideoQualities(vid, ev = null) {
  const text = `${vid.title || ""} ${vid.description || ""} ${vid.source_domain || ""} ${ev?.actual_subject || ""} ${ev?.content_format || ""}`.toLowerCase();
  const qualities = [];

  if (ev?.entertainment_format) {
    qualities.push(ev.entertainment_format);
  }

  if (/visual|geometry|animation|diagram|graphics|animated|infographic|spatial|render|3d|chart/i.test(text)) {
    qualities.push("visual explanation");
  }
  if (/story|narrative|lore|tales|chronicle|history|drama|documentary|epic|adventure/i.test(text)) {
    qualities.push("storytelling");
  }
  if (/mechanics|physics|mathematics|engineering|analysis|microscopic|principles|algorithm|theory|scientific|chemistry/i.test(text)) {
    qualities.push("technical depth");
  }
  if (/craft|artisan|woodworking|handmade|handcrafted|restoration|workshop|tool|making|joinery|clockmaking|bookbinding/i.test(text)) {
    qualities.push("artisan craft");
  }
  if (/funny|humor|comedy|blunder|satire|comedic|bizarre|hilarious|parody|wit/i.test(text)) {
    qualities.push("humor");
  }
  if (/cross|bridge|intersection|analogy|unexpected|lateral|interdisciplinary|metamaterials|biomimicry|quasicrystal/i.test(text) || vid.source_query_type === "cross_domain_analogy" || vid.discovery_type) {
    qualities.push("unusual connections");
  }

  return qualities;
}

function recalculateLivingTasteProfile(profile) {
  if (!profile) return;

  // 1. Ensure imported evidence containers are initialized
  if (!profile.imported_subject_affinities || typeof profile.imported_subject_affinities !== "object") {
    profile.imported_subject_affinities = {};
  }
  if (!profile.imported_subject_evidence_counts || typeof profile.imported_subject_evidence_counts !== "object") {
    profile.imported_subject_evidence_counts = {};
  }

  // 2. Retain any existing subject affinities that don't have explicit imported tracking yet.
  // When an import seeds subject affinities directly into subject_affinities, preserve those
  // baseline subjects into imported_subject_affinities so they survive subsequent interactions.
  if (profile.subject_affinities && typeof profile.subject_affinities === "object") {
    const signalDomains = new Set((profile.recent_signals || []).map(s => s.domain).filter(Boolean));
    for (const [dom, score] of Object.entries(profile.subject_affinities)) {
      if (profile.imported_subject_affinities[dom] === undefined) {
        if (!signalDomains.has(dom)) {
          profile.imported_subject_affinities[dom] = score;
          const cnt = profile.subject_evidence_counts?.[dom];
          profile.imported_subject_evidence_counts[dom] = (cnt !== undefined && cnt !== null) ? cnt : 2;
        }
      }
    }
  }

  // 3. Start baseline affinities and evidence counts from retained imported evidence
  profile.subject_affinities = { ...(profile.imported_subject_affinities || {}) };
  profile.subject_evidence_counts = { ...(profile.imported_subject_evidence_counts || {}) };
  profile.preferred_qualities = {};
  profile.preferred_entertainment_formats = {};
  const vidsPerDomain = {};

  const signals = profile.recent_signals || [];

  const entFormatAliases = {
    "comedy": "comedy",
    "humor": "comedy",
    "gameplay": "gameplay",
    "challenges": "challenges",
    "challenge": "challenges",
    "sports moments": "sports_moments",
    "sports_moments": "sports_moments",
    "sports": "sports_moments",
    "performance": "performance",
    "live performance": "performance",
    "music": "performance",
    "animation": "animation",
    "creative projects": "creative_projects",
    "creative_projects": "creative_projects",
    "artisan craft": "creative_projects",
    "craft": "creative_projects",
    "compelling story": "compelling_story",
    "compelling_story": "compelling_story",
    "storytelling": "compelling_story",
    "lore": "compelling_story"
  };

  // 4. Combine with ongoing activity from recent_signals in chronological order (oldest to newest)
  [...signals].reverse().forEach((s, idx) => {
    const ageDays = Math.max(0, (Date.now() - Date.parse(s.timestamp || new Date().toISOString())) / 86400000);
    const effectiveWeight = s.weight * (s.action === "substantial_watch" ? Math.pow(0.5, ageDays / 30) : 1);

    if (s.domain) {
      const dom = s.domain;
      if (s.action === "disliked") {
        profile.subject_affinities[dom] = Math.max(0, (profile.subject_affinities[dom] || 0) - Math.abs(effectiveWeight));
      } else {
        profile.subject_affinities[dom] = Math.round(((profile.subject_affinities[dom] || 0) + effectiveWeight) * 10) / 10;
        if (!vidsPerDomain[dom]) vidsPerDomain[dom] = new Set();
        vidsPerDomain[dom].add(s.video_id || s.title || `vid_${idx}`);
      }
    }

    if (s.action !== "disliked" && s.qualities) {
      for (const q of s.qualities) {
        profile.preferred_qualities[q] = Math.round(((profile.preferred_qualities[q] || 0) + effectiveWeight * 0.8) * 10) / 10;
        const normFmt = entFormatAliases[q.toLowerCase()];
        if (normFmt) {
          profile.preferred_entertainment_formats[normFmt] = Math.round(
            ((profile.preferred_entertainment_formats[normFmt] || 0) + effectiveWeight * 0.8) * 10
          ) / 10;
        }
      }
    }
  });

  // 5. Combine interaction evidence counts with imported counts
  for (const [dom, vids] of Object.entries(vidsPerDomain)) {
    profile.subject_evidence_counts[dom] = (profile.subject_evidence_counts[dom] || 0) + vids.size;
  }
}

function applyPassiveDecayToProfile(profile, decayFactor = 0.85) {
  if (!profile.recent_signals) return;
  profile.recent_signals.forEach(s => {
    if (s.action === "substantial_watch") {
      s.weight = Math.round(s.weight * decayFactor * 100) / 100;
    }
  });
  recalculateLivingTasteProfile(profile);
  saveLivingTasteProfile(profile);
}

function removeTasteSignal(videoId, action = null) {
  const profile = getLivingTasteProfile();
  if (!profile.recent_signals) return;
  profile.recent_signals = profile.recent_signals.filter(s => {
    if (videoId && (s.video_id === videoId || s.channel === videoId)) {
      if (!action || s.action === action) return false;
    }
    if (!videoId && action && s.action === action) return false;
    return true;
  });
  recalculateLivingTasteProfile(profile);
  saveLivingTasteProfile(profile);
  updateTasteProfileBadge();
  renderTasteProfileModal();
}

function recordTasteAction(vid, action, ev = null, reason = null) {
  if (!vid || (!vid.id && !vid.video_id && !vid.title)) return;
  const profile = getLivingTasteProfile();

  recalculateLivingTasteProfile(profile);

  // Explicit actions carry higher weights than passive watch time
  const weights = {
    watch_later: 3.0,
    followed: 3.5,
    completed: 2.5,
    substantial_watch: 1.0,
    disliked: -2.0
  };
  const weight = weights[action] ?? 1.0;

  const qualities = extractVideoQualities(vid, ev);
  const domain = ev?.actual_subject || vid.source_domain || ev?.source_domain || null;
  const channel = vid.channel || null;
  const vidId = vid.id || vid.video_id || (vid.channel ? `creator_${vid.channel}` : "unknown_vid");

  const signal = {
    video_id: vidId,
    title: vid.title || "Untitled",
    channel: channel,
    domain: domain,
    qualities: qualities,
    action: action,
    weight: weight,
    timestamp: new Date().toISOString()
  };

  profile.recent_signals = profile.recent_signals || [];
  profile.recent_signals = profile.recent_signals.filter(s => !(s.video_id === vidId && s.action === action));
  profile.recent_signals.unshift(signal);
  if (profile.recent_signals.length > 300) {
    profile.recent_signals = profile.recent_signals.slice(0, 300);
  }

  // Followed creators
  if (action === "followed" && channel) {
    profile.followed_creators = profile.followed_creators || [];
    if (!profile.followed_creators.includes(channel)) {
      profile.followed_creators.push(channel);
    }
  }

  // Disliked patterns
  if (action === "disliked" && reason) {
    profile.disliked_patterns = profile.disliked_patterns || [];
    if (!profile.disliked_patterns.includes(reason)) {
      profile.disliked_patterns.push(reason);
    }
  }

  recalculateLivingTasteProfile(profile);
  saveLivingTasteProfile(profile);
  updateTasteProfileBadge();
}

function openTasteProfileModal() {
  renderTasteProfileModal();
  document.getElementById("tasteProfileModal").classList.remove("hidden");
}

function closeTasteProfileModal() {
  document.getElementById("tasteProfileModal").classList.add("hidden");
}

function renderTasteProfileModal() {
  const profile = getLivingTasteProfile();

  // 1. Established Subjects (>= 2 distinct interactions)
  const establishedList = document.getElementById("establishedSubjectsList");
  const establishedEmpty = document.getElementById("establishedEmptyMsg");
  const establishedCountElem = document.getElementById("establishedSubjectsCount");

  const counts = profile.subject_evidence_counts || {};
  const affinities = profile.subject_affinities || {};

  const established = Object.entries(counts)
    .filter(([_, cnt]) => cnt >= 2)
    .sort((a, b) => (affinities[b[0]] || 0) - (affinities[a[0]] || 0));

  if (establishedCountElem) establishedCountElem.textContent = `${established.length} subjects`;
  if (establishedList) establishedList.innerHTML = "";
  if (established.length > 0) {
    if (establishedEmpty) establishedEmpty.classList.add("hidden");
    established.forEach(([sub, cnt]) => {
      const pill = document.createElement("div");
      pill.className = "px-3 py-1.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 text-xs flex items-center gap-2";
      pill.innerHTML = `
        <span class="font-medium">${escapeHtml(sub)}</span>
        <span class="px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-emerald-400 font-mono text-[10px]" title="${cnt} interactions, affinity score ${(affinities[sub] || 0).toFixed(1)}">
          ${cnt} actions • score ${(affinities[sub] || 0).toFixed(1)}
        </span>
      `;
      if (establishedList) establishedList.appendChild(pill);
    });
  } else {
    if (establishedEmpty) establishedEmpty.classList.remove("hidden");
  }

  // 2. Tentative / Emerging Subjects (1 interaction)
  const tentativeList = document.getElementById("tentativeSubjectsList");
  const tentativeEmpty = document.getElementById("tentativeEmptyMsg");
  const tentative = Object.entries(counts).filter(([_, cnt]) => cnt === 1);

  if (tentativeList) tentativeList.innerHTML = "";
  if (tentative.length > 0) {
    if (tentativeEmpty) tentativeEmpty.classList.add("hidden");
    tentative.forEach(([sub, cnt]) => {
      const pill = document.createElement("div");
      pill.className = "px-2.5 py-1 rounded-lg border border-surface-border bg-surface text-zinc-300 text-[11px] flex items-center gap-1.5";
      pill.innerHTML = `
        <span>${escapeHtml(sub)}</span>
        <span class="text-surface-muted text-[10px]">1 action</span>
      `;
      if (tentativeList) tentativeList.appendChild(pill);
    });
  } else {
    if (tentativeEmpty) tentativeEmpty.classList.remove("hidden");
  }

  // 3. Preferred Qualities
  const qualitiesContainer = document.getElementById("qualitiesContainer");
  if (qualitiesContainer) {
    qualitiesContainer.innerHTML = "";
    const qualities = profile.preferred_qualities || {};
    const maxQualVal = Math.max(1, ...Object.values(qualities));

    Object.entries(qualities).forEach(([qName, qScore]) => {
      const row = document.createElement("div");
      row.className = "p-2.5 rounded-xl bg-surface border border-surface-border/60 space-y-1";
      const pct = Math.min(100, Math.round((qScore / maxQualVal) * 100));
      row.innerHTML = `
        <div class="flex items-center justify-between text-[11px]">
          <span class="capitalize font-medium text-zinc-200">${escapeHtml(qName)}</span>
          <span class="font-mono text-cyan-400 text-[10px]">${qScore.toFixed(1)} pts</span>
        </div>
        <div class="h-1.5 rounded-full bg-surface-border overflow-hidden">
          <div class="h-full bg-gradient-to-r from-emerald-500 to-cyan-400 rounded-full" style="width: ${pct}%"></div>
        </div>
      `;
      qualitiesContainer.appendChild(row);
    });
  }

  // 3b. Preferred Entertainment Formats
  const entFormatsList = document.getElementById("profileEntertainmentFormatsList");
  const entFormatsCount = document.getElementById("profileEntertainmentFormatsCount");
  if (entFormatsList) {
    entFormatsList.innerHTML = "";
    const entFormats = profile.preferred_entertainment_formats || {};
    const entries = Object.entries(entFormats).sort((a, b) => b[1] - a[1]);
    if (entFormatsCount) entFormatsCount.textContent = `${entries.length} format${entries.length === 1 ? '' : 's'}`;
    if (entries.length > 0) {
      entries.forEach(([fmt, score]) => {
        const item = document.createElement("div");
        item.className = "px-2.5 py-1 rounded-lg bg-pink-500/10 border border-pink-500/30 text-pink-300 text-xs flex items-center gap-1.5";
        item.innerHTML = `
          <span class="font-medium capitalize">${escapeHtml(fmt.replace(/_/g, ' '))}</span>
          <span class="font-mono text-[10px] text-pink-400">score ${score.toFixed(1)}</span>
        `;
        entFormatsList.appendChild(item);
      });
    } else {
      entFormatsList.innerHTML = `<span class="text-surface-muted text-[11px] italic">No entertainment format affinities recorded yet. Watch or save entertainment videos to build your profile.</span>`;
    }
  }

  // 4. Followed Creators
  const creatorsList = document.getElementById("profileFollowedCreatorsList");
  if (creatorsList) {
    creatorsList.innerHTML = "";
    const creators = profile.followed_creators || [];
    if (creators.length > 0) {
      creators.forEach(c => {
        const item = document.createElement("div");
        item.className = "px-2.5 py-1 rounded-lg bg-surface border border-purple-500/20 text-purple-300 text-[11px]";
        item.textContent = c;
        creatorsList.appendChild(item);
      });
    } else {
      creatorsList.innerHTML = `<span class="text-surface-muted text-[11px] italic">No creators followed yet.</span>`;
    }
  }

  // 5. Dislike Patterns
  const dislikedList = document.getElementById("profileDislikedPatternsList");
  if (dislikedList) {
    dislikedList.innerHTML = "";
    const patterns = profile.disliked_patterns || [];
    if (patterns.length > 0) {
      patterns.forEach(p => {
        const item = document.createElement("div");
        item.className = "px-2.5 py-1 rounded-lg bg-surface border border-red-500/20 text-red-300 text-[11px]";
        item.textContent = p;
        dislikedList.appendChild(item);
      });
    } else {
      dislikedList.innerHTML = `<span class="text-surface-muted text-[11px] italic">No dislike patterns recorded.</span>`;
    }
  }

  // 6. Recent Signals Log
  const signalsLog = document.getElementById("tasteSignalsLog");
  if (signalsLog) {
    signalsLog.innerHTML = "";
    const signals = profile.recent_signals || [];
    if (signals.length > 0) {
      signals.forEach(s => {
        const item = document.createElement("div");
        item.className = "flex items-center justify-between py-1 border-b border-surface-border/30 last:border-0";
        const iconMap = {
          watch_later: "🔖",
          followed: "👤",
          completed: "✓",
          substantial_watch: "⏱",
          disliked: "👎"
        };
        const icon = iconMap[s.action] || "•";
        const weightSign = s.weight > 0 ? `+${s.weight.toFixed(1)}` : `${s.weight.toFixed(1)}`;
        const weightColor = s.weight > 0 ? "text-emerald-400" : "text-red-400";
        const titleSnippet = s.title.length > 35 ? s.title.slice(0, 35) + "..." : s.title;

        item.innerHTML = `
          <div class="flex items-center gap-2 truncate">
            <span>${icon}</span>
            <span class="text-zinc-300 truncate">${escapeHtml(titleSnippet)}</span>
            ${s.domain ? `<span class="text-surface-muted text-[10px]">(${escapeHtml(s.domain)})</span>` : ''}
          </div>
          <span class="${weightColor} font-mono text-[10px] shrink-0">${weightSign}</span>
        `;
        signalsLog.appendChild(item);
      });
    } else {
      signalsLog.innerHTML = `<div class="text-center py-4 text-surface-muted text-[11px] italic">No taste signals recorded yet. Explore and interact with videos to start learning!</div>`;
    }
  }
}

function updateArchetypeCardsUI() {
  document.querySelectorAll("#archetypesGrid .archetype-card").forEach(card => {
    const arch = card.dataset.archetype;
    if (selectedArchetypes.has(arch)) {
      card.classList.add("selected");
    } else {
      card.classList.remove("selected");
    }
  });
}

function collectTasteDraft() {
  const form = document.getElementById("discoveryForm");
  return {
    interests: document.getElementById("interestsInput").value,
    curiosity_archetypes: Array.from(selectedArchetypes),
    curiosity_statement: document.getElementById("interestsInput").value.trim(),
    experience_type: form.querySelector('input[name="experienceType"]:checked')?.value || "mix",
    recency_preference: form.querySelector('input[name="recencyPref"]:checked')?.value || "any",
    serendipity_level: Number(document.getElementById("serendipitySlider").value),
    avoid_topics: document.getElementById("avoidInput").value,
    familiar_topics: document.getElementById("familiarInput").value,
    learning_goal: document.getElementById("goalInput").value,
    depth_level: document.getElementById("depthSelect").value,
    avoid_sensationalism: document.getElementById("avoidClickbaitCheckbox").checked,
  };
}

function restoreTasteForm(prefs) {
  if (!prefs) return;
  const form = document.getElementById("discoveryForm");
  document.getElementById("interestsInput").value = prefs.curiosity_statement || prefs.interests || "";
  for (const [name, value] of [["experienceType", prefs.experience_type], ["recencyPref", prefs.recency_preference]]) {
    const radio = Array.from(form.querySelectorAll(`input[name="${name}"]`)).find(input => input.value === value);
    if (radio) radio.checked = true;
  }
  const slider = document.getElementById("serendipitySlider");
  slider.value = prefs.serendipity_level || 3;
  slider.dispatchEvent(new Event("input"));
  document.getElementById("avoidInput").value = prefs.avoid_topics || "";
  document.getElementById("familiarInput").value = prefs.familiar_topics || "";
  document.getElementById("goalInput").value = prefs.learning_goal || "";
  document.getElementById("depthSelect").value = prefs.depth_level || "curious_explorer";
  document.getElementById("avoidClickbaitCheckbox").checked = prefs.avoid_sensationalism !== false;

  // Restore archetype card selections
  selectedArchetypes.clear();
  const archetypes = prefs.curiosity_archetypes || [];
  archetypes.forEach(a => selectedArchetypes.add(a));
  updateArchetypeCardsUI();
}

function saveTasteDraft() {
  const status = document.getElementById("tasteSaveStatus");
  status.textContent = saveLocalJSON(DRAFT_STORAGE_KEY, collectTasteDraft())
    ? "Draft saved on this device"
    : "This browser could not save your draft";
}

function feedCacheKey(prefs) {
  return JSON.stringify({
    interests: prefs.interests,
    curiosity_archetypes: (prefs.curiosity_archetypes || []).slice().sort(),
    active_category: prefs.active_category || null,
    category_view: prefs.active_category ? (prefs.category_view || "new") : null,
    experience_type: prefs.experience_type,
    feed_mode: prefs.feed_mode,
    recency_preference: prefs.recency_preference,
    serendipity_level: prefs.serendipity_level,
    avoid_topics: prefs.avoid_topics,
    familiar_topics: prefs.familiar_topics,
    learning_goal: prefs.learning_goal,
    depth_level: prefs.depth_level,
    avoid_sensationalism: prefs.avoid_sensationalism,
    refresh_index: prefs.refresh_index,
    seen_video_ids: prefs.seen_video_ids,
  });
}

function getViewingHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

function saveViewingHistory(historyObj) {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(historyObj));
  } catch (_) {}
}

function getViewingRecord(videoId) {
  const history = getViewingHistory();
  return history[videoId] || null;
}

function updateViewingRecord(record) {
  if (!record || !record.video_id) return;
  const history = getViewingHistory();
  history[record.video_id] = {
    ...record,
    last_watched_timestamp: new Date().toISOString()
  };
  saveViewingHistory(history);
  renderContinueWatching();
  updateHistoryBadge();
}

function removeViewingRecord(videoId) {
  const history = getViewingHistory();
  delete history[videoId];
  saveViewingHistory(history);
  renderContinueWatching();
  renderViewingHistoryUI();
  updateHistoryBadge();
}

function clearAllViewingHistory() {
  saveViewingHistory({});
  renderContinueWatching();
  renderViewingHistoryUI();
  updateHistoryBadge();
}

// --- Watch Later Management ---

function getWatchLaterRecords() {
  try {
    const raw = localStorage.getItem(WATCH_LATER_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveWatchLaterRecords(records) {
  try {
    localStorage.setItem(WATCH_LATER_STORAGE_KEY, JSON.stringify(records));
    updateWatchLaterNavBadge();
    scheduleServerSync({ watch_later: records });
  } catch (_) {}
}

function isWatchLater(videoId) {
  return getWatchLaterRecords().some(r => r.video_id === videoId);
}

function toggleWatchLater(item) {
  const vid = item.video || item;
  const ev = item.assessment || null;
  const records = getWatchLaterRecords();
  const existingIdx = records.findIndex(r => r.video_id === vid.id);

  if (existingIdx >= 0) {
    records.splice(existingIdx, 1);
    saveWatchLaterRecords(records);
    removeTasteSignal(vid.id, "watch_later");
    return false;
  } else {
    const rec = {
      video_id: vid.id,
      title: vid.title,
      channel: vid.channel,
      thumbnail: vid.thumbnail || null,
      duration_seconds: vid.duration_seconds || null,
      recommendation_context: ev ? (ev.connection_note || ev.relevance_reason || "") : (item.discovery_connection || ""),
      source_domain: vid.source_domain || null,
      source_category: item.category || null,
      is_discovery: !!item.is_discovery,
      discovery_connection: item.discovery_connection || null,
      saved_at: new Date().toISOString()
    };
    records.unshift(rec);
    saveWatchLaterRecords(records);
    recordTasteAction(vid, "watch_later", ev);

    if (item.is_discovery) {
      handleDiscoveryInteraction(item, "saved");
    }
    return true;
  }
}

function removeWatchLater(videoId) {
  const records = getWatchLaterRecords().filter(r => r.video_id !== videoId);
  saveWatchLaterRecords(records);
  removeTasteSignal(videoId, "watch_later");
  renderWatchLaterUI();
}

function clearAllWatchLater() {
  saveWatchLaterRecords([]);
  removeTasteSignal(null, "watch_later");
  renderWatchLaterUI();
}

function updateWatchLaterNavBadge() {
  const badge = document.getElementById("watchLaterNavBadge");
  if (!badge) return;
  const count = getWatchLaterRecords().length;
  if (count > 0) {
    badge.textContent = count;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function renderWatchLaterUI() {
  const container = document.getElementById("watchLaterListContainer");
  const emptyNotice = document.getElementById("watchLaterEmptyNotice");
  if (!container || !emptyNotice) return;

  const records = getWatchLaterRecords();
  container.innerHTML = "";

  if (records.length === 0) {
    emptyNotice.classList.remove("hidden");
    container.classList.add("hidden");
    return;
  }

  emptyNotice.classList.add("hidden");
  container.classList.remove("hidden");

  records.forEach((rec) => {
    const item = document.createElement("div");
    item.className = "flex items-start justify-between p-3.5 rounded-xl bg-surface border border-surface-border/60 gap-4";

    const durText = rec.duration_seconds ? formatDuration(rec.duration_seconds) : "Video";
    const savedDate = rec.saved_at ? new Date(rec.saved_at).toLocaleDateString() : "";
    const discoveryBadge = rec.is_discovery
      ? `<span class="px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30 text-[10px] font-bold">✨ Discovery</span>`
      : "";

    item.innerHTML = `
      <div class="flex items-start space-x-3 truncate">
        <div class="relative w-24 h-14 bg-black rounded-lg overflow-hidden border border-surface-border shrink-0 cursor-pointer wl-play">
          <img
            src="${escapeHtml(safeThumbnail(rec.thumbnail))}"
            alt="${escapeHtml(rec.title)}"
            class="w-full h-full object-cover"
          >
          <div class="absolute inset-0 bg-black/40 flex items-center justify-center hover:bg-black/20">
            <span class="text-xs text-white">▶</span>
          </div>
          <span class="absolute bottom-1 right-1 px-1 py-0.2 rounded bg-black/80 font-mono text-[9px] text-zinc-300">
            ${durText}
          </span>
        </div>
        <div class="space-y-1 truncate">
          <h4 class="text-xs font-semibold text-white truncate cursor-pointer wl-play hover:text-emerald-300" title="${escapeHtml(rec.title)}">
            ${escapeHtml(rec.title)}
          </h4>
          <div class="flex items-center space-x-2 text-[11px] text-surface-muted">
            <span class="text-emerald-400 font-medium">${escapeHtml(rec.channel)}</span>
            <span>•</span>
            <span>Saved ${savedDate}</span>
            ${discoveryBadge}
          </div>
          ${rec.recommendation_context ? `
            <p class="text-[10px] text-surface-muted truncate max-w-lg">Context: ${escapeHtml(rec.recommendation_context)}</p>
          ` : ""}
        </div>
      </div>
      <div class="flex items-center space-x-2 shrink-0">
        <button
          type="button"
          class="wl-remove-btn px-2.5 py-1 text-xs text-surface-muted hover:text-red-400 border border-surface-border hover:border-red-500/40 rounded-lg transition-colors cursor-pointer"
          title="Remove from Watch Later"
        >
          Remove
        </button>
        <button
          type="button"
          class="wl-play-btn px-3 py-1 bg-emerald-500 hover:bg-emerald-400 text-black font-semibold rounded-lg text-xs transition-colors cursor-pointer"
        >
          Play
        </button>
      </div>
    `;

    const playAction = () => {
      document.getElementById("watchLaterModal").classList.add("hidden");
      openModal({
        id: rec.video_id,
        title: rec.title,
        channel: rec.channel,
        thumbnail: rec.thumbnail,
        duration_seconds: rec.duration_seconds,
        source_domain: rec.source_domain,
      }, {
        connection_note: rec.recommendation_context || "Saved in Watch Later",
        relevance_reason: rec.recommendation_context || "",
        is_discovery: rec.is_discovery,
        matched_concept: rec.source_domain,
      });
    };

    item.querySelectorAll(".wl-play").forEach(el => el.addEventListener("click", playAction));
    item.querySelector(".wl-play-btn").addEventListener("click", playAction);
    item.querySelector(".wl-remove-btn").addEventListener("click", () => {
      removeWatchLater(rec.video_id);
    });

    container.appendChild(item);
  });
}

// --- Followed Creators Management ---

function getFollowedCreators() {
  try {
    const raw = localStorage.getItem(FOLLOWED_CREATORS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveFollowedCreators(creators) {
  try {
    localStorage.setItem(FOLLOWED_CREATORS_STORAGE_KEY, JSON.stringify(creators));
    updateFollowedCreatorsNavBadge();
    updateCardFollowButtons();
    scheduleServerSync({ followed_creators: creators });
  } catch (_) {}
}

function matchesCreator(c, channelName, channelId) {
  if (!c) return false;
  if (channelId && c.channel_id) {
    return c.channel_id.trim() === channelId.trim();
  }
  const norm1 = (c.name || "").trim().toLowerCase();
  const norm2 = (channelName || "").trim().toLowerCase();
  if (norm1 && norm2) {
    return norm1 === norm2;
  }
  return false;
}

function isCreatorFollowed(channelName, channelId) {
  const list = getFollowedCreators();
  return list.some(c => matchesCreator(c, channelName, channelId));
}

function followCreator(channelName, channelId, thumbnail) {
  if (!channelName && !channelId) return;
  const list = getFollowedCreators();
  const existingIdx = list.findIndex(c => matchesCreator(c, channelName, channelId));

  if (existingIdx >= 0) {
    // Avoid duplicates: enrich with channel_id if newly available
    if (channelId && !list[existingIdx].channel_id) {
      list[existingIdx].channel_id = channelId;
      saveFollowedCreators(list);
    }
    return;
  }

  list.push({
    channel_id: channelId || null,
    name: (channelName || "Unknown Channel").trim(),
    followed_at: new Date().toISOString(),
    thumbnail: thumbnail || null
  });
  saveFollowedCreators(list);
  recordTasteAction({ id: channelId || channelName, title: channelName, channel: channelName }, "followed");
}

function unfollowCreator(channelName, channelId) {
  let list = getFollowedCreators();
  list = list.filter(c => !matchesCreator(c, channelName, channelId));
  saveFollowedCreators(list);

  const profile = getLivingTasteProfile();
  if (profile.followed_creators) {
    profile.followed_creators = profile.followed_creators.filter(c => c !== channelName);
    saveLivingTasteProfile(profile);
  }
  removeTasteSignal(channelId || channelName, "followed");
}

function toggleFollowCreator(channelName, channelId, thumbnail) {
  if (isCreatorFollowed(channelName, channelId)) {
    unfollowCreator(channelName, channelId);
    return false;
  } else {
    followCreator(channelName, channelId, thumbnail);
    return true;
  }
}

function updateFollowedCreatorsNavBadge() {
  const badge = document.getElementById("followedCreatorsNavBadge");
  if (!badge) return;
  const count = getFollowedCreators().length;
  badge.textContent = count;
  if (count > 0) {
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function updateCardFollowButtons() {
  document.querySelectorAll(".follow-creator-card-btn").forEach(btn => {
    const chName = btn.getAttribute("data-channel");
    const chId = btn.getAttribute("data-channel-id");
    const followed = isCreatorFollowed(chName, chId);
    const label = btn.querySelector(".follow-creator-label") || btn;
    if (followed) {
      btn.className = "follow-creator-card-btn px-2 py-0.5 rounded-full border text-[10px] font-medium transition-colors cursor-pointer shrink-0 border-purple-500/50 bg-purple-500/15 text-purple-300 hover:bg-purple-500/25";
      btn.title = "Following creator. Click to unfollow.";
      label.textContent = "Following ✓";
    } else {
      btn.className = "follow-creator-card-btn px-2 py-0.5 rounded-full border text-[10px] font-medium transition-colors cursor-pointer shrink-0 border-surface-border/80 text-surface-muted hover:text-white hover:border-purple-500/50 hover:bg-purple-500/10";
      btn.title = "Follow creator to tune recommendations to your taste.";
      label.textContent = "+ Follow";
    }
  });
}

function renderFollowedCreatorsModal() {
  const container = document.getElementById("followedCreatorsListContainer");
  const emptyNotice = document.getElementById("followedCreatorsEmptyNotice");
  const modalBadge = document.getElementById("followedCreatorsModalBadge");
  const summaryText = document.getElementById("followedCreatorsSummaryText");
  const clearBtn = document.getElementById("clearAllFollowedBtn");
  if (!container) return;

  const creators = getFollowedCreators();
  if (modalBadge) modalBadge.textContent = creators.length;
  if (summaryText) summaryText.textContent = `${creators.length} creator${creators.length === 1 ? '' : 's'} followed`;

  if (clearBtn) {
    if (creators.length > 0) clearBtn.classList.remove("hidden");
    else clearBtn.classList.add("hidden");
  }

  if (creators.length === 0) {
    container.innerHTML = "";
    if (emptyNotice) emptyNotice.classList.remove("hidden");
    return;
  }

  if (emptyNotice) emptyNotice.classList.add("hidden");
  container.innerHTML = "";

  creators.forEach((c) => {
    const card = document.createElement("div");
    card.className = "flex items-center justify-between p-3 rounded-xl bg-surface/60 border border-surface-border/80 hover:border-surface-border transition-colors";

    const idTag = c.channel_id
      ? `<span class="px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 font-mono text-[9px] border border-purple-500/20" title="Stable YouTube Channel ID: ${escapeHtml(c.channel_id)}">id: ${escapeHtml(c.channel_id)}</span>`
      : "";

    const dateStr = c.followed_at ? new Date(c.followed_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";

    card.innerHTML = `
      <div class="flex items-center space-x-3 min-w-0">
        <div class="w-8 h-8 rounded-full bg-purple-500/20 border border-purple-500/30 flex items-center justify-center text-purple-300 font-bold text-xs shrink-0">
          ${escapeHtml((c.name || "C")[0].toUpperCase())}
        </div>
        <div class="min-w-0 space-y-0.5">
          <div class="flex items-center space-x-2">
            <span class="font-semibold text-white text-xs truncate">${escapeHtml(c.name)}</span>
            ${idTag}
          </div>
          ${dateStr ? `<p class="text-[10px] text-surface-muted">Followed on ${escapeHtml(dateStr)}</p>` : ''}
        </div>
      </div>
      <button type="button" class="unfollow-btn px-2.5 py-1 rounded-lg border border-red-500/30 hover:border-red-500/60 bg-red-500/10 text-red-400 hover:text-red-300 text-xs font-medium transition-colors cursor-pointer shrink-0">
        Unfollow
      </button>
    `;

    card.querySelector(".unfollow-btn").addEventListener("click", () => {
      unfollowCreator(c.name, c.channel_id);
      renderFollowedCreatorsModal();
    });

    container.appendChild(card);
  });
}

// --- Categories and Emerging Interests Management ---

function getCategories() {
  try {
    const raw = localStorage.getItem(CATEGORIES_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveCategories(cats) {
  try {
    localStorage.setItem(CATEGORIES_STORAGE_KEY, JSON.stringify(cats));
    scheduleServerSync({ categories: cats });
  } catch (_) {}
}

function getDismissedCategories() {
  try {
    const raw = localStorage.getItem(DISMISSED_CATEGORIES_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveDismissedCategories(list) {
  try {
    localStorage.setItem(DISMISSED_CATEGORIES_STORAGE_KEY, JSON.stringify(list));
  } catch (_) {}
}

function getEmergingInterests() {
  try {
    const raw = localStorage.getItem(EMERGING_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveEmergingInterests(signals) {
  try {
    localStorage.setItem(EMERGING_STORAGE_KEY, JSON.stringify(signals));
  } catch (_) {}
}

function getActiveCategory() {
  return localStorage.getItem(ACTIVE_CATEGORY_STORAGE_KEY) || null;
}

function setActiveCategory(catNameOrNull) {
  if (catNameOrNull) {
    localStorage.setItem(ACTIVE_CATEGORY_STORAGE_KEY, catNameOrNull);
  } else {
    localStorage.removeItem(ACTIVE_CATEGORY_STORAGE_KEY);
  }
}

function splitInterestCategories(interestsStr) {
  if (!interestsStr) return [];
  const parts = interestsStr
    .split(/[,;]|\band\b(?=\s+\w+\s+\w+)/i)
    .map(p => p.trim())
    .filter(Boolean);
  return parts.length > 0 ? parts : [interestsStr.trim()];
}

function areTopicsCloselyRelated(topicA, topicB) {
  if (!topicA || !topicB) return false;
  const a = topicA.toLowerCase().trim();
  const b = topicB.toLowerCase().trim();
  if (a === b || a.includes(b) || b.includes(a)) return true;

  const stopWords = new Set(["and", "the", "for", "with", "from", "about", "into", "over", "tactics", "theory"]);
  const tokensA = a.match(/[a-z0-9]+/g)?.filter(w => w.length > 3 && !stopWords.has(w)) || [];
  const tokensB = b.match(/[a-z0-9]+/g)?.filter(w => w.length > 3 && !stopWords.has(w)) || [];
  if (tokensA.length === 0 || tokensB.length === 0) return false;

  const overlap = tokensA.filter(t => tokensB.includes(t));
  return overlap.length >= Math.min(tokensA.length, tokensB.length) * 0.5;
}

function extractTopicFromConnection(connectionNote) {
  if (!connectionNote) return null;
  const match = connectionNote.match(/(?:connect(?:ing|s)?\s+(?:your\s+taste\s+in\s+[^to]+to|to)\s+|in\s+['"]?)([^.'".,;\n]+)/i);
  return match ? match[1].trim() : null;
}

function handleDiscoveryInteraction(itemOrRecord, actionType) {
  const vid = itemOrRecord.video || itemOrRecord;
  const ev = itemOrRecord.assessment || null;
  const isDisc = !!(itemOrRecord.is_discovery || vid.is_discovery || itemOrRecord.source_query_type === "cross_domain_analogy");
  if (!isDisc) return;

  const videoId = vid.id || vid.video_id;
  const dislikedSet = new Set(getDislikedVideoIds());
  if (dislikedSet.has(videoId)) return;

  let topic = vid.source_domain || (ev && ev.matched_concept) || (itemOrRecord.discovery_connection ? extractTopicFromConnection(itemOrRecord.discovery_connection) : null);
  if (!topic || topic.length < 3) {
    topic = "Lateral Discovery";
  }

  // Dismissed check
  const dismissed = getDismissedCategories();
  if (dismissed.some(d => areTopicsCloselyRelated(d, topic))) {
    return;
  }

  // Already existing category check
  const categories = getCategories();
  if (categories.some(c => areTopicsCloselyRelated(c.name, topic))) {
    return;
  }

  const signals = getEmergingInterests();
  let signal = signals.find(s => areTopicsCloselyRelated(s.topic, topic));

  if (!signal) {
    signal = {
      topic: topic,
      source_discovery_domain: vid.source_domain || topic,
      distinct_video_ids: [videoId],
      saved_video_ids: actionType === "saved" ? [videoId] : [],
      watched_video_ids: actionType === "substantially_watched" ? [videoId] : [],
      positive_actions_count: 1,
      is_promoted: false,
      is_dismissed: false,
      first_detected_at: new Date().toISOString(),
      last_updated_at: new Date().toISOString()
    };
    signals.push(signal);
  } else {
    signal.last_updated_at = new Date().toISOString();
    if (!signal.distinct_video_ids.includes(videoId)) {
      signal.distinct_video_ids.push(videoId);
    }
    if (actionType === "saved" && !signal.saved_video_ids.includes(videoId)) {
      signal.saved_video_ids.push(videoId);
      signal.positive_actions_count += 1;
    }
    if (actionType === "substantially_watched" && !signal.watched_video_ids.includes(videoId)) {
      signal.watched_video_ids.push(videoId);
      signal.positive_actions_count += 1;
    }
  }

  const distinctCount = signal.distinct_video_ids.length;
  const hasSaveAndWatch = signal.saved_video_ids.some(id => signal.watched_video_ids.includes(id));

  // Strong evidence criteria:
  // 1) 2 distinct positive actions on distinct discovery videos
  // 2) saving and substantially watching one video (>= 60s)
  const shouldPromote = !signal.is_promoted && (
    (distinctCount >= 2 && signal.positive_actions_count >= 2) ||
    hasSaveAndWatch
  );

  if (shouldPromote) {
    signal.is_promoted = true;
    saveEmergingInterests(signals);
    promoteToCategory(signal.topic);
  } else {
    saveEmergingInterests(signals);
  }
}

function promoteToCategory(topicName) {
  const categories = getCategories();
  if (categories.some(c => areTopicsCloselyRelated(c.name, topicName))) return;
  const dismissed = getDismissedCategories();
  if (dismissed.some(d => areTopicsCloselyRelated(d, topicName))) return;

  const newCat = {
    id: "cat_" + Date.now(),
    name: topicName,
    is_auto_created: true,
    is_dismissed: false,
    created_at: new Date().toISOString()
  };
  categories.push(newCat);
  saveCategories(categories);

  const banner = document.getElementById("emergingPromotionBanner");
  const msg = document.getElementById("emergingPromotionMsg");
  if (banner && msg) {
    msg.textContent = `New interest tab created from your discovery actions: "${topicName}"!`;
    banner.classList.remove("hidden");
    const viewBtn = document.getElementById("viewPromotedTabBtn");
    if (viewBtn) {
      viewBtn.onclick = () => {
        banner.classList.add("hidden");
        selectCategoryTab(newCat.name);
      };
    }
  }
  renderHomepageTabs();
}

function renderHomepageTabs() {
  const tabsBar = document.getElementById("homepageTabsBar");
  const tabsSection = document.getElementById("homepageTabsSection");
  if (!tabsBar || !tabsSection) return;

  const categories = getCategories().filter(c => !c.is_dismissed);
  tabsSection.classList.remove("hidden");
  tabsBar.innerHTML = "";

  const activeCategory = getActiveCategory();

  // 1. "🌟 Main Feed" tab
  const mainTab = document.createElement("button");
  mainTab.type = "button";
  const isMainActive = !activeCategory;
  mainTab.className = `px-3.5 py-1.5 rounded-xl font-medium text-xs flex items-center gap-1.5 cursor-pointer whitespace-nowrap transition-all ${
    isMainActive
      ? "bg-emerald-500 text-black font-bold shadow-md shadow-emerald-500/20"
      : "bg-surface border border-surface-border text-surface-muted hover:text-white"
  }`;
  mainTab.innerHTML = `<span>🌟</span><span>Main Feed</span>`;
  mainTab.addEventListener("click", () => selectCategoryTab(null));
  tabsBar.appendChild(mainTab);

  // 2. Individual Category Tabs
  categories.forEach((cat) => {
    const isCatActive = activeCategory === cat.name;
    const tabWrap = document.createElement("div");
    tabWrap.className = `flex items-center rounded-xl border transition-all ${
      isCatActive
        ? "bg-emerald-500/15 border-emerald-500/50 text-emerald-400"
        : "bg-surface border-surface-border text-surface-muted hover:border-surface-muted hover:text-white"
    }`;

    const clickBtn = document.createElement("button");
    clickBtn.type = "button";
    clickBtn.className = "px-3 py-1.5 text-xs font-medium cursor-pointer whitespace-nowrap";
    clickBtn.innerHTML = `<span>${cat.is_auto_created ? '✨ ' : ''}${escapeHtml(cat.name)}</span>`;
    clickBtn.addEventListener("click", () => selectCategoryTab(cat.name));

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "px-1.5 py-1 text-[11px] text-surface-muted hover:text-white cursor-pointer transition-colors";
    editBtn.title = "Manage / Dismiss Category";
    editBtn.textContent = "⚙️";
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openCategoryModal(cat);
    });

    tabWrap.appendChild(clickBtn);
    tabWrap.appendChild(editBtn);
    tabsBar.appendChild(tabWrap);
  });

  renderCategoryViewsBar(activeCategory);
}

function renderCategoryViewsBar(activeCategory) {
  const bar = document.getElementById("categoryViewsBar");
  if (!bar) return;
  if (!activeCategory) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  const currentView = getActiveCategoryView(activeCategory);

  const views = ["new", "deeper", "connections"];
  views.forEach(v => {
    const btn = document.querySelector(`.category-view-btn[data-view="${v}"]`);
    if (!btn) return;
    if (v === currentView) {
      btn.className = "category-view-btn px-3 py-1 rounded-full text-xs font-medium transition-all bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 cursor-pointer shadow-sm hover:border-emerald-400";
    } else {
      btn.className = "category-view-btn px-3 py-1 rounded-full text-xs font-medium transition-all bg-surface/60 text-surface-muted border border-surface-border cursor-pointer hover:text-surface-text hover:border-surface-border/80";
    }
  });
}

function selectCategoryTab(categoryNameOrNull) {
  // Invalidate a response still being generated for the previous tab.
  latestFeedRequestId += 1;
  setActiveCategory(categoryNameOrNull);
  const view = categoryNameOrNull ? getActiveCategoryView(categoryNameOrNull) : null;
  if (currentPreferences) {
    currentPreferences.active_category = categoryNameOrNull;
    currentPreferences.category_view = view;
  }
  renderHomepageTabs();

  const tabKey = getTabCacheKey(categoryNameOrNull, view);
  if (tabFeedsCache[tabKey] && tabFeedsCache[tabKey].items && tabFeedsCache[tabKey].items.length > 0) {
    document.getElementById("loadingState").classList.add("hidden");
    document.getElementById("cardsGrid").classList.remove("opacity-50", "pointer-events-none");
    displayFeed(tabFeedsCache[tabKey], {
      ...(currentPreferences || {}),
      active_category: categoryNameOrNull,
      category_view: view
    });
  } else if (currentPreferences) {
    currentPreferences.active_category = categoryNameOrNull;
    currentPreferences.category_view = view;
    currentPreferences.refresh_index = perTabRefreshIndex[tabKey] || 0;
    fetchFeed(currentPreferences);
  }
}

function onSelectCategoryView(view) {
  const activeCat = getActiveCategory();
  if (!activeCat) return;
  setActiveCategoryView(activeCat, view);
  renderCategoryViewsBar(activeCat);
  const tabKey = getTabCacheKey(activeCat, view);

  if (currentPreferences) {
    currentPreferences.active_category = activeCat;
    currentPreferences.category_view = view;
  }

  if (tabFeedsCache[tabKey] && tabFeedsCache[tabKey].items && tabFeedsCache[tabKey].items.length > 0) {
    document.getElementById("loadingState").classList.add("hidden");
    document.getElementById("cardsGrid").classList.remove("opacity-50", "pointer-events-none");
    displayFeed(tabFeedsCache[tabKey], {
      ...(currentPreferences || {}),
      active_category: activeCat,
      category_view: view
    });
  } else if (currentPreferences) {
    currentPreferences.refresh_index = perTabRefreshIndex[tabKey] || 0;
    fetchFeed(currentPreferences);
  }
}

function openCategoryModal(categoryOrNull) {
  activeEditingCategory = categoryOrNull;
  const modal = document.getElementById("categoryModal");
  const title = document.getElementById("categoryModalTitle");
  const input = document.getElementById("categoryNameInput");
  const dismissBtn = document.getElementById("dismissCategoryBtn");
  const hint = document.getElementById("categoryModalHint");

  if (categoryOrNull) {
    title.textContent = "Manage Category";
    input.value = categoryOrNull.name;
    dismissBtn.classList.remove("hidden");
    dismissBtn.textContent = "Dismiss Category";
    hint.textContent = "Dismissing will remove this tab and prevent it from immediately reappearing.";
  } else {
    title.textContent = "Add Interest Category";
    input.value = "";
    dismissBtn.classList.add("hidden");
    hint.textContent = "Adds a new focused tab to your discovery homepage.";
  }

  modal.classList.remove("hidden");
  input.focus();
}

function closeCategoryModal() {
  document.getElementById("categoryModal").classList.add("hidden");
  activeEditingCategory = null;
}

const DISLIKED_STORAGE_KEY = "rabbithole_disliked_videos";
let activeDislikeVideoId = null;
let lastDislikedState = null;
let undoToastTimeout = null;

function getDislikedRecords() {
  try {
    const raw = localStorage.getItem(DISLIKED_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveDislikedRecords(records) {
  try {
    localStorage.setItem(DISLIKED_STORAGE_KEY, JSON.stringify(records));
    updateDislikedNavBadge();
  } catch (_) {}
}

function getDislikedVideoIds() {
  return getDislikedRecords().map(r => r.video_id);
}

function recordDislike(vid, ev, reason = null) {
  const records = getDislikedRecords();
  const existingIdx = records.findIndex(r => r.video_id === vid.id);
  const rec = {
    video_id: vid.id,
    title: vid.title,
    channel: vid.channel,
    thumbnail: vid.thumbnail || null,
    recommendation_context: ev ? (ev.connection_note || ev.relevance_reason || "") : "",
    chosen_reason: reason,
    source_domain: vid.source_domain || null,
    date: new Date().toISOString()
  };
  if (existingIdx >= 0) {
    records[existingIdx] = rec;
  } else {
    records.unshift(rec);
  }
  saveDislikedRecords(records);
  return rec;
}

function updateDislikeReason(videoId, reason) {
  const records = getDislikedRecords();
  const rec = records.find(r => r.video_id === videoId);
  if (rec) {
    rec.chosen_reason = reason;
    saveDislikedRecords(records);
    recordTasteAction({ id: videoId, title: rec.title, channel: rec.channel, source_domain: rec.recommendation_domain }, "disliked", null, reason);
  }
}

function removeDislike(videoId) {
  const records = getDislikedRecords().filter(r => r.video_id !== videoId);
  saveDislikedRecords(records);
  updateDislikedNavBadge();
}

function clearAllDislikes() {
  saveDislikedRecords([]);
  updateDislikedNavBadge();
}

function updateDislikedNavBadge() {
  const badge = document.getElementById("dislikedNavBadge");
  if (!badge) return;
  const count = getDislikedRecords().length;
  if (count > 0) {
    badge.textContent = count;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function openDislikeReasonModal(vid) {
  activeDislikeVideoId = vid.id;
  document.getElementById("dislikeReasonVideoTitle").textContent = vid.title;
  document.getElementById("dislikeReasonVideoChannel").textContent = vid.channel;
  document.getElementById("dislikeReasonModal").classList.remove("hidden");
}

function closeDislikeReasonModal() {
  document.getElementById("dislikeReasonModal").classList.add("hidden");
  activeDislikeVideoId = null;
}

function showUndoToast(vid) {
  const toast = document.getElementById("undoToast");
  const toastText = document.getElementById("undoToastText");
  if (!toast || !toastText) return;

  if (undoToastTimeout) {
    clearTimeout(undoToastTimeout);
  }

  const titleSnippet = vid.title.length > 30 ? vid.title.slice(0, 30) + "..." : vid.title;
  toastText.textContent = `Removed "${titleSnippet}" from feed`;
  toast.classList.remove("hidden");

  undoToastTimeout = setTimeout(() => {
    toast.classList.add("hidden");
  }, 8000);
}

function handleDislike(item, card, index) {
  const vid = item.video;
  const ev = item.assessment;

  // 1. Record dislike locally without erasing playback history
  recordDislike(vid, ev, null);
  recordTasteAction(vid, "disliked", ev, null);

  // 2. Save state for Undo option
  const parentNode = card.parentNode;
  const nextSibling = card.nextSibling;
  lastDislikedState = {
    item,
    card,
    parentNode,
    nextSibling,
    index,
  };

  // 3. Immediately remove card from current feed
  card.remove();

  // 4. Update cached feed so refresh or reload will not display it
  const cachedFeed = readLocalJSON(FEED_STORAGE_KEY);
  if (cachedFeed && cachedFeed.data && Array.isArray(cachedFeed.data.items)) {
    cachedFeed.data.items = cachedFeed.data.items.filter(it => it.video.id !== vid.id);
    saveLocalJSON(FEED_STORAGE_KEY, cachedFeed);
  }

  // 5. If feed empty, show empty notice
  const grid = document.getElementById("cardsGrid");
  const empty = document.getElementById("feedEmptyNotice");
  if (grid && grid.children.length === 0 && empty) {
    empty.classList.remove("hidden");
    empty.querySelector("h3").textContent = "Feed Cleared";
    empty.querySelector("p").textContent = "You've removed videos from this feed. Click 'Refresh feed' for fresh discoveries.";
  }

  // 6. Open optional reason prompt with quick choices
  openDislikeReasonModal(vid);

  // 7. Show Undo toast
  showUndoToast(vid);
}

function handleUndoDislike() {
  if (!lastDislikedState) return;

  const { item, card, parentNode, nextSibling } = lastDislikedState;
  const vid = item.video;

  // 1. Remove from disliked records (restores eligibility)
  removeDislike(vid.id);

  // 2. Re-insert card into feed at previous position
  if (parentNode) {
    if (nextSibling && parentNode.contains(nextSibling)) {
      parentNode.insertBefore(card, nextSibling);
    } else {
      parentNode.appendChild(card);
    }
  }

  // 3. Re-add to cached feed if present
  const cachedFeed = readLocalJSON(FEED_STORAGE_KEY);
  if (cachedFeed && cachedFeed.data && Array.isArray(cachedFeed.data.items)) {
    if (!cachedFeed.data.items.some(it => it.video.id === vid.id)) {
      cachedFeed.data.items.splice(lastDislikedState.index, 0, item);
      saveLocalJSON(FEED_STORAGE_KEY, cachedFeed);
    }
  }

  // 4. Hide empty notice if visible
  const empty = document.getElementById("feedEmptyNotice");
  if (empty) {
    empty.classList.add("hidden");
  }

  // 5. Close reason modal if it belongs to this video
  if (activeDislikeVideoId === vid.id) {
    closeDislikeReasonModal();
  }

  // 6. Dismiss toast
  const toast = document.getElementById("undoToast");
  if (toast) toast.classList.add("hidden");
  if (undoToastTimeout) clearTimeout(undoToastTimeout);

  lastDislikedState = null;
}

function renderDislikedList() {
  const container = document.getElementById("dislikedListContainer");
  const emptyNotice = document.getElementById("dislikedEmptyNotice");
  if (!container || !emptyNotice) return;

  const records = getDislikedRecords();
  container.innerHTML = "";

  if (records.length === 0) {
    emptyNotice.classList.remove("hidden");
    container.classList.add("hidden");
    return;
  }

  emptyNotice.classList.add("hidden");
  container.classList.remove("hidden");

  records.forEach((rec) => {
    const item = document.createElement("div");
    item.className = "flex items-start justify-between p-3.5 rounded-xl bg-surface border border-surface-border/60 gap-4";

    const dateStr = rec.date ? new Date(rec.date).toLocaleDateString() : "";
    const reasonText = rec.chosen_reason || "No reason given";

    item.innerHTML = `
      <div class="flex items-start space-x-3 truncate">
        <img
          src="${escapeHtml(safeThumbnail(rec.thumbnail))}"
          alt="${escapeHtml(rec.title)}"
          class="w-24 h-14 object-cover rounded-lg bg-black border border-surface-border shrink-0"
        >
        <div class="space-y-1 truncate">
          <h4 class="text-xs font-semibold text-white truncate" title="${escapeHtml(rec.title)}">
            ${escapeHtml(rec.title)}
          </h4>
          <div class="flex items-center space-x-2 text-[11px] text-surface-muted">
            <span class="text-emerald-400 font-medium">${escapeHtml(rec.channel)}</span>
            <span>•</span>
            <span class="px-2 py-0.5 rounded bg-surface-raised border border-surface-border text-zinc-300 font-mono text-[10px]">
              ${escapeHtml(reasonText)}
            </span>
            ${dateStr ? `<span>•</span><span>${escapeHtml(dateStr)}</span>` : ""}
          </div>
          ${rec.recommendation_context ? `
            <p class="text-[10px] text-surface-muted truncate max-w-lg">Context: ${escapeHtml(rec.recommendation_context)}</p>
          ` : ""}
        </div>
      </div>
      <button
        type="button"
        class="un-dislike-btn px-2.5 py-1 text-xs text-zinc-300 hover:text-white border border-surface-border hover:border-emerald-500/50 rounded-lg shrink-0 transition-colors cursor-pointer"
        title="Un-dislike this video to allow it in future feeds"
      >
        Restore
      </button>
    `;

    const unDislikeBtn = item.querySelector(".un-dislike-btn");
    unDislikeBtn.addEventListener("click", () => {
      removeDislike(rec.video_id);
      renderDislikedList();
    });

    container.appendChild(item);
  });
}

// Extract viewing signals to enhance discovery without overwhelming user choices
function extractViewingSignals() {
  const history = getViewingHistory();
  const records = Object.values(history);

  const completed_video_ids = [];
  const watched_channels = {};
  const watched_topics_set = new Set();

  for (const r of records) {
    if (r.status === "completed") {
      completed_video_ids.push(r.video_id);
    }
    // Only count channel watch time if user actually spent >= 30 seconds playing it
    if (r.channel && r.watch_time_seconds >= 30) {
      watched_channels[r.channel] = (watched_channels[r.channel] || 0) + r.watch_time_seconds;
    }
    // Topics with substantive engagement (>= 60s watch time)
    if (r.source_domain && r.watch_time_seconds >= 60) {
      watched_topics_set.add(r.source_domain);
    }
  }

  return {
    completed_video_ids,
    watched_channels,
    watched_topics: Array.from(watched_topics_set),
  };
}

// --- Curiosity Archetypes & YouTube Session Integration ---

function initArchetypesUI() {
  document.querySelectorAll("#archetypesGrid .archetype-card").forEach(card => {
    card.addEventListener("click", () => {
      const arch = card.dataset.archetype;
      if (!arch) return;
      if (selectedArchetypes.has(arch)) {
        selectedArchetypes.delete(arch);
      } else {
        selectedArchetypes.add(arch);
      }
      updateArchetypeCardsUI();
      saveTasteDraft();
    });
  });
}

function openYouTubeModal() {
  const modal = document.getElementById("youtubeModal");
  if (modal) {
    modal.classList.remove("hidden");
    const status = document.getElementById("youtubeSyncStatus");
    if (status) status.classList.add("hidden");
  }
}

function closeYouTubeModal() {
  const modal = document.getElementById("youtubeModal");
  if (modal) modal.classList.add("hidden");
}

window.openYouTubeModal = openYouTubeModal;
window.closeYouTubeModal = closeYouTubeModal;

function initYouTubeModal() {
  const modal = document.getElementById("youtubeModal");
  if (!modal) return;

  const openFromSetupBtn = document.getElementById("openYouTubeSyncFromSetupBtn");
  if (openFromSetupBtn) openFromSetupBtn.addEventListener("click", openYouTubeModal);

  const openFromNavBtn = document.getElementById("youtubeSyncNavBtn");
  if (openFromNavBtn) openFromNavBtn.addEventListener("click", openYouTubeModal);

  const openFromTasteBarBtn = document.getElementById("openYouTubeFromTasteBarBtn");
  if (openFromTasteBarBtn) openFromTasteBarBtn.addEventListener("click", openYouTubeModal);

  const closeBtn = document.getElementById("closeYouTubeModalBtn");
  if (closeBtn) closeBtn.addEventListener("click", closeYouTubeModal);

  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeYouTubeModal();
  });

  // Tab switching
  const tabBrowserSync = document.getElementById("tabBrowserSync");
  const tabFileImport = document.getElementById("tabFileImport");
  const browserSyncSection = document.getElementById("browserSyncSection");
  const fileImportSection = document.getElementById("fileImportSection");

  if (tabBrowserSync && tabFileImport && browserSyncSection && fileImportSection) {
    tabBrowserSync.addEventListener("click", () => {
      tabBrowserSync.className = "pb-2 font-semibold text-emerald-400 border-b-2 border-emerald-400 cursor-pointer";
      tabFileImport.className = "pb-2 font-semibold text-surface-muted hover:text-white cursor-pointer";
      browserSyncSection.classList.remove("hidden");
      fileImportSection.classList.add("hidden");
    });

    tabFileImport.addEventListener("click", () => {
      tabFileImport.className = "pb-2 font-semibold text-emerald-400 border-b-2 border-emerald-400 cursor-pointer";
      tabBrowserSync.className = "pb-2 font-semibold text-surface-muted hover:text-white cursor-pointer";
      fileImportSection.classList.remove("hidden");
      browserSyncSection.classList.add("hidden");
    });
  }

  // Browser Session Sync Action
  const startBrowserSyncBtn = document.getElementById("startBrowserSyncBtn");
  const browserSelect = document.getElementById("youtubeBrowserSelect");
  const statusEl = document.getElementById("youtubeSyncStatus");
  const resultCard = document.getElementById("youtubeResultCard");

  if (startBrowserSyncBtn && browserSelect && statusEl && resultCard) {
    startBrowserSyncBtn.addEventListener("click", async () => {
      const browser = browserSelect.value;
      startBrowserSyncBtn.disabled = true;
      startBrowserSyncBtn.classList.add("opacity-50", "pointer-events-none");
      resultCard.classList.add("hidden");
      statusEl.className = "p-3 rounded-xl border border-surface-border bg-surface text-xs flex items-center gap-2 text-zinc-300";
      statusEl.innerHTML = `<span class="animate-spin inline-block">⚡</span> <span>Accessing ${escapeHtml(browser)} YouTube session and analyzing your subscriptions with Gemini...</span>`;
      statusEl.classList.remove("hidden");

      try {
        const res = await fetch("/api/youtube/sync-browser", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ browser, preferences: currentPreferences })
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || data.error || "Browser synchronization failed");
        }
        displayYouTubeSynthesisResult(data);
        statusEl.className = "p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-xs flex items-center gap-2 text-emerald-300";
        statusEl.innerHTML = `<span>✓</span> <span>Found ${data.channels_analyzed} channels. Inferred curiosity profile ready below!</span>`;
      } catch (err) {
        statusEl.className = "p-3 rounded-xl border border-amber-500/30 bg-amber-500/10 text-xs flex flex-col gap-2.5 text-amber-200";
        statusEl.innerHTML = `
          <div class="flex items-start gap-2">
            <span class="text-base shrink-0">🔒</span>
            <div>
              <strong class="text-amber-100 font-semibold block">macOS Privacy Protection Active</strong>
              <p class="text-[11px] text-amber-200/90 mt-0.5 leading-relaxed">
                macOS sandboxing prevents external apps from accessing Chrome's private cookie database directly.
                You can easily import your channels in seconds using the tab below!
              </p>
            </div>
          </div>
          <div>
            <button id="switchToFileImportBtn" type="button" class="px-3.5 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-black font-semibold rounded-lg text-xs flex items-center gap-1.5 cursor-pointer">
              <span>📁 Switch to Channel List Import →</span>
            </button>
          </div>
        `;
        const switchBtn = document.getElementById("switchToFileImportBtn");
        if (switchBtn) {
          switchBtn.addEventListener("click", () => {
            if (tabFileImport) tabFileImport.click();
          });
        }
      } finally {
        startBrowserSyncBtn.disabled = false;
        startBrowserSyncBtn.classList.remove("opacity-50", "pointer-events-none");
      }
    });
  }

  // File / Text Import Action
  const startFileImportBtn = document.getElementById("startFileImportBtn");
  const fileInput = document.getElementById("youtubeFileInput");
  const textInput = document.getElementById("youtubeImportInput");

  if (fileInput && textInput) {
    fileInput.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (file) {
        const reader = new FileReader();
        reader.onload = (re) => {
          textInput.value = re.target.result;
        };
        reader.readAsText(file);
      }
    });

    // Preset inspiration chips
    document.querySelectorAll(".channel-preset-chip").forEach(chip => {
      chip.addEventListener("click", () => {
        if (textInput && chip.dataset.channels) {
          textInput.value = chip.dataset.channels;
          textInput.focus();
        }
      });
    });
  }

  if (startFileImportBtn && textInput && statusEl && resultCard) {
    startFileImportBtn.addEventListener("click", async () => {
      const content = textInput.value.trim();
      if (!content) {
        alert("Please upload a subscriptions CSV or paste channel names/links into the box.");
        return;
      }

      startFileImportBtn.disabled = true;
      startFileImportBtn.classList.add("opacity-50", "pointer-events-none");
      resultCard.classList.add("hidden");
      statusEl.className = "p-3 rounded-xl border border-surface-border bg-surface text-xs flex items-center gap-2 text-zinc-300";
      statusEl.innerHTML = `<span class="animate-spin inline-block">✨</span> <span>Parsing channels and synthesizing your curiosity archetypes with Gemini...</span>`;
      statusEl.classList.remove("hidden");

      try {
        const filename = fileInput?.files?.[0]?.name || "import.txt";
        const res = await fetch("/api/youtube/import-subscriptions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content, filename, preferences: currentPreferences })
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || "Import failed");
        }
        displayYouTubeSynthesisResult(data);
        statusEl.className = "p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-xs flex items-center gap-2 text-emerald-300";
        statusEl.innerHTML = `<span>✓</span> <span>Analyzed ${data.channels_analyzed} channels. Inferred curiosity profile ready below!</span>`;
      } catch (err) {
        statusEl.className = "p-3 rounded-xl border border-red-500/30 bg-red-500/10 text-xs flex items-center gap-2 text-red-300";
        statusEl.innerHTML = `<span>⚠️</span> <span>${escapeHtml(err.message)}</span>`;
      } finally {
        startFileImportBtn.disabled = false;
        startFileImportBtn.classList.remove("opacity-50", "pointer-events-none");
      }
    });
  }

  // Apply YouTube Taste Button
  const applyBtn = document.getElementById("applyYouTubeTasteBtn");
  if (applyBtn) {
    applyBtn.addEventListener("click", async () => {
      if (!stagedYouTubeResult) return;

      currentPreferences = {
        ...(currentPreferences || {}),
        ...stagedYouTubeResult
      };

      savePreferencesLocally(currentPreferences);
      selectedArchetypes.clear();
      (currentPreferences.curiosity_archetypes || []).forEach(a => selectedArchetypes.add(a));
      updateArchetypeCardsUI();

      if (currentPreferences.curiosity_statement) {
        currentPreferences.interests = currentPreferences.curiosity_statement;
        const interestsElem = document.getElementById("interestsInput");
        if (interestsElem) interestsElem.value = currentPreferences.curiosity_statement;
      }

      // Add followed creators
      if (stagedYouTubeResult.followed_creators && stagedYouTubeResult.followed_creators.length > 0) {
        const existingFollows = getFollowedCreators();
        const set = new Set(existingFollows.map(c => c.name.toLowerCase()));
        stagedYouTubeResult.followed_creators.forEach(fc => {
          const name = typeof fc === "string" ? fc : fc.name;
          if (name && !set.has(name.toLowerCase())) {
            existingFollows.push({ name, added_at: new Date().toISOString(), source: "youtube_sync" });
            set.add(name.toLowerCase());
          }
        });
        saveFollowedCreators(existingFollows);
        updateFollowedCreatorsNavBadge();
      }

      // Populate fresh categories based on inferred latent discovery subjects from YouTube
      const latentSubjects = stagedYouTubeResult.latent_subjects ||
        (stagedYouTubeResult.living_taste_profile?.subject_affinities
          ? Object.keys(stagedYouTubeResult.living_taste_profile.subject_affinities)
          : []);

      if (latentSubjects.length > 0 || stagedYouTubeResult.living_taste_profile) {
        const profile = getLivingTasteProfile();
        profile.imported_subject_affinities = profile.imported_subject_affinities || {};
        profile.imported_subject_evidence_counts = profile.imported_subject_evidence_counts || {};

        if (stagedYouTubeResult.living_taste_profile) {
          const ltp = stagedYouTubeResult.living_taste_profile;
          if (ltp.imported_subject_affinities) {
            Object.assign(profile.imported_subject_affinities, ltp.imported_subject_affinities);
          }
          if (ltp.imported_subject_evidence_counts) {
            Object.assign(profile.imported_subject_evidence_counts, ltp.imported_subject_evidence_counts);
          }
          if (ltp.subject_affinities) {
            for (const [sub, score] of Object.entries(ltp.subject_affinities)) {
              if (profile.imported_subject_affinities[sub] === undefined) {
                profile.imported_subject_affinities[sub] = score;
                profile.imported_subject_evidence_counts[sub] = ltp.subject_evidence_counts?.[sub] || 2;
              }
            }
          }
        }

        for (const subject of latentSubjects) {
          if (profile.imported_subject_affinities[subject] === undefined) {
            profile.imported_subject_affinities[subject] = 2;
            profile.imported_subject_evidence_counts[subject] = 2;
          }
        }
        recalculateLivingTasteProfile(profile);
        saveLivingTasteProfile(profile);
        const freshCategories = latentSubjects.map((name, i) => ({
          id: "cat_yt_" + i + "_" + Date.now(),
          name,
          is_auto_created: true,
          is_dismissed: false,
          created_at: new Date().toISOString()
        }));
        saveCategories(freshCategories);
      } else {
        let categories = getCategories();
        if (!categories || categories.length === 0) {
          const seedCats = currentPreferences.curiosity_statement || currentPreferences.interests || "";
          const parts = splitInterestCategories(seedCats);
          categories = parts.map((name, i) => ({
            id: "cat_" + i + "_" + Date.now(),
            name,
            is_auto_created: false,
            is_dismissed: false,
            created_at: new Date().toISOString()
          }));
          saveCategories(categories);
        }
      }

      closeYouTubeModal();
      showSavedTasteBar(currentPreferences);
      renderHomepageTabs();
      await fetchFeed(currentPreferences);
    });
  }
}

function displayYouTubeSynthesisResult(data) {
  stagedYouTubeResult = data.updated_preferences;
  const card = document.getElementById("youtubeResultCard");
  const countBadge = document.getElementById("youtubeChannelCountBadge");
  const archList = document.getElementById("youtubeArchetypesList");
  const stmtText = document.getElementById("youtubeStatementText");
  const creatorsList = document.getElementById("youtubeCreatorsList");
  const explText = document.getElementById("youtubeExplanationText");

  if (countBadge) countBadge.textContent = `${data.channels_analyzed} channels analyzed`;

  if (archList) {
    archList.innerHTML = "";
    const profile = data.synthesized_profile || {};
    (profile.curiosity_archetypes || []).forEach(arch => {
      const info = ARCHETYPE_INFO[arch];
      const span = document.createElement("span");
      span.className = "px-2.5 py-1 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 font-semibold text-xs flex items-center gap-1.5";
      span.textContent = info ? `${info.icon} ${info.label}` : arch;
      archList.appendChild(span);
    });
  }

  if (stmtText) {
    const profile = data.synthesized_profile || {};
    stmtText.textContent = profile.curiosity_statement ? `"${profile.curiosity_statement}"` : "No statement synthesized.";
  }

  if (creatorsList) {
    creatorsList.innerHTML = "";
    const profile = data.synthesized_profile || {};
    const creators = profile.followed_creators || [];
    if (creators.length > 0) {
      creators.slice(0, 10).forEach(cr => {
        const name = typeof cr === "string" ? cr : (cr.name || "");
        if (!name) return;
        const span = document.createElement("span");
        span.className = "px-2 py-0.5 rounded bg-surface border border-surface-border text-zinc-300";
        span.textContent = name;
        creatorsList.appendChild(span);
      });
      if (creators.length > 10) {
        const more = document.createElement("span");
        more.className = "text-surface-muted italic text-[11px] self-center";
        more.textContent = `+${creators.length - 10} more`;
        creatorsList.appendChild(more);
      }
    } else {
      creatorsList.innerHTML = `<span class="text-surface-muted italic">None specified</span>`;
    }
  }

  if (explText) {
    const profile = data.synthesized_profile || {};
    explText.textContent = profile.summary || profile.ai_explanation || "";
  }

  if (card) card.classList.remove("hidden");
}

// --- DOM Lifecycle ---

document.addEventListener("DOMContentLoaded", async () => {
  await syncUserStateWithServer();
  initElements();
  checkHealth();
  bootstrapHomepage();
  renderContinueWatching();
  updateHistoryBadge();
  updateDislikedNavBadge();
  updateWatchLaterNavBadge();
  updateFollowedCreatorsNavBadge();
  updateYouTubeNavBadge(getSavedPreferences());
});

function initElements() {
  initArchetypesUI();
  initYouTubeModal();

  // Category tab view toggle buttons (New, Go Deeper, Connections)
  document.querySelectorAll(".category-view-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const view = btn.getAttribute("data-view");
      if (view) onSelectCategoryView(view);
    });
  });
  const slider = document.getElementById("serendipitySlider");
  const label = document.getElementById("serendipityLabel");
  const desc = document.getElementById("serendipityDescription");
  const form = document.getElementById("discoveryForm");

  slider.addEventListener("input", (e) => {
    const val = e.target.value;
    const titles = { 1: "Deep Dive", 2: "Specialized", 3: "Balanced", 4: "Cross-Domain", 5: "Radical" };
    label.textContent = `${val} (${titles[val]})`;
    desc.textContent = serendipityDescriptions[val];
  });

  form.addEventListener("input", saveTasteDraft);
  form.addEventListener("change", saveTasteDraft);

  // Submit / Save Taste Form
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const interestsVal = document.getElementById("interestsInput").value.trim();
    if (!interestsVal && selectedArchetypes.size === 0) {
      alert("Please select at least one curiosity archetype or enter your seed topics / curiosity statement.");
      return;
    }
    const interests = interestsVal || Array.from(selectedArchetypes).map(id => ARCHETYPE_INFO[id]?.label || id).join(", ");

    const expType = document.querySelector('input[name="experienceType"]:checked')?.value || "mix";
    const recency = document.querySelector('input[name="recencyPref"]:checked')?.value || "any";
    const serendipityVal = parseInt(slider.value, 10);

    // Advanced inputs
    const avoidElem = document.getElementById("avoidInput");
    const familiarElem = document.getElementById("familiarInput");
    const goalElem = document.getElementById("goalInput");
    const depthElem = document.getElementById("depthSelect");
    const cbElem = document.getElementById("avoidClickbaitCheckbox");

    let initialFeedMode = "mix";
    if (expType === "fun") {
      initialFeedMode = "entertainment";
    } else if (expType === "thoughtful") {
      initialFeedMode = "deep_learning";
    }

    const seenIds = getSeenVideoIds();
    const viewingSignals = extractViewingSignals();

    currentPreferences = {
      interests: interests,
      curiosity_archetypes: Array.from(selectedArchetypes),
      curiosity_statement: interestsVal || null,
      intellectual_vibes: currentPreferences?.intellectual_vibes || [],
      youtube_connected: currentPreferences?.youtube_connected || false,
      youtube_synced_channel_count: currentPreferences?.youtube_synced_channel_count || 0,
      youtube_sync_mode: currentPreferences?.youtube_sync_mode || null,
      experience_type: expType,
      feed_mode: initialFeedMode,
      recency_preference: recency,
      serendipity_level: serendipityVal,
      familiar_topics: familiarElem ? familiarElem.value.trim() : "",
      explore_topics: "",
      avoid_topics: avoidElem ? avoidElem.value.trim() : "",
      learning_goal: goalElem ? goalElem.value.trim() : "",
      depth_level: depthElem ? depthElem.value : "curious_explorer",
      avoid_sensationalism: cbElem ? cbElem.checked : true,
      seen_video_ids: seenIds,
      refresh_index: 0,
      completed_video_ids: viewingSignals.completed_video_ids,
      watched_channels: viewingSignals.watched_channels,
      watched_topics: viewingSignals.watched_topics,
    };

    if (!savePreferencesLocally(currentPreferences)) {
      document.getElementById("tasteSaveStatus").textContent = "Could not save in this browser. Check site storage settings before continuing.";
      return;
    }
    localStorage.removeItem(DRAFT_STORAGE_KEY);
    document.getElementById("tasteSaveStatus").textContent = "Taste saved on this device";
    currentTrail = [interests.slice(0, 30) + (interests.length > 30 ? "..." : "")];
    updateTrailUI();

    showSavedTasteBar(currentPreferences);
    updateModeButtonsUI(currentPreferences.feed_mode);
    updateSeenHistoryUI(seenIds.length);

    // Initialize categories if needed
    let categories = getCategories();
    if (!categories || categories.length === 0) {
      const parts = splitInterestCategories(interests);
      categories = parts.map((name, i) => ({
        id: "cat_" + i + "_" + Date.now(),
        name,
        is_auto_created: false,
        is_dismissed: false,
        created_at: new Date().toISOString()
      }));
      saveCategories(categories);
    }
    renderHomepageTabs();

    await fetchFeed(currentPreferences);
  });

  // Preset button chips
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("interestsInput").value = btn.textContent.trim();
      saveTasteDraft();
    });
  });

  // Edit Taste Button
  document.getElementById("editTasteBtn").addEventListener("click", () => {
    showTasteSetup(true);
  });

  // Cancel Edit Button
  document.getElementById("cancelEditBtn").addEventListener("click", () => {
    if (currentPreferences) {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
      restoreTasteForm(currentPreferences);
      showSavedTasteBar(currentPreferences);
    }
  });

  // Reset Taste Button
  document.getElementById("resetTasteBtn").addEventListener("click", () => {
    if (confirm("Reset your viewing taste preferences? This will return to the setup screen.")) {
      localStorage.removeItem(PREFS_STORAGE_KEY);
      localStorage.removeItem(DRAFT_STORAGE_KEY);
      localStorage.removeItem(FEED_STORAGE_KEY);
      localStorage.removeItem(TASTE_PROFILE_STORAGE_KEY);
      updateTasteProfileBadge();
      currentPreferences = null;
      selectedArchetypes.clear();
      updateArchetypeCardsUI();
      updateYouTubeNavBadge(null);
      document.getElementById("interestsInput").value = "";
      document.getElementById("discoveryForm").reset();
      slider.value = "3";
      label.textContent = "3 (Balanced)";
      desc.textContent = serendipityDescriptions[3];
      document.getElementById("tasteSaveStatus").textContent = "Your answers save as you type";

      document.getElementById("savedTasteBar").classList.add("hidden");
      document.getElementById("feedActionBar").classList.add("hidden");
      document.getElementById("tasteSetupSection").classList.remove("hidden");
      document.getElementById("cancelEditBtn").classList.add("hidden");
      document.getElementById("setupHeading").textContent = "Set Up Your Viewing Taste";

      document.getElementById("cardsGrid").innerHTML = "";
      document.getElementById("strategySection").classList.add("hidden");
      document.getElementById("poolExhaustedBanner").classList.add("hidden");
      document.getElementById("feedEmptyNotice").classList.remove("hidden");
    }
  });

  // Prominent Refresh Feed Button (independent per active tab and view)
  document.getElementById("refreshFeedBtn").addEventListener("click", async () => {
    if (!currentPreferences) return;
    const activeCat = getActiveCategory();
    const currentView = activeCat ? getActiveCategoryView(activeCat) : null;
    const tabKey = getTabCacheKey(activeCat, currentView);
    perTabRefreshIndex[tabKey] = (perTabRefreshIndex[tabKey] || 0) + 1;
    saveLocalJSON("rabbithole_refresh_indices", perTabRefreshIndex);
    delete tabFeedsCache[tabKey];

    currentPreferences.refresh_index = perTabRefreshIndex[tabKey];
    currentPreferences.active_category = activeCat;
    currentPreferences.category_view = currentView;
    currentPreferences.seen_video_ids = getSeenVideoIds();

    const viewingSignals = extractViewingSignals();
    currentPreferences.completed_video_ids = viewingSignals.completed_video_ids;
    currentPreferences.watched_channels = viewingSignals.watched_channels;
    currentPreferences.watched_topics = viewingSignals.watched_topics;
    currentPreferences.disliked_video_ids = getDislikedVideoIds();
    currentPreferences.disliked_records = getDislikedRecords();
    currentPreferences.categories = getCategories().map(c => c.name);
    currentPreferences.dismissed_categories = getDismissedCategories();
    currentPreferences.emerging_interests = getEmergingInterests().map(s => s.topic);
    currentPreferences.watch_later_video_ids = getWatchLaterRecords().map(r => r.video_id);

    await fetchFeed(currentPreferences);
  });

  // Watch Later Navigation & Modal
  const watchLaterModal = document.getElementById("watchLaterModal");
  document.getElementById("watchLaterNavBtn").addEventListener("click", () => {
    renderWatchLaterUI();
    watchLaterModal.classList.remove("hidden");
  });
  document.getElementById("closeWatchLaterBtn").addEventListener("click", () => {
    watchLaterModal.classList.add("hidden");
  });
  watchLaterModal.addEventListener("click", (e) => {
    if (e.target === watchLaterModal) watchLaterModal.classList.add("hidden");
  });
  document.getElementById("clearAllWatchLaterBtn").addEventListener("click", () => {
    if (confirm("Clear all videos in Watch Later?")) {
      clearAllWatchLater();
    }
  });

  // Followed Creators Navigation & Modal
  const followedModal = document.getElementById("followedCreatorsModal");
  document.getElementById("followedCreatorsNavBtn").addEventListener("click", () => {
    renderFollowedCreatorsModal();
    followedModal.classList.remove("hidden");
  });
  document.getElementById("closeFollowedCreatorsBtn").addEventListener("click", () => {
    followedModal.classList.add("hidden");
  });
  followedModal.addEventListener("click", (e) => {
    if (e.target === followedModal) followedModal.classList.add("hidden");
  });
  document.getElementById("clearAllFollowedBtn").addEventListener("click", () => {
    if (confirm("Unfollow all creators?")) {
      saveFollowedCreators([]);
      renderFollowedCreatorsModal();
    }
  });

  // Category Modal Controls & Tabs
  document.getElementById("addCategoryBtn").addEventListener("click", () => {
    openCategoryModal(null);
  });
  document.getElementById("closeCategoryModalBtn").addEventListener("click", closeCategoryModal);
  document.getElementById("cancelCategoryModalBtn").addEventListener("click", closeCategoryModal);
  const categoryModal = document.getElementById("categoryModal");
  categoryModal.addEventListener("click", (e) => {
    if (e.target === categoryModal) closeCategoryModal();
  });

  document.getElementById("saveCategoryModalBtn").addEventListener("click", () => {
    const input = document.getElementById("categoryNameInput");
    const name = input.value.trim();
    if (!name) return;

    const categories = getCategories();
    if (activeEditingCategory) {
      const oldName = activeEditingCategory.name;
      activeEditingCategory.name = name;
      const idx = categories.findIndex(c => c.id === activeEditingCategory.id);
      if (idx >= 0) categories[idx] = activeEditingCategory;
      saveCategories(categories);
      if (getActiveCategory() === oldName) {
        setActiveCategory(name);
      }
    } else {
      const newCat = {
        id: "cat_" + Date.now(),
        name,
        is_auto_created: false,
        is_dismissed: false,
        created_at: new Date().toISOString()
      };
      categories.push(newCat);
      saveCategories(categories);
      setActiveCategory(name);
    }
    closeCategoryModal();
    renderHomepageTabs();
    selectCategoryTab(getActiveCategory());
  });

  document.getElementById("dismissCategoryBtn").addEventListener("click", () => {
    if (!activeEditingCategory) return;
    const catName = activeEditingCategory.name;
    const dismissed = getDismissedCategories();
    if (!dismissed.includes(catName)) {
      dismissed.push(catName);
      saveDismissedCategories(dismissed);
    }
    const categories = getCategories().filter(c => c.id !== activeEditingCategory.id);
    saveCategories(categories);

    closeCategoryModal();
    selectCategoryTab(null);
  });

  // Promoted Banner Dismiss
  const dismissPromotedBtn = document.getElementById("dismissPromotedBannerBtn");
  if (dismissPromotedBtn) {
    dismissPromotedBtn.addEventListener("click", () => {
      document.getElementById("emergingPromotionBanner").classList.add("hidden");
    });
  }

  // Feed Mode Buttons
  document.getElementById("modeMixBtn").addEventListener("click", () => switchFeedMode("mix"));
  document.getElementById("modeEntertainmentBtn").addEventListener("click", () => switchFeedMode("entertainment"));
  document.getElementById("modeDeepLearningBtn").addEventListener("click", () => switchFeedMode("deep_learning"));

  // Clear Seen History (Shown Feeds Deduplication) Actions
  const handleClearSeenHistory = async () => {
    saveSeenVideoIds([]);
    if (currentPreferences) {
      currentPreferences.seen_video_ids = [];
      currentPreferences.refresh_index = 0;
      savePreferencesLocally(currentPreferences);
    }
    updateSeenHistoryUI(0);
    document.getElementById("poolExhaustedBanner").classList.add("hidden");
    if (currentPreferences) {
      await fetchFeed(currentPreferences);
    }
  };

  document.getElementById("clearHistoryBtn").addEventListener("click", handleClearSeenHistory);
  document.getElementById("bannerClearHistoryBtn").addEventListener("click", handleClearSeenHistory);

  // Viewing History Navigation
  const historyModal = document.getElementById("historyModal");
  document.getElementById("historyNavBtn").addEventListener("click", () => {
    renderViewingHistoryUI();
    historyModal.classList.remove("hidden");
  });
  document.getElementById("closeHistoryBtn").addEventListener("click", () => {
    historyModal.classList.add("hidden");
  });
  historyModal.addEventListener("click", (e) => {
    if (e.target === historyModal) historyModal.classList.add("hidden");
  });

  document.getElementById("clearAllHistoryBtn").addEventListener("click", () => {
    if (confirm("Clear all recorded viewing history? This will reset all in-progress and completed video progress.")) {
      clearAllViewingHistory();
    }
  });

  // History Filter Tabs
  document.querySelectorAll(".hist-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".hist-filter-btn").forEach((b) => {
        b.className = "hist-filter-btn px-2.5 py-1 rounded-md text-surface-muted hover:text-white cursor-pointer";
      });
      btn.className = "hist-filter-btn px-2.5 py-1 rounded-md text-emerald-400 bg-emerald-500/15 border border-emerald-500/30 cursor-pointer font-medium";
      currentHistoryFilter = btn.dataset.filter || "all";
      renderViewingHistoryUI();
    });
  });

  // Settings Modal
  const settingsModal = document.getElementById("settingsModal");
  document.getElementById("apiKeySettingsBtn").addEventListener("click", () => {
    settingsModal.classList.remove("hidden");
  });
  document.getElementById("closeSettingsBtn").addEventListener("click", () => {
    settingsModal.classList.add("hidden");
  });
  document.getElementById("saveKeysBtn").addEventListener("click", saveApiKey);

  // Player Modal Controls
  const playerModal = document.getElementById("playerModal");
  document.getElementById("closeModalBtn").addEventListener("click", closeModal);
  playerModal.addEventListener("click", (e) => {
    if (e.target === playerModal) closeModal();
  });

  // Restart video button in player modal
  document.getElementById("modalRestartBtn").addEventListener("click", () => {
    if (ytPlayer && ytPlayer.seekTo) {
      ytPlayer.seekTo(0, true);
      document.getElementById("modalResumeAlert").classList.add("hidden");
      if (activeVideoRecord) {
        activeVideoRecord.current_position_seconds = 0;
        updateViewingRecord(activeVideoRecord);
      }
    }
  });

  // Disliked Videos Modal Navigation
  const dislikedModal = document.getElementById("dislikedModal");
  document.getElementById("dislikedNavBtn").addEventListener("click", () => {
    renderDislikedList();
    dislikedModal.classList.remove("hidden");
  });
  document.getElementById("closeDislikedBtn").addEventListener("click", () => {
    dislikedModal.classList.add("hidden");
  });
  dislikedModal.addEventListener("click", (e) => {
    if (e.target === dislikedModal) dislikedModal.classList.add("hidden");
  });
  document.getElementById("clearAllDislikesBtn").addEventListener("click", () => {
    if (confirm("Clear all disliked videos? They will be eligible to appear in future feeds again.")) {
      clearAllDislikes();
      renderDislikedList();
    }
  });

  // Dislike Reason Modal Quick Choices
  document.querySelectorAll(".dislike-choice-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const reason = btn.getAttribute("data-reason");
      if (activeDislikeVideoId && reason) {
        updateDislikeReason(activeDislikeVideoId, reason);
      }
      closeDislikeReasonModal();
    });
  });
  document.getElementById("skipDislikeReasonBtn").addEventListener("click", closeDislikeReasonModal);
  document.getElementById("closeDislikeReasonBtn").addEventListener("click", closeDislikeReasonModal);
  const dislikeReasonModal = document.getElementById("dislikeReasonModal");
  dislikeReasonModal.addEventListener("click", (e) => {
    if (e.target === dislikeReasonModal) closeDislikeReasonModal();
  });

  // Undo Toast Actions
  document.getElementById("undoActionBtn").addEventListener("click", handleUndoDislike);
  document.getElementById("dismissUndoBtn").addEventListener("click", () => {
    document.getElementById("undoToast").classList.add("hidden");
    if (undoToastTimeout) clearTimeout(undoToastTimeout);
  });

  // Living Taste Profile Modal
  const tasteProfileNavBtn = document.getElementById("tasteProfileNavBtn");
  if (tasteProfileNavBtn) tasteProfileNavBtn.addEventListener("click", openTasteProfileModal);
  const closeTasteProfileBtn = document.getElementById("closeTasteProfileBtn");
  if (closeTasteProfileBtn) closeTasteProfileBtn.addEventListener("click", closeTasteProfileModal);
  const resetLearnedTasteBtn = document.getElementById("resetLearnedTasteBtn");
  if (resetLearnedTasteBtn) {
    resetLearnedTasteBtn.addEventListener("click", () => {
      if (confirm("Reset what Rabbithole has learned about your taste? Your setup preferences and categories will remain.")) {
        resetLivingTasteProfile();
      }
    });
  }
  const tasteProfileModal = document.getElementById("tasteProfileModal");
  if (tasteProfileModal) {
    tasteProfileModal.addEventListener("click", (e) => {
      if (e.target === tasteProfileModal) closeTasteProfileModal();
    });
  }
}

function bootstrapHomepage() {
  const saved = getSavedPreferences();
  const draft = readLocalJSON(DRAFT_STORAGE_KEY);
  const seenIds = getSeenVideoIds();
  updateSeenHistoryUI(seenIds.length);
  updateTasteProfileBadge();

  if (saved && (saved.interests || (saved.curiosity_archetypes && saved.curiosity_archetypes.length > 0))) {
    const viewingSignals = extractViewingSignals();
    currentPreferences = {
      ...saved,
      active_category: getActiveCategory(),
      seen_video_ids: seenIds,
      refresh_index: saved.refresh_index || 0,
      completed_video_ids: viewingSignals.completed_video_ids,
      watched_channels: viewingSignals.watched_channels,
      watched_topics: viewingSignals.watched_topics,
      disliked_video_ids: getDislikedVideoIds(),
      disliked_records: getDislikedRecords(),
    };
    const displayLabel = saved.curiosity_statement || saved.interests || (saved.curiosity_archetypes || []).join(", ");
    currentTrail = [displayLabel.slice(0, 30) + (displayLabel.length > 30 ? "..." : "")];
    updateTrailUI();
    restoreTasteForm(saved);

    let categories = getCategories();
    if (!categories || categories.length === 0) {
      const parts = splitInterestCategories(saved.interests || displayLabel);
      categories = parts.map((name, i) => ({
        id: "cat_" + i + "_" + Date.now(),
        name,
        is_auto_created: false,
        is_dismissed: false,
        created_at: new Date().toISOString()
      }));
      saveCategories(categories);
    }
    renderHomepageTabs();
    updateWatchLaterNavBadge();

    showSavedTasteBar(currentPreferences);
    updateModeButtonsUI(currentPreferences.feed_mode || "mix");
    const cachedFeed = readLocalJSON(FEED_STORAGE_KEY);
    if (cachedFeed && cachedFeed.key === feedCacheKey(currentPreferences) && Array.isArray(cachedFeed.data?.items)) {
      const dislikedSet = new Set(getDislikedVideoIds());
      cachedFeed.data.items = cachedFeed.data.items.filter(item => !dislikedSet.has(item.video.id));
      if (cachedFeed.data.items.length > 0) {
        displayFeed(cachedFeed.data, currentPreferences);
      } else {
        fetchFeed(currentPreferences);
      }
    } else {
      fetchFeed(currentPreferences);
    }
  } else {
    restoreTasteForm(draft);
    if (draft) document.getElementById("tasteSaveStatus").textContent = "Draft restored from this device";
    document.getElementById("tasteSetupSection").classList.remove("hidden");
    document.getElementById("savedTasteBar").classList.add("hidden");
    document.getElementById("feedActionBar").classList.add("hidden");
  }
}

function showSavedTasteBar(prefs) {
  const bar = document.getElementById("savedTasteBar");
  const setup = document.getElementById("tasteSetupSection");
  const actionBar = document.getElementById("feedActionBar");
  const tabsSection = document.getElementById("homepageTabsSection");

  const displayInterests = prefs.curiosity_statement || prefs.interests || "Curiosity Exploration";
  document.getElementById("summaryInterests").textContent = displayInterests;
  document.getElementById("summaryExperience").textContent = prefs.experience_type || "mix";
  document.getElementById("summaryRecency").textContent = prefs.recency_preference === "any" ? "Anytime" : (prefs.recency_preference === "recent" ? "Recent" : "Older finds");
  document.getElementById("summarySerendipity").textContent = `Dial: ${prefs.serendipity_level || 3}`;

  // Render archetype badges
  const archetypesContainer = document.getElementById("summaryArchetypes");
  if (archetypesContainer) {
    archetypesContainer.innerHTML = "";
    const archs = prefs.curiosity_archetypes || [];
    if (archs.length > 0) {
      archs.forEach(id => {
        const info = ARCHETYPE_INFO[id];
        const span = document.createElement("span");
        span.className = "px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-[10px] text-emerald-300 font-medium";
        span.textContent = info ? `${info.icon} ${info.label}` : id;
        archetypesContainer.appendChild(span);
      });
      archetypesContainer.classList.remove("hidden");
    } else {
      // Prompt user with a button to pick curiosity archetypes if they have an older profile
      const promptBtn = document.createElement("button");
      promptBtn.type = "button";
      promptBtn.className = "px-2.5 py-0.5 rounded-full bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/40 text-[11px] text-emerald-300 font-medium transition-colors cursor-pointer flex items-center gap-1.5 shadow-sm";
      promptBtn.innerHTML = `<span>✨</span><span>Choose Curiosity Archetypes</span>`;
      promptBtn.onclick = () => showTasteSetup(true);
      archetypesContainer.appendChild(promptBtn);
      archetypesContainer.classList.remove("hidden");
    }
  }

  updateYouTubeNavBadge(prefs);

  bar.classList.remove("hidden");
  setup.classList.add("hidden");
  actionBar.classList.remove("hidden");
  if (tabsSection) tabsSection.classList.remove("hidden");
}

function updateYouTubeNavBadge(prefs) {
  const badge = document.getElementById("youtubeNavBadge");
  if (!badge) return;
  if (prefs && prefs.youtube_connected) {
    const count = prefs.youtube_synced_channel_count || 0;
    badge.textContent = `${count} synced`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function showTasteSetup(isEditing = false) {
  const bar = document.getElementById("savedTasteBar");
  const setup = document.getElementById("tasteSetupSection");
  const cancelBtn = document.getElementById("cancelEditBtn");
  const heading = document.getElementById("setupHeading");

  if (isEditing && currentPreferences) {
    heading.textContent = "Edit Your Viewing Taste";
    cancelBtn.classList.remove("hidden");
    restoreTasteForm(readLocalJSON(DRAFT_STORAGE_KEY) || currentPreferences);
  }

  bar.classList.add("hidden");
  setup.classList.remove("hidden");
}

async function switchFeedMode(mode) {
  if (!currentPreferences) return;
  currentPreferences.feed_mode = mode;
  savePreferencesLocally(currentPreferences);
  updateModeButtonsUI(mode);
  currentPreferences.seen_video_ids = getSeenVideoIds();

  const viewingSignals = extractViewingSignals();
  currentPreferences.completed_video_ids = viewingSignals.completed_video_ids;
  currentPreferences.watched_channels = viewingSignals.watched_channels;
  currentPreferences.watched_topics = viewingSignals.watched_topics;

  await fetchFeed(currentPreferences);
}

function updateModeButtonsUI(activeMode) {
  const modes = [
    { id: "modeMixBtn", key: "mix" },
    { id: "modeEntertainmentBtn", key: "entertainment" },
    { id: "modeDeepLearningBtn", key: "deep_learning" },
  ];

  modes.forEach(({ id, key }) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (key === activeMode) {
      btn.className = "mode-btn flex-1 sm:flex-initial px-3.5 py-1.5 rounded-lg font-semibold text-white bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 transition-all cursor-pointer shadow-sm";
    } else {
      btn.className = "mode-btn flex-1 sm:flex-initial px-3.5 py-1.5 rounded-lg font-medium text-surface-muted hover:text-white border border-transparent transition-all cursor-pointer";
    }
  });
}

function updateSeenHistoryUI(count) {
  const label = document.getElementById("seenHistoryCount");
  if (label) {
    label.textContent = `Seen: ${count} video${count === 1 ? '' : 's'}`;
  }
}

function updateHistoryBadge() {
  const history = getViewingHistory();
  const count = Object.keys(history).length;
  const badge = document.getElementById("historyNavBadge");
  if (badge) {
    if (count > 0) {
      badge.textContent = count;
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const badge = document.getElementById("llmStatusBadge");
    const text = document.getElementById("llmStatusText");

    if (data.gemini_configured) {
      text.textContent = "Gemini Mode Active";
      badge.classList.remove("text-surface-muted");
      badge.classList.add("text-emerald-400", "border-emerald-500/30");
    } else {
      text.textContent = "Fast Heuristic Mode (Add Key for Full LLM)";
    }
  } catch (err) {
    console.error("Health check failed:", err);
  }
}

async function saveApiKey() {
  const geminiKey = document.getElementById("geminiKeyInput").value.trim();
  if (!geminiKey) return;

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gemini_api_key: geminiKey }),
    });
    if (res.ok) {
      document.getElementById("settingsModal").classList.add("hidden");
      document.getElementById("geminiKeyInput").value = "";
      await checkHealth();
    }
  } catch (err) {
    alert("Failed to save API key.");
  }
}

async function fetchFeed(payload) {
  const requestId = ++latestFeedRequestId;
  const loading = document.getElementById("loadingState");
  const empty = document.getElementById("feedEmptyNotice");
  const grid = document.getElementById("cardsGrid");
  const strategy = document.getElementById("strategySection");

  loading.classList.remove("hidden");
  empty.classList.add("hidden");
  grid.classList.add("opacity-50", "pointer-events-none");
  strategy.classList.add("hidden");

  // Ensure viewing signals and dislike feedback are attached
  const viewingSignals = extractViewingSignals();
  payload.completed_video_ids = viewingSignals.completed_video_ids;
  payload.watched_channels = viewingSignals.watched_channels;
  payload.watched_topics = viewingSignals.watched_topics;
  payload.disliked_video_ids = getDislikedVideoIds();
  payload.disliked_records = getDislikedRecords();
  payload.categories = getCategories()
    .map(c => (typeof c === "string" ? c.trim() : (c && c.name ? c.name.trim() : "")))
    .filter(Boolean);
  payload.active_category = getActiveCategory();
  if (payload.active_category) {
    const view = getActiveCategoryView(payload.active_category);
    payload.category_view = view;
    const otherViewIds = new Set();
    ["new", "deeper", "connections"].forEach(v => {
      if (v !== view) {
        const otherKey = getTabCacheKey(payload.active_category, v);
        if (tabFeedsCache[otherKey] && tabFeedsCache[otherKey].items) {
          tabFeedsCache[otherKey].items.forEach(it => {
            if (it.video && it.video.id) otherViewIds.add(it.video.id);
          });
        }
      }
    });
    payload.other_view_video_ids = Array.from(otherViewIds);
  } else {
    payload.category_view = null;
    payload.other_view_video_ids = [];
  }
  payload.saved_videos = getWatchLaterRecords();
  payload.dismissed_categories = (getDismissedCategories() || []).map(d => (typeof d === "string" ? d : d.name || "")).filter(Boolean);
  payload.emerging_interests = (getEmergingInterests() || []).map(s => (typeof s === "string" ? s.trim() : (s && s.topic ? s.topic.trim() : ""))).filter(Boolean);
  payload.watch_later_video_ids = getWatchLaterRecords().map(r => r.video_id);
  payload.followed_creators = getFollowedCreators();
  payload.living_taste_profile = getLivingTasteProfile();
  const queryHistory = readLocalJSON("rabbithole_recent_queries") || {};
  const scope = payload.active_category || "main";
  payload.recent_queries = (queryHistory[scope] || []).slice(-40);
  const impressions = readLocalJSON("rabbithole_recent_impressions") || [];
  payload.recent_shown_video_ids = impressions.filter(x => Date.now() - x.at < 3600000).slice(-20).map(x => x.id);
  const shownSubjectsHistory = readLocalJSON("rabbithole_recent_shown_subjects") || [];
  payload.recent_shown_subjects = shownSubjectsHistory.slice(-20);
  const shownCreatorsHistory = readLocalJSON("rabbithole_recent_shown_creators") || [];
  payload.recent_shown_creators = shownCreatorsHistory.slice(-20);
  const requestPayload = { ...payload };
  const requestedTasteProfile = JSON.stringify(payload.living_taste_profile);

  try {
    const res = await fetch("/api/feed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
    });

    if (!res.ok) {
      throw new Error(`Feed generation failed: ${res.statusText}`);
    }

    const data = await res.json();
    if (requestId !== latestFeedRequestId) return;
    // Feedback received during generation makes this response obsolete.
    if (JSON.stringify(getLivingTasteProfile()) !== requestedTasteProfile) return;
    const history = readLocalJSON("rabbithole_recent_queries") || {};
    history[scope] = [...(history[scope] || []), ...(data.generated_queries || []).map(q => q.query)].slice(-40);
    saveLocalJSON("rabbithole_recent_queries", history);
    const tabCacheKey = getTabCacheKey(requestPayload.active_category, requestPayload.category_view);
    tabFeedsCache[tabCacheKey] = data;
    displayFeed(data, requestPayload);
    if (currentPreferences) saveLocalJSON(FEED_STORAGE_KEY, {key: feedCacheKey(currentPreferences), data});
    if (currentPreferences) savePreferencesLocally(currentPreferences);
  } catch (err) {
    if (requestId !== latestFeedRequestId) return;
    console.error(err);
    grid.innerHTML = `<div class="p-6 col-span-2 text-center text-red-400 border border-red-500/20 bg-red-500/10 rounded-xl">Error building feed: ${escapeHtml(err.message)}</div>`;
  } finally {
    if (requestId === latestFeedRequestId) {
      loading.classList.add("hidden");
      grid.classList.remove("opacity-50", "pointer-events-none");
    }
  }
}

function displayFeed(data, payload) {
  const activeCat = (payload && payload.active_category) || getActiveCategory();
  const currentView = (payload && payload.category_view) || (activeCat ? getActiveCategoryView(activeCat) : null);
  const tabKey = getTabCacheKey(activeCat, currentView);
  tabFeedsCache[tabKey] = data;

  const exhaustionBanner = document.getElementById("poolExhaustedBanner");
  if (data.pool_exhausted) {
    document.getElementById("poolExhaustedMsg").textContent = data.status_message;
    exhaustionBanner.classList.remove("hidden");
  } else {
    exhaustionBanner.classList.add("hidden");
  }

    // Accumulate shown video IDs, subjects, and creators for feed deduplication & diversity planning
    if (data.items && data.items.length > 0) {
      const returnedIds = data.items.map(i => i.video.id).filter(Boolean);
      const returnedSubjects = data.items.map(i => i.domain || (i.video && i.video.source_domain)).filter(Boolean);
      const returnedCreators = data.items.map(i => i.video && i.video.channel).filter(Boolean);

      const subHistory = readLocalJSON("rabbithole_recent_shown_subjects") || [];
      const updatedSubs = Array.from(new Set([...subHistory, ...returnedSubjects])).slice(-40);
      saveLocalJSON("rabbithole_recent_shown_subjects", updatedSubs);

      const creatorHistory = readLocalJSON("rabbithole_recent_shown_creators") || [];
      const updatedCreators = Array.from(new Set([...creatorHistory, ...returnedCreators])).slice(-40);
      saveLocalJSON("rabbithole_recent_shown_creators", updatedCreators);

      const impressions = (readLocalJSON("rabbithole_recent_impressions") || []).filter(x => Date.now() - x.at < 3600000);
      const known = new Set(impressions.map(x => x.id));
      saveLocalJSON("rabbithole_recent_impressions", [...impressions, ...returnedIds.filter(id => !known.has(id)).map(id => ({id, at: Date.now()}))].slice(-100));
      const existingSeen = getSeenVideoIds();
      const mergedSeen = Array.from(new Set([...existingSeen, ...returnedIds]));
      saveSeenVideoIds(mergedSeen);
      updateSeenHistoryUI(mergedSeen.length);
      if (currentPreferences) {
        currentPreferences.seen_video_ids = mergedSeen;
        currentPreferences.recent_shown_subjects = updatedSubs;
        currentPreferences.recent_shown_creators = updatedCreators;
      }
    }

  renderStrategy(data);
  renderCards(data.items, payload.feed_mode);
}

function renderStrategy(data) {
  const section = document.getElementById("strategySection");
  const list = document.getElementById("queriesList");
  const stats = document.getElementById("filteredStats");

  if (!data.generated_queries || data.generated_queries.length === 0) {
    section.classList.add("hidden");
    return;
  }

  stats.textContent = `${data.total_candidates_analyzed} candidates • ${data.filtered_out_count} filtered • ${data.seen_count || 0} shown excluded`;
  list.innerHTML = "";

  data.generated_queries.forEach((q) => {
    const pill = document.createElement("div");
    pill.className = "px-2.5 py-1 rounded-md bg-surface border border-surface-border text-surface-muted text-[11px] flex items-center space-x-1.5";
    pill.innerHTML = `
      <span class="text-emerald-400">✦</span>
      <span class="font-medium text-zinc-200">"${escapeHtml(q.query)}"</span>
      <span class="text-[10px] text-surface-muted">(${escapeHtml(q.domain)})</span>
    `;
    list.appendChild(pill);
  });

  section.classList.remove("hidden");
}

function renderCards(items, feedMode) {
  const grid = document.getElementById("cardsGrid");
  const empty = document.getElementById("feedEmptyNotice");

  grid.replaceChildren();

  if (!items || items.length === 0) {
    empty.classList.remove("hidden");
    empty.querySelector("h3").textContent = "No New Videos Available";
    empty.querySelector("p").textContent = "All matching candidates have either been seen in recent feeds or filtered out. Click 'Clear History' above or adjust your taste.";
    return;
  }

  items.forEach((item, index) => {
    const card = createVideoCard(item, index, feedMode);
    grid.appendChild(card);
  });
}

function createVideoCard(item, index, feedMode) {
  const vid = item.video;
  const ev = item.assessment;

  const card = document.createElement("article");
  card.className = "feed-card bg-surface-raised border border-surface-border rounded-2xl overflow-hidden flex flex-col justify-between";

  const substancePct = Math.round(ev.substance_score * 100);
  const noveltyPct = Math.round(ev.novelty_score * 100);
  const entertainmentPct = Math.round((ev.entertainment_score ?? 0.5) * 100);
  const clickbaitText = ev.clickbait_score <= 0.2 ? "Clean Title" : `Hype ${(ev.clickbait_score * 100).toFixed(0)}%`;

  const roleName = item.path_step || "Highlight";
  const roleReason = item.path_reason || "Selected based on available metadata.";
  const isSaved = isWatchLater(vid.id);
  const isFollowed = isCreatorFollowed(vid.channel, vid.channel_id);

  // Check if video is already started or completed in local history
  const savedRecord = getViewingRecord(vid.id);
  let progressBadge = "";
  if (savedRecord) {
    if (savedRecord.status === "completed") {
      progressBadge = `<span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-medium">Completed ✓</span>`;
    } else if (savedRecord.current_position_seconds > 5) {
      const pct = Math.round((savedRecord.current_position_seconds / (savedRecord.duration_seconds || vid.duration_seconds || 600)) * 100);
      progressBadge = `<span class="px-2 py-0.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30 text-[10px] font-medium">In Progress (${pct}%)</span>`;
    }
  }

  const discTier = item.discovery_type || (item.video && item.video.discovery_type) || "";
  const discTierLabel = discTier ? `: ${escapeHtml(discTier.charAt(0).toUpperCase() + discTier.slice(1))}` : "";
  const discoveryBadge = item.is_discovery
    ? `<span class="px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30 text-[10px] font-bold tracking-wide">✨ Discovery${discTierLabel}</span>`
    : "";

  card.innerHTML = `
    <!-- Step / Role Pill Header -->
    <div class="px-5 pt-4 pb-1 text-xs flex items-center justify-between border-b border-surface-border/40 bg-surface/30">
      <div class="flex items-center space-x-2">
        <span class="w-5 h-5 rounded-full bg-emerald-500/15 text-emerald-400 flex items-center justify-center font-bold text-[10px] border border-emerald-500/30">
          ${index + 1}
        </span>
        <span class="font-bold text-emerald-400 tracking-wide">${escapeHtml(roleName)}</span>
        ${discoveryBadge}
        ${progressBadge}
      </div>
      <div class="flex items-center space-x-2">
        <span class="text-[11px] text-surface-muted truncate max-w-[100px] sm:max-w-[140px] text-right" title="${escapeHtml(roleReason)}">${escapeHtml(roleReason)}</span>
        <button type="button" class="watch-later-card-btn px-2 py-0.5 rounded-lg border ${isSaved ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-400' : 'border-surface-border/80 text-surface-muted hover:text-white hover:border-emerald-500/50 hover:bg-emerald-500/10'} text-[11px] font-medium flex items-center gap-1 transition-colors cursor-pointer" title="Save to Watch Later">
          <span>🔖</span>
          <span class="watch-later-label hidden sm:inline">${isSaved ? "Saved ✓" : "Watch Later"}</span>
        </button>
        <button type="button" class="dislike-card-btn px-2 py-0.5 rounded-lg border border-surface-border/80 hover:border-red-500/50 hover:bg-red-500/10 text-surface-muted hover:text-red-400 text-[11px] font-medium flex items-center gap-1 transition-colors cursor-pointer" title="Dislike this video and remove from feed">
          <span>👎</span>
          <span class="hidden sm:inline">Dislike</span>
        </button>
      </div>
    </div>

    <!-- Thumbnail & Duration -->
    <div class="relative aspect-video bg-black cursor-pointer group thumbnail-click">
      <img
        src="${escapeHtml(safeThumbnail(vid.thumbnail))}"
        alt="${escapeHtml(vid.title)}"
        class="w-full h-full object-cover group-hover:opacity-90 transition-opacity"
        loading="lazy"
      >
      <div class="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-60 group-hover:opacity-40 transition-opacity"></div>
      
      <!-- Duration Badge -->
      <div class="absolute bottom-3 right-3 px-2 py-0.5 rounded bg-black/80 text-white font-mono text-[11px] font-medium backdrop-blur-sm">
        ${vid.duration_seconds ? formatDuration(vid.duration_seconds) : "Video"}
      </div>

      <!-- Play Button Overlay -->
      <div class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
        <div class="w-12 h-12 rounded-full bg-emerald-500/90 text-black flex items-center justify-center text-lg pl-1 shadow-lg transform group-hover:scale-105 transition-transform">
          ▶
        </div>
      </div>

      <!-- Progress bar if previously started -->
      ${savedRecord && savedRecord.current_position_seconds > 5 && savedRecord.status !== 'completed' ? `
        <div class="absolute bottom-0 left-0 right-0 h-1 bg-surface-border">
          <div class="bg-emerald-400 h-1" style="width: ${Math.min(100, Math.round((savedRecord.current_position_seconds / (savedRecord.duration_seconds || vid.duration_seconds || 600)) * 100))}%"></div>
        </div>
      ` : ''}
    </div>

    <!-- Content Details -->
    <div class="p-5 flex-1 flex flex-col justify-between space-y-4">
      <div class="space-y-2.5">
        <div class="flex items-center justify-between text-[11px] text-surface-muted gap-2">
          <div class="flex items-center space-x-2 min-w-0">
            <span class="font-medium text-emerald-400 truncate max-w-[110px] sm:max-w-[170px]" title="${escapeHtml(vid.channel)}">${escapeHtml(vid.channel)}</span>
            <button type="button" class="follow-creator-card-btn px-2 py-0.5 rounded-full border text-[10px] font-medium transition-colors cursor-pointer shrink-0 ${isFollowed ? 'border-purple-500/50 bg-purple-500/15 text-purple-300 hover:bg-purple-500/25' : 'border-surface-border/80 text-surface-muted hover:text-white hover:border-purple-500/50 hover:bg-purple-500/10'}" data-channel="${escapeHtml(vid.channel)}" data-channel-id="${escapeHtml(vid.channel_id || '')}" title="${isFollowed ? 'Following creator. Click to unfollow.' : 'Follow creator to tune recommendations to your taste.'}">
              <span class="follow-creator-label">${isFollowed ? 'Following ✓' : '+ Follow'}</span>
            </button>
          </div>
          <span class="shrink-0">${formatViews(vid.view_count)} views</span>
        </div>

        <h3 class="font-semibold text-white text-sm leading-snug line-clamp-2 hover:text-emerald-300 transition-colors cursor-pointer title-click">
          ${escapeHtml(vid.title)}
        </h3>

        <!-- Rabbithole Grounded Recommendation Reason -->
        <div class="p-3 rounded-xl ${item.is_discovery ? 'bg-purple-950/25 border border-purple-500/30' : 'bg-surface border border-surface-border/60'} text-xs text-zinc-300 space-y-1">
          <div class="flex items-center space-x-1.5 ${item.is_discovery ? 'text-purple-400 font-semibold text-[11px]' : 'text-emerald-400 font-semibold text-[11px]'}">
            <span>${item.is_discovery ? '✨' : '💡'}</span>
            <span>${item.is_discovery ? 'Why this discovery?' : 'Why Recommended'}</span>
          </div>
          <p class="${item.is_discovery ? 'text-purple-200/90' : 'text-surface-muted'} leading-relaxed text-[11px]">
            ${escapeHtml(item.discovery_connection || item.discovery_signal || ev.connection_note || ev.relevance_reason || "Selected based on metadata evidence.")}
          </p>
        </div>
      </div>

      <!-- Quality Metrics & Branches -->
      <div class="space-y-3 pt-2 border-t border-surface-border/40">
        <!-- Metric Badges -->
        <div class="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
          <span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium" title="Estimated intellectual depth from metadata">
            ${substancePct}% Substance
          </span>
          <span class="px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20 font-medium" title="Metadata-based tone estimate (craft, humor, or playfulness) — not a verified measure of viewer enjoyment">
            ~${entertainmentPct}% Est. Craft & Fun
          </span>
          ${ev.is_entertainment_first && ev.entertainment_format ? `
          <span class="px-2 py-0.5 rounded bg-pink-500/10 text-pink-300 border border-pink-500/20 font-medium" title="Identified entertainment format: ${escapeHtml(ev.entertainment_format)}">
            ${escapeHtml(ev.entertainment_format.replace('_', ' '))}
          </span>` : ''}
          <span class="px-2 py-0.5 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20 font-medium" title="Estimated novelty">
            ${noveltyPct}% Novelty
          </span>
          <span class="px-2 py-0.5 rounded bg-surface border border-surface-border text-surface-muted">
            ${clickbaitText}
          </span>
          ${ev.evidence_source ? `
          <span class="px-2 py-0.5 rounded bg-surface border border-surface-border text-surface-muted" title="Evaluation grounded in ${ev.evidence_source === 'metadata_and_captions' ? 'video metadata and caption transcripts' : 'video metadata (captions optional/absent)'}">
            ${ev.evidence_source === 'metadata_and_captions' ? '📄 Captions' : '🏷️ Metadata'}
          </span>` : ''}
          ${ev.content_format && !ev.is_entertainment_first ? `
          <span class="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 font-medium" title="Format: ${escapeHtml(ev.content_format)}">
            ${escapeHtml(ev.content_format)}
          </span>` : ''}
        </div>

        <!-- Branch Out Section -->
        <div class="space-y-1.5">
          <span class="text-[10px] font-semibold tracking-wider text-surface-muted uppercase">
            Branch Out Next:
          </span>
          <div class="flex flex-wrap gap-1.5 branch-container"></div>
        </div>
      </div>
    </div>
  `;

  // Render branch buttons
  const branchContainer = card.querySelector(".branch-container");
  (ev.branch_suggestions || []).forEach((branchText) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "branch-pill px-2.5 py-1 rounded-lg bg-surface border border-surface-border text-[11px] text-zinc-300 flex items-center space-x-1 cursor-pointer hover:border-emerald-500/50 hover:text-white transition-colors";
    btn.innerHTML = `<span>⤹</span> <span>${escapeHtml(branchText)}</span>`;
    btn.addEventListener("click", () => handleBranchOut(branchText));
    branchContainer.appendChild(btn);
  });

  // Open modal clicks
  const openAction = () => openModal(vid, ev, null, item);
  card.querySelector(".thumbnail-click").addEventListener("click", openAction);
  card.querySelector(".title-click").addEventListener("click", openAction);

  // Follow Creator button click
  const followBtn = card.querySelector(".follow-creator-card-btn");
  if (followBtn) {
    followBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const chName = followBtn.getAttribute("data-channel") || vid.channel;
      const chId = followBtn.getAttribute("data-channel-id") || vid.channel_id || null;
      toggleFollowCreator(chName, chId, vid.thumbnail);
    });
  }

  // Watch Later button click
  const wlBtn = card.querySelector(".watch-later-card-btn");
  if (wlBtn) {
    wlBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const savedNow = toggleWatchLater(item);
      const labelElem = wlBtn.querySelector(".watch-later-label");
      if (savedNow) {
        wlBtn.classList.add("border-emerald-500/50", "bg-emerald-500/10", "text-emerald-400");
        wlBtn.classList.remove("border-surface-border/80", "text-surface-muted");
        if (labelElem) labelElem.textContent = "Saved ✓";
      } else {
        wlBtn.classList.remove("border-emerald-500/50", "bg-emerald-500/10", "text-emerald-400");
        wlBtn.classList.add("border-surface-border/80", "text-surface-muted");
        if (labelElem) labelElem.textContent = "Watch Later";
      }
    });
  }

  // Dislike button click
  const dislikeBtn = card.querySelector(".dislike-card-btn");
  if (dislikeBtn) {
    dislikeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      handleDislike(item, card, index);
    });
  }

  return card;
}

function handleBranchOut(branchTopic) {
  currentTrail.push(branchTopic);
  updateTrailUI();

  window.scrollTo({ top: 100, behavior: "smooth" });

  currentPreferences = {
    ...(currentPreferences || {}),
    interests: branchTopic,
    seed_topic: branchTopic,
    refresh_index: ((currentPreferences && currentPreferences.refresh_index) || 0) + 1,
    seen_video_ids: getSeenVideoIds(),
  };

  showSavedTasteBar(currentPreferences);
  fetchFeed(currentPreferences);
}

function updateTrailUI() {
  const section = document.getElementById("journeySection");
  const container = document.getElementById("trailContainer");

  if (currentTrail.length <= 1) {
    section.classList.add("hidden");
    return;
  }

  container.innerHTML = "";
  currentTrail.forEach((crumb, idx) => {
    const item = document.createElement("span");
    item.className = "flex items-center space-x-1.5 font-mono text-[11px]";
    item.innerHTML = `
      <span class="px-2 py-0.5 rounded bg-surface border border-surface-border text-zinc-200">${escapeHtml(crumb)}</span>
      ${idx < currentTrail.length - 1 ? '<span class="text-surface-muted">→</span>' : ''}
    `;
    container.appendChild(item);
  });

  section.classList.remove("hidden");
}

// --- Continue Watching Section ---

function renderContinueWatching() {
  const section = document.getElementById("continueWatchingSection");
  const container = document.getElementById("continueWatchingCards");
  const countBadge = document.getElementById("continueWatchingCount");

  const history = getViewingHistory();
  // Filter for in-progress or started videos that are not completed
  const resumeable = Object.values(history).filter(r => {
    return (r.status === "in_progress" || (r.status === "started" && r.current_position_seconds > 5)) && r.status !== "completed";
  });

  // Sort by most recently watched
  resumeable.sort((a, b) => new Date(b.last_watched_timestamp || 0) - new Date(a.last_watched_timestamp || 0));

  if (resumeable.length === 0) {
    section.classList.add("hidden");
    return;
  }

  countBadge.textContent = resumeable.length;
  container.innerHTML = "";

  resumeable.forEach(r => {
    const dur = r.duration_seconds || 600;
    const pos = r.current_position_seconds || 0;
    const pct = Math.min(100, Math.max(0, Math.round((pos / dur) * 100)));
    const posMin = Math.floor(pos / 60);
    const durMin = Math.ceil(dur / 60);

    const card = document.createElement("div");
    card.className = "min-w-[280px] sm:min-w-[320px] max-w-[320px] shrink-0 snap-start bg-surface border border-surface-border rounded-xl p-3 flex flex-col justify-between space-y-3 shadow-sm hover:border-emerald-500/40 transition-colors";

    card.innerHTML = `
      <div class="space-y-2">
        <!-- Thumbnail with Progress Bar -->
        <div class="relative aspect-video rounded-lg overflow-hidden bg-black cursor-pointer cw-play">
          <img src="${escapeHtml(safeThumbnail(r.thumbnail))}" alt="${escapeHtml(r.title)}" class="w-full h-full object-cover">
          <div class="absolute inset-0 bg-black/30 flex items-center justify-center hover:bg-black/10 transition-colors">
            <div class="w-8 h-8 rounded-full bg-emerald-500 text-black flex items-center justify-center pl-0.5 text-sm font-bold shadow">▶</div>
          </div>
          <!-- Progress bar -->
          <div class="absolute bottom-0 left-0 right-0 h-1.5 bg-black/60">
            <div class="bg-emerald-400 h-1.5" style="width: ${pct}%"></div>
          </div>
          <span class="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 font-mono text-[10px] text-zinc-300">
            ${formatDuration(Math.floor(pos))} / ${formatDuration(dur)}
          </span>
        </div>

        <div>
          <div class="flex items-center justify-between text-[11px] text-surface-muted">
            <span class="truncate max-w-[70%] font-medium text-emerald-400">${escapeHtml(r.channel)}</span>
            <span class="text-zinc-400">${pct}% done</span>
          </div>
          <h4 class="text-xs font-semibold text-white line-clamp-2 mt-0.5 hover:text-emerald-300 cursor-pointer cw-play">
            ${escapeHtml(r.title)}
          </h4>
        </div>
      </div>

      <!-- Action Row -->
      <div class="flex items-center justify-between pt-1 border-t border-surface-border/40 text-xs">
        <span class="text-[11px] text-surface-muted font-medium">
          ${posMin} of ${durMin} min
        </span>
        <div class="flex items-center space-x-1.5">
          <button type="button" class="cw-remove p-1 text-surface-muted hover:text-red-400 text-xs transition-colors cursor-pointer" title="Remove from Continue Watching">
            ✕
          </button>
          <button type="button" class="cw-resume px-3 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-semibold text-xs flex items-center space-x-1 shadow transition-all cursor-pointer">
            <span>▶</span>
            <span>Resume</span>
          </button>
        </div>
      </div>
    `;

    const resumeAction = () => {
      openModal({
        id: r.video_id,
        title: r.title,
        channel: r.channel,
        thumbnail: r.thumbnail,
        duration_seconds: r.duration_seconds,
        source_domain: r.source_domain,
      }, {
        connection_note: "Resumed from Continue Watching."
      }, r.current_position_seconds);
    };

    card.querySelectorAll(".cw-play, .cw-resume").forEach(el => el.addEventListener("click", resumeAction));

    card.querySelector(".cw-remove").addEventListener("click", (e) => {
      e.stopPropagation();
      removeViewingRecord(r.video_id);
    });

    container.appendChild(card);
  });

  section.classList.remove("hidden");
}

// --- Viewing History View Modal ---

function renderViewingHistoryUI() {
  const container = document.getElementById("historyListContainer");
  const emptyNotice = document.getElementById("historyEmptyNotice");
  const totalStats = document.getElementById("historyTotalStats");

  const history = getViewingHistory();
  let records = Object.values(history);

  // Apply filter tab
  if (currentHistoryFilter === "in_progress") {
    records = records.filter(r => r.status === "in_progress" || (r.status === "started" && r.current_position_seconds > 5 && r.status !== "completed"));
  } else if (currentHistoryFilter === "completed") {
    records = records.filter(r => r.status === "completed");
  } else if (currentHistoryFilter === "started") {
    records = records.filter(r => r.status === "started");
  }

  // Sort descending by last_watched_timestamp
  records.sort((a, b) => new Date(b.last_watched_timestamp || 0) - new Date(a.last_watched_timestamp || 0));

  totalStats.textContent = `${records.length} item${records.length === 1 ? '' : 's'}`;
  container.innerHTML = "";

  if (records.length === 0) {
    emptyNotice.classList.remove("hidden");
    return;
  }
  emptyNotice.classList.add("hidden");

  records.forEach(r => {
    const dur = r.duration_seconds || 600;
    const pos = r.current_position_seconds || 0;
    const pct = Math.min(100, Math.max(0, Math.round((pos / dur) * 100)));
    const watchSec = Math.round(r.watch_time_seconds || 0);

    let statusBadge = "";
    if (r.status === "completed") {
      statusBadge = `<span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[11px] font-semibold">✓ Completed</span>`;
    } else if (r.status === "in_progress" || pos > 5) {
      statusBadge = `<span class="px-2 py-0.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30 text-[11px] font-semibold">⏳ In Progress (${Math.floor(pos / 60)} of ${Math.ceil(dur / 60)} min)</span>`;
    } else {
      statusBadge = `<span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[11px] font-semibold">▶ Started</span>`;
    }

    const relTime = formatRelativeTime(r.last_watched_timestamp);

    const row = document.createElement("div");
    row.className = "p-3 rounded-xl bg-surface border border-surface-border/60 hover:border-surface-border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3";

    row.innerHTML = `
      <div class="flex items-center space-x-3 w-full sm:w-auto flex-1 truncate">
        <!-- Thumbnail -->
        <div class="relative w-24 aspect-video rounded-lg overflow-hidden bg-black flex-shrink-0 cursor-pointer hist-play">
          <img src="${escapeHtml(safeThumbnail(r.thumbnail))}" alt="${escapeHtml(r.title)}" class="w-full h-full object-cover">
          <div class="absolute bottom-0 left-0 right-0 h-1 bg-black/60">
            <div class="${r.status === 'completed' ? 'bg-emerald-400' : 'bg-sky-400'} h-1" style="width: ${pct}%"></div>
          </div>
        </div>

        <div class="truncate space-y-1 flex-1">
          <div class="flex items-center space-x-2">
            ${statusBadge}
            <span class="text-[11px] text-surface-muted truncate">${escapeHtml(r.channel)}</span>
          </div>
          <h4 class="text-xs font-semibold text-white truncate cursor-pointer hover:text-emerald-300 hist-play">
            ${escapeHtml(r.title)}
          </h4>
          <div class="flex items-center space-x-3 text-[10px] font-mono text-surface-muted">
            <span>Watched: ${formatDuration(watchSec)}</span>
            <span>•</span>
            <span>Position: ${formatDuration(Math.floor(pos))} / ${formatDuration(dur)}</span>
            <span>•</span>
            <span>${relTime}</span>
          </div>
        </div>
      </div>

      <!-- Action buttons -->
      <div class="flex items-center space-x-2 self-end sm:self-center">
        <button type="button" class="hist-play px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-semibold text-xs flex items-center space-x-1 cursor-pointer">
          <span>${r.status === 'completed' ? '↺' : '▶'}</span>
          <span>${r.status === 'completed' ? 'Watch Again' : 'Resume'}</span>
        </button>
        <button type="button" class="hist-del p-1.5 rounded-lg border border-surface-border hover:border-red-500/50 text-surface-muted hover:text-red-400 transition-colors text-xs cursor-pointer" title="Remove from viewing history">
          🗑️
        </button>
      </div>
    `;

    const playAction = () => {
      document.getElementById("historyModal").classList.add("hidden");
      openModal({
        id: r.video_id,
        title: r.title,
        channel: r.channel,
        thumbnail: r.thumbnail,
        duration_seconds: r.duration_seconds,
        source_domain: r.source_domain,
      }, {
        connection_note: "Opened from viewing history."
      }, r.status === "completed" ? 0 : r.current_position_seconds);
    };

    row.querySelectorAll(".hist-play").forEach(el => el.addEventListener("click", playAction));

    row.querySelector(".hist-del").addEventListener("click", () => {
      removeViewingRecord(r.video_id);
    });

    container.appendChild(row);
  });
}

// --- Real Playback Tracking Modal via YouTube IFrame API ---

function openModal(vid, ev, startSeconds = null, item = null) {
  const modal = document.getElementById("playerModal");
  document.getElementById("modalVideoTitle").textContent = vid.title;
  document.getElementById("modalVideoChannel").textContent = vid.channel;
  document.getElementById("modalConnectionNote").textContent = (ev && (ev.connection_note || ev.relevance_reason)) || (item && item.discovery_connection) || "Recommended based on metadata.";

  const ext = document.getElementById("modalExternalLink");
  ext.href = `https://www.youtube.com/watch?v=${encodeURIComponent(vid.id)}`;

  activeVideoAssessment = ev;

  // Retrieve or create viewing record
  let record = getViewingRecord(vid.id);
  if (!record) {
    record = {
      video_id: vid.id,
      title: vid.title,
      channel: vid.channel,
      thumbnail: vid.thumbnail,
      duration_seconds: vid.duration_seconds || 600,
      current_position_seconds: 0,
      watch_time_seconds: 0,
      status: "started",
      last_watched_timestamp: new Date().toISOString(),
      source_domain: vid.source_domain || (ev && ev.matched_concept) || null,
      source_query: vid.source_query || null,
      is_discovery: !!((item && item.is_discovery) || (ev && ev.is_discovery)),
      discovery_connection: (item && item.discovery_connection) || null,
    };
  } else if (item && item.is_discovery) {
    record.is_discovery = true;
    if (item.discovery_connection) record.discovery_connection = item.discovery_connection;
  }

  activeVideoRecord = record;

  // Calculate start position
  let targetStart = 0;
  if (startSeconds !== null && startSeconds !== undefined) {
    targetStart = Math.floor(startSeconds);
  } else if (record.current_position_seconds > 5 && record.status !== "completed") {
    targetStart = Math.floor(record.current_position_seconds);
  }

  const resumeAlert = document.getElementById("modalResumeAlert");
  const resumeText = document.getElementById("modalResumeText");
  if (targetStart > 5) {
    resumeText.textContent = `Resuming from ${formatDuration(targetStart)} (saved position)...`;
    resumeAlert.classList.remove("hidden");
  } else {
    resumeAlert.classList.add("hidden");
  }

  modal.classList.remove("hidden");
  loadYouTubePlayer(vid.id, targetStart);
}

function loadYouTubePlayer(videoId, startSeconds = 0) {
  const statusElem = document.getElementById("playerPlaybackStatus");
  statusElem.textContent = "Loading player...";

  const onPlayerReady = (event) => {
    statusElem.textContent = "Ready to play • Tracking inside Rabbithole";
    if (startSeconds > 0) {
      event.target.seekTo(startSeconds, true);
    }
    event.target.playVideo();
  };

  const onPlayerStateChange = (event) => {
    const state = event.data;
    if (window.YT && window.YT.PlayerState) {
      if (state === window.YT.PlayerState.PLAYING) {
        statusElem.textContent = "Playing • Tracking progress...";
        startPlaybackTracking();
      } else if (state === window.YT.PlayerState.PAUSED) {
        statusElem.textContent = "Paused • Progress saved";
        stopPlaybackTracking();
        saveCurrentPlaybackProgress();
      } else if (state === window.YT.PlayerState.ENDED) {
        statusElem.textContent = "Completed ✓ • Progress saved";
        stopPlaybackTracking();
        markVideoCompleted();
      }
    }
  };

  // If player instance already exists on page, load new video
  if (ytPlayer && typeof ytPlayer.loadVideoById === "function") {
    try {
      ytPlayer.loadVideoById({
        videoId: videoId,
        startSeconds: startSeconds
      });
      return;
    } catch (_) {
      // Re-create if corrupted
    }
  }

  // Create new player
  if (window.YT && window.YT.Player) {
    ytPlayer = new window.YT.Player("playerContainer", {
      videoId: videoId,
      playerVars: {
        autoplay: 1,
        start: startSeconds,
        rel: 0,
        modestbranding: 1,
        playsinline: 1,
      },
      events: {
        onReady: onPlayerReady,
        onStateChange: onPlayerStateChange,
        onError: (err) => {
          statusElem.textContent = "Player error or video unavailable";
        }
      }
    });
  } else {
    // Fallback if YouTube IFrame API script was blocked or delayed
    const container = document.getElementById("playerContainer");
    container.innerHTML = `
      <iframe
        id="playerIframe"
        class="w-full h-full border-0"
        src="https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=1&start=${startSeconds}&rel=0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowfullscreen
      ></iframe>
    `;
    statusElem.textContent = "Embedded player active";
  }
}

function startPlaybackTracking() {
  stopPlaybackTracking();
  lastTickTimestamp = Date.now();

  playbackTrackerInterval = setInterval(() => {
    if (!ytPlayer || typeof ytPlayer.getCurrentTime !== "function") return;

    try {
      const now = Date.now();
      const deltaSec = (now - (lastTickTimestamp || now)) / 1000.0;
      lastTickTimestamp = now;

      const currTime = ytPlayer.getCurrentTime();
      const dur = ytPlayer.getDuration() || (activeVideoRecord && activeVideoRecord.duration_seconds) || 600;

      if (activeVideoRecord) {
        // Measure real elapsed play time; cap delta to prevent seeking from counting as watch time!
        if (deltaSec > 0 && deltaSec < 3.0) {
          activeVideoRecord.watch_time_seconds = (activeVideoRecord.watch_time_seconds || 0) + deltaSec;
        }

        activeVideoRecord.current_position_seconds = currTime;
        activeVideoRecord.duration_seconds = Math.round(dur);

        // State detection:
        // Do not mark completed on seek alone: require actual watch time >= 30s or position >= 92% of duration
        if (currTime >= dur * 0.92 && activeVideoRecord.watch_time_seconds >= Math.min(30, dur * 0.3)) {
          activeVideoRecord.status = "completed";
          if (!activeVideoRecord.completed_at) {
            activeVideoRecord.completed_at = new Date().toISOString();
          }
        } else if (activeVideoRecord.watch_time_seconds >= 15 || currTime >= 15) {
          if (activeVideoRecord.status !== "completed") {
            activeVideoRecord.status = "in_progress";
          }
        } else {
          if (activeVideoRecord.status !== "completed" && activeVideoRecord.status !== "in_progress") {
            activeVideoRecord.status = "started";
          }
        }

        // Substantial watch time tracking for taste profile & discovery (>= 60s)
        if (activeVideoRecord.watch_time_seconds >= 60.0) {
          if (!activeVideoRecord._substantialTasteRecorded) {
            activeVideoRecord._substantialTasteRecorded = true;
            recordTasteAction(activeVideoRecord, "substantial_watch", activeVideoAssessment);
          }
          if ((activeVideoRecord.is_discovery || (activeVideoAssessment && activeVideoAssessment.is_discovery)) && !activeVideoRecord._discoveryWatchRecorded) {
            activeVideoRecord._discoveryWatchRecorded = true;
            handleDiscoveryInteraction(activeVideoRecord, "substantially_watched");
          }
        }

        if (activeVideoRecord.status === "completed" && !activeVideoRecord._completedTasteRecorded) {
          activeVideoRecord._completedTasteRecorded = true;
          recordTasteAction(activeVideoRecord, "completed", activeVideoAssessment);
        }

        // Save periodically every tick
        updateViewingRecord(activeVideoRecord);

        // Update status text
        const statusElem = document.getElementById("playerPlaybackStatus");
        statusElem.textContent = `Playing • ${formatDuration(Math.floor(currTime))} / ${formatDuration(Math.round(dur))} • Saved`;
      }
    } catch (_) {}
  }, 1000);
}

function stopPlaybackTracking() {
  if (playbackTrackerInterval) {
    clearInterval(playbackTrackerInterval);
    playbackTrackerInterval = null;
  }
  lastTickTimestamp = null;
}

function saveCurrentPlaybackProgress() {
  if (!activeVideoRecord) return;
  if (ytPlayer && typeof ytPlayer.getCurrentTime === "function") {
    try {
      const currTime = ytPlayer.getCurrentTime();
      const dur = ytPlayer.getDuration() || activeVideoRecord.duration_seconds;
      activeVideoRecord.current_position_seconds = currTime;
      activeVideoRecord.duration_seconds = Math.round(dur);

      if (currTime >= dur * 0.92 && activeVideoRecord.watch_time_seconds >= Math.min(30, dur * 0.3)) {
        activeVideoRecord.status = "completed";
        if (!activeVideoRecord.completed_at) {
          activeVideoRecord.completed_at = new Date().toISOString();
        }
      } else if (activeVideoRecord.watch_time_seconds >= 15 || currTime >= 15) {
        if (activeVideoRecord.status !== "completed") {
          activeVideoRecord.status = "in_progress";
        }
      }
    } catch (_) {}
  }
  updateViewingRecord(activeVideoRecord);
}

function markVideoCompleted() {
  if (!activeVideoRecord) return;
  activeVideoRecord.status = "completed";
  activeVideoRecord.completed_at = new Date().toISOString();
  if (ytPlayer && typeof ytPlayer.getDuration === "function") {
    try {
      const dur = ytPlayer.getDuration();
      activeVideoRecord.current_position_seconds = dur;
      activeVideoRecord.duration_seconds = Math.round(dur);
    } catch (_) {}
  }
  updateViewingRecord(activeVideoRecord);
  if (!activeVideoRecord._completedTasteRecorded) {
    activeVideoRecord._completedTasteRecorded = true;
    recordTasteAction(activeVideoRecord, "completed", activeVideoAssessment);
  }
}

function closeModal() {
  stopPlaybackTracking();
  saveCurrentPlaybackProgress();

  if (ytPlayer && typeof ytPlayer.stopVideo === "function") {
    try {
      ytPlayer.stopVideo();
    } catch (_) {}
  }

  const modal = document.getElementById("playerModal");
  modal.classList.add("hidden");
  activeVideoRecord = null;
  activeVideoAssessment = null;

  renderContinueWatching();
  renderViewingHistoryUI();
}

function formatDuration(sec) {
  if (!sec || isNaN(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const h = Math.floor(m / 60);
  const remainingM = m % 60;
  if (h > 0) return `${h}:${remainingM.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatViews(count) {
  if (!count) return "Archival";
  if (count >= 1000000) return (count / 1000000).toFixed(1) + "M";
  if (count >= 1000) return (count / 1000).toFixed(0) + "k";
  return count.toString();
}

function formatRelativeTime(isoString) {
  if (!isoString) return "Recently";
  try {
    const date = new Date(isoString);
    const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
    if (diffSec < 60) return "Just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  } catch (_) {
    return "Recently";
  }
}
