import { useState, useEffect, useRef, useCallback } from 'react'
import Header from './components/Header'
import WeekSelector from './components/WeekSelector'
import CategoryFilter from './components/CategoryFilter'
import PaperCard from './components/PaperCard'
import TrendSummary from './components/TrendSummary'
import { LANGUAGE_STORAGE_KEY, t } from './i18n.js'

const DATA_BASE = './data'
const LS_FAVORITES = 'arxiv-favorites'
const LS_READ      = 'arxiv-read'
// OpenAlex polite pool (higher rate limit)
const OPENALEX_EMAIL = 'beinvoked66@gmail.com'

import { readUrlState, buildUrlSearch } from './utils.js'

function pushUrlState(state) {
  const url = buildUrlSearch(state) || window.location.pathname
  window.history.pushState({}, '', url)
}

// ── External API fetchers ──────────────────────────────────────────────
async function fetchCitationsForPapers(papers) {
  const CHUNK = 5
  const results = {}
  for (let i = 0; i < papers.length; i += CHUNK) {
    const chunk = papers.slice(i, i + CHUNK)
    await Promise.allSettled(chunk.map(async p => {
      const id = p.id.split('v')[0]
      try {
        const url = `https://api.openalex.org/works/https://doi.org/10.48550/arXiv.${id}` +
                    `?select=cited_by_count&mailto=${OPENALEX_EMAIL}`
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        if (data?.cited_by_count != null) results[id] = data.cited_by_count
      } catch {}
    }))
    if (i + CHUNK < papers.length) await new Promise(r => setTimeout(r, 500))
  }
  return results
}

async function fetchGithubReposForPapers(papers) {
  const CHUNK = 10
  const results = {}
  for (let i = 0; i < papers.length; i += CHUNK) {
    const chunk = papers.slice(i, i + CHUNK)
    await Promise.allSettled(chunk.map(async p => {
      const id = p.id.split('v')[0]
      try {
        const res = await fetch(`https://huggingface.co/api/papers/${id}`)
        if (!res.ok) return
        const data = await res.json()
        if (data?.githubRepo) results[id] = data.githubRepo
      } catch {}
    }))
    if (i + CHUNK < papers.length) await new Promise(r => setTimeout(r, 300))
  }
  return results
}

async function fetchWeekData(date) {
  const res = await fetch(`${DATA_BASE}/weekly/${date}.json`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ── App ────────────────────────────────────────────────────────────────
export default function App() {
  const initial = readUrlState()

  const [index,            setIndex]            = useState(null)
  const [lang,             setLangRaw]           = useState(initial.lang)
  const [loadedWeeks,      setLoadedWeeks]      = useState([])
  const [toDate,           setToDateRaw]        = useState(initial.toDate)
  const [fromDate,         setFromDateRaw]      = useState(initial.fromDate)
  const [nextLoadIdx,      setNextLoadIdx]      = useState(0)
  const [loading,          setLoading]          = useState(true)
  const [loadingMore,      setLoadingMore]      = useState(false)
  const [hasMore,          setHasMore]          = useState(true)
  const [activeCat,        setActiveCatRaw]     = useState(initial.activeCat)
  const [search,           setSearchRaw]        = useState(initial.search)
  const [sortByCitations,  setSortRaw]          = useState(initial.sortByCitations)
  const [showFavoritesOnly,setFavRaw]           = useState(initial.showFavoritesOnly)
  const [citationMap,      setCitationMap]      = useState({})
  const [githubMap,        setGithubMap]        = useState({})
  const [favorites,        setFavorites]        = useState(
    () => new Set(JSON.parse(localStorage.getItem(LS_FAVORITES) || '[]'))
  )
  const [readPapers,       setReadPapers]       = useState(
    () => new Set(JSON.parse(localStorage.getItem(LS_READ) || '[]'))
  )
  const sentinelRef   = useRef(null)
  const filterRef     = useRef({ lang: initial.lang, toDate: initial.toDate, fromDate: initial.fromDate,
                                 activeCat: initial.activeCat, search: initial.search,
                                 sortByCitations: initial.sortByCitations,
                                 showFavoritesOnly: initial.showFavoritesOnly })
  const [showScrollTop, setShowScrollTop] = useState(false)

  // Wrapper that updates the URL with pushState.
  const pushFilter = useCallback((patch) => {
    const next = { ...filterRef.current, ...patch }
    filterRef.current = next
    pushUrlState(next)
  }, [])

  const setToDate = useCallback((v) => {
    setToDateRaw(v); pushFilter({ toDate: v, fromDate: null }); setFromDateRaw(null)
  }, [pushFilter])

  const setLang = useCallback((value) => {
    setLangRaw(value)
    localStorage.setItem(LANGUAGE_STORAGE_KEY, value)
    pushFilter({ lang: value })
  }, [pushFilter])

  const setFromDate = useCallback((v) => {
    setFromDateRaw(v); pushFilter({ fromDate: v })
  }, [pushFilter])

  const setActiveCat = useCallback((v) => {
    setActiveCatRaw(v); pushFilter({ activeCat: v })
  }, [pushFilter])

  const setSearch = useCallback((v) => {
    setSearchRaw(v); pushFilter({ search: v })
  }, [pushFilter])

  const setSortByCitations = useCallback((fn) => {
    setSortRaw(prev => {
      const next = typeof fn === 'function' ? fn(prev) : fn
      pushFilter({ sortByCitations: next })
      return next
    })
  }, [pushFilter])

  const setShowFavoritesOnly = useCallback((fn) => {
    setFavRaw(prev => {
      const next = typeof fn === 'function' ? fn(prev) : fn
      pushFilter({ showFavoritesOnly: next })
      return next
    })
  }, [pushFilter])

  // Handle browser back and forward navigation.
  useEffect(() => {
    const onPop = () => {
      const s = readUrlState()
      filterRef.current = s
      setLangRaw(s.lang)
      setToDateRaw(s.toDate)
      setFromDateRaw(s.fromDate)
      setActiveCatRaw(s.activeCat)
      setSearchRaw(s.search)
      setSortRaw(s.sortByCitations)
      setFavRaw(s.showFavoritesOnly)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, lang)
    document.documentElement.lang = lang
    document.title = t(lang).pageTitle
  }, [lang])

  // Scroll-to-top button.
  useEffect(() => {
    const onScroll = () => setShowScrollTop(window.scrollY > 400)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Favorite and read-state toggles.
  const toggleFavorite = useCallback((id) => {
    setFavorites(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      localStorage.setItem(LS_FAVORITES, JSON.stringify([...next]))
      return next
    })
  }, [])

  const toggleRead = useCallback((id) => {
    setReadPapers(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      localStorage.setItem(LS_READ, JSON.stringify([...next]))
      return next
    })
  }, [])

  // Fetch index.json.
  useEffect(() => {
    fetch(`${DATA_BASE}/index.json`)
      .then(r => r.json())
      .then(data => {
        setIndex(data)
        const weeks = data.weeks ?? []
        const urlState = readUrlState()
        const startDate =
          (urlState.toDate && weeks.find(w => w.date === urlState.toDate) ? urlState.toDate : null) ||
          weeks[0]?.date
        if (startDate) setToDateRaw(startDate)
        else setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  // Reset and load the first week when toDate changes.
  useEffect(() => {
    if (!toDate || !index) return
    const weeks = index.weeks
    const idx = weeks.findIndex(w => w.date === toDate)
    if (idx < 0) { setLoading(false); return }

    setLoadedWeeks([])
    setCitationMap({})
    setGithubMap({})
    setLoading(true)
    setHasMore(true)

    fetchWeekData(toDate).then(data => {
      setLoadedWeeks([data])
      setNextLoadIdx(idx + 1)
      setHasMore(idx + 1 < weeks.length)
      setLoading(false)
      const papers = data.categories.flatMap(c => c.papers)
      fetchCitationsForPapers(papers).then(m => setCitationMap(prev => ({ ...prev, ...m })))
      fetchGithubReposForPapers(papers).then(m => setGithubMap(prev => ({ ...prev, ...m })))
    }).catch(() => setLoading(false))
  }, [toDate, index])

  // Load the next week for infinite scrolling.
  const loadNextWeek = useCallback(() => {
    if (!index || loadingMore || !hasMore) return
    const weeks = index.weeks
    if (nextLoadIdx >= weeks.length) { setHasMore(false); return }
    const nextDate = weeks[nextLoadIdx].date
    if (fromDate && nextDate < fromDate) { setHasMore(false); return }

    setLoadingMore(true)
    fetchWeekData(nextDate).then(data => {
      setLoadedWeeks(prev => [...prev, data])
      const newIdx = nextLoadIdx + 1
      setNextLoadIdx(newIdx)
      setHasMore(newIdx < weeks.length && (!fromDate || weeks[newIdx]?.date >= fromDate))
      setLoadingMore(false)
      const papers = data.categories.flatMap(c => c.papers)
      fetchCitationsForPapers(papers).then(m => setCitationMap(prev => ({ ...prev, ...m })))
      fetchGithubReposForPapers(papers).then(m => setGithubMap(prev => ({ ...prev, ...m })))
    }).catch(() => setLoadingMore(false))
  }, [index, nextLoadIdx, loadingMore, hasMore, fromDate])

  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel) return
    const observer = new IntersectionObserver(
      entries => { if (entries[0].isIntersecting) loadNextWeek() },
      { threshold: 0.1 }
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [loadNextWeek])

  // Categories from index.json with counts from loaded weeks.
  const paperCountById = Object.fromEntries(
    loadedWeeks.flatMap(w => w.categories).reduce((map, c) => {
      map.set(c.id, (map.get(c.id) ?? 0) + c.papers.length)
      return map
    }, new Map())
  )
  const allCategories = (index?.categories ?? [...new Map(
    loadedWeeks.flatMap(w => w.categories).map(c => [c.id, c])
  ).values()]).map(c => ({ ...c, papers: Array(paperCountById[c.id] ?? 0).fill(null) }))

  const totalPapers = loadedWeeks.reduce(
    (sum, w) => sum + w.categories.reduce((s, c) => s + c.papers.length, 0), 0
  )

  return (
    <div style={{ minHeight: '100vh', background: '#0f1117', color: '#e2e8f0',
      fontFamily: "'IBM Plex Mono','Courier New',monospace" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Space+Mono:wght@700&display=swap');
        *{box-sizing:border-box;margin:0;padding:0}
        ::-webkit-scrollbar{width:4px}
        ::-webkit-scrollbar-thumb{background:#38bdf8;border-radius:2px}
        .catBtn{background:transparent;border:1px solid #1e293b;color:#64748b;
          font-family:'IBM Plex Mono',monospace;font-size:11px;padding:5px 13px;
          cursor:pointer;letter-spacing:1px;transition:all 0.15s;border-radius:2px;white-space:nowrap}
        .catBtn:hover{color:#94a3b8;border-color:#334155}
        .ctrlBtn{background:transparent;border:1px solid #1e293b;color:#64748b;
          font-family:'IBM Plex Mono',monospace;font-size:11px;padding:5px 10px;
          cursor:pointer;letter-spacing:1px;transition:all 0.15s;border-radius:2px}
        .ctrlBtn:hover{color:#94a3b8;border-color:#334155}
        .ctrlBtn.active{border-color:#38bdf8;color:#38bdf8;background:#38bdf810}
        .fd{animation:fd 0.3s ease both}
        @keyframes fd{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
        .refLink{color:inherit;text-decoration:none;border-bottom:1px solid currentColor;
          opacity:0.85;transition:opacity 0.15s}
        .refLink:hover{opacity:1}
        .toolbar{border-bottom:1px solid #1e293b;padding:10px 26px;background:#0a0d14;
          display:flex;flex-direction:column;gap:8px}
        .toolbar-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
        .content{padding:24px 26px;max-width:960px;margin:0 auto}
        .search-input{background:#131720;border:1px solid #1e293b;color:#94a3b8;
          font-family:'IBM Plex Mono',monospace;font-size:13px;
          padding:4px 10px;border-radius:2px;outline:none;width:160px}
        @media(max-width:640px){
          .toolbar{padding:8px 12px}
          .content{padding:14px 12px}
          .search-input{width:100%}
          .catBtn,.ctrlBtn{font-size:12px}
        }
        @media(max-width:400px){
          .toolbar{padding:6px 10px}
          .content{padding:12px 10px}
        }
      `}</style>

      <Header total={totalPapers} loading={loading} lang={lang} onLanguageChange={setLang} />

      <div className="toolbar">
        <div className="toolbar-row">
          <WeekSelector
            weeks={index?.weeks ?? []}
            toDate={toDate}
            fromDate={fromDate}
            onToChange={setToDate}
            onFromChange={setFromDate}
            lang={lang}
          />
          <input
            className="search-input"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t(lang).search}
          />
          <button className={`ctrlBtn${sortByCitations ? ' active' : ''}`}
            onClick={() => setSortByCitations(s => !s)}>
            {sortByCitations ? t(lang).citations : t(lang).date}
          </button>
          <button className={`ctrlBtn${showFavoritesOnly ? ' active' : ''}`}
            onClick={() => setShowFavoritesOnly(s => !s)}>
            {showFavoritesOnly ? '★' : '☆'} {t(lang).favorites}
          </button>
        </div>
        <div className="toolbar-row">
          <CategoryFilter categories={allCategories} active={activeCat} onChange={setActiveCat} lang={lang} />
        </div>
      </div>

      <div className="content">
        {loading && (
          <div style={{ textAlign: 'center', padding: '80px 0', color: '#38bdf8', letterSpacing: 3 }}>
            {t(lang).loading}
          </div>
        )}

        {loadedWeeks.map((week) => {
          const filteredCats = week.categories
            .filter(c => activeCat === 'all' || c.id === activeCat)
            .map(c => ({
              ...c,
              papers: c.papers.filter(p => {
                const id = p.id.split('v')[0]
                if (showFavoritesOnly && !favorites.has(id)) return false
                if (search) {
                  const q = search.toLowerCase()
                  return `${p.title} ${p.titleJa ?? ''} ${p.abstract ?? ''} ${p.abstractJa ?? ''} ${p.task ?? ''} ${p.taskEn ?? ''} ${p.what ?? ''} ${p.whatEn ?? ''}`.toLowerCase().includes(q)
                }
                return true
              }),
            }))
            .map(c => ({
              ...c,
              papers: sortByCitations
                ? [...c.papers].sort((a, b) =>
                    (citationMap[b.id.split('v')[0]] ?? 0) - (citationMap[a.id.split('v')[0]] ?? 0))
                : c.papers,
            }))
            .filter(c => c.papers.length > 0)

          return (
            <div key={week.date} style={{ marginBottom: 56 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16,
                paddingBottom: 10, borderBottom: '1px solid #1e293b' }}>
                <span style={{ fontSize: 13, color: '#38bdf8', fontWeight: 600, letterSpacing: 2 }}>
                  {t(lang).week} {week.date}
                </span>
                <span style={{ fontSize: 11, color: '#334155' }}>
                  {t(lang).papers(week.categories.reduce((s, c) => s + c.papers.length, 0))}
                </span>
              </div>

              {activeCat === 'all' && !showFavoritesOnly && !search && (
                <TrendSummary trend={lang === 'en' ? (week.trendEn || week.trend) : (week.trend || week.trendEn)} lang={lang} />
              )}

              {filteredCats.map((cat, ci) => (
                <div key={cat.id} style={{ marginBottom: 36 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                    <span style={{ fontSize: 15, color: cat.color, fontWeight: 600, letterSpacing: 2 }}>
                      {lang === 'en'
                        ? (allCategories.find(definition => definition.id === cat.id)?.labelEn || cat.labelEn || (cat.id === 'other' ? 'Other' : cat.label))
                        : cat.label}
                    </span>
                    <div style={{ flex: 1, height: 1, background: `${cat.color}25` }} />
                    <span style={{ fontSize: 11, color: '#334155' }}>{t(lang).papers(cat.papers.length)}</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                    {cat.papers.map((paper, pi) => {
                      const id = paper.id.split('v')[0]
                      return (
                        <PaperCard key={paper.id} paper={paper} cat={cat} lang={lang}
                          animDelay={ci * 0.05 + pi * 0.04}
                          citationCount={citationMap[id]}
                          githubUrl={githubMap[id]}
                          isFavorite={favorites.has(id)}
                          onToggleFavorite={() => toggleFavorite(id)}
                          isRead={readPapers.has(id)}
                          onToggleRead={() => toggleRead(id)} />
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )
        })}

        <div ref={sentinelRef} style={{ height: 1 }} />
        {loadingMore && (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#334155', fontSize: 12, letterSpacing: 2 }}>
            {t(lang).loadingOlder}
          </div>
        )}
        {!hasMore && loadedWeeks.length > 0 && (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#1e293b', fontSize: 11, letterSpacing: 2 }}>
            {t(lang).allLoaded}
          </div>
        )}
        <div style={{ marginTop: 22, fontSize: 11, color: '#1e293b', letterSpacing: 1,
          borderTop: '1px solid #1e293b', paddingTop: 14 }}>
          {t(lang).footer}
        </div>
      </div>

      {showScrollTop && (
        <button onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
          style={{ position: 'fixed', bottom: 28, right: 28, zIndex: 100,
            width: 44, height: 44, borderRadius: '50%',
            background: '#131720', border: '1px solid #334155',
            color: '#38bdf8', fontSize: 18, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 4px 12px rgba(0,0,0,0.4)' }}
          title={t(lang).scrollTop}>▴</button>
      )}
    </div>
  )
}
