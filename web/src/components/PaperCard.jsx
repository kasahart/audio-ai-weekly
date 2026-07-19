import { useState } from 'react'
import { stripPrefix } from '../utils.js'
import { localized, t } from '../i18n.js'

const SECTIONS = [
  { key: 'what', icon: '1.', color: '#cbd5e1' }, { key: 'novel', icon: '2.', color: '#38bdf8' },
  { key: 'method', icon: '3.', color: '#a78bfa' }, { key: 'validation', icon: '4.', color: '#4ade80' },
  { key: 'discussion', icon: '5.', color: '#fb923c' }, { key: 'nextReads', icon: '6.', color: '#f472b6' },
]


function Badge({ href, onClick, color, bg, children }) {
  const style = {
    fontSize: 11, padding: '2px 7px', borderRadius: 2,
    border: `1px solid ${color}`, color, background: bg ?? 'transparent',
    fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap',
  }
  return href
    ? <a href={href} target="_blank" rel="noreferrer" onClick={onClick} style={style}>{children}</a>
    : <span style={style}>{children}</span>
}

export default function PaperCard({ paper, cat, lang = 'ja', animDelay = 0, citationCount, githubUrl, isFavorite, onToggleFavorite, isRead, onToggleRead }) {
  const [expanded, setExpanded] = useState(false)
  const copy = t(lang)

  // Prefer the frontend-fetched githubUrl, then fall back to githubRepo from JSON.
  const codeUrl = githubUrl || paper.githubRepo
  const demoUrl = paper.projectPage

  return (
    <div className="fd" style={{
      background: '#111520',
      borderTop: `1px solid ${expanded ? cat.color + '70' : cat.color + '40'}`,
      borderRight: `1px solid ${expanded ? cat.color + '70' : cat.color + '40'}`,
      borderBottom: `1px solid ${expanded ? cat.color + '70' : cat.color + '40'}`,
      borderLeft: `4px solid ${cat.color}`,
      borderRadius: 4,
      boxShadow: expanded ? `0 0 20px rgba(0,0,0,0.3)` : 'none',
      animationDelay: `${animDelay}s`,
      opacity: isRead && !expanded ? 0.45 : 1,
      transition: 'opacity 0.2s',
    }}>
      <div onClick={() => setExpanded(e => !e)}
        style={{ padding: 'clamp(10px,3vw,13px) clamp(10px,3vw,16px) 11px', cursor: 'pointer', userSelect: 'none' }}>

        {/* Badge row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 7, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, padding: '2px 10px', background: cat.color,
            color: '#080c14', fontWeight: 700, letterSpacing: 1, borderRadius: 2, flexShrink: 0 }}>
            {paper.date}
          </span>
          {localized(paper, 'task', lang) && (
            <Badge color='#a78bfa' bg='#a78bfa10'>{localized(paper, 'task', lang)}</Badge>
          )}
          {paper.proposedMethod && (
            <Badge color='#38bdf8' bg='#38bdf810'>{paper.proposedMethod}</Badge>
          )}
          {codeUrl && (
            <Badge href={codeUrl} onClick={e => e.stopPropagation()} color='#4ade80' bg='#4ade8010'>Code</Badge>
          )}
          {demoUrl && (
            <Badge href={demoUrl} onClick={e => e.stopPropagation()} color='#fb923c' bg='#fb923c10'>Demo</Badge>
          )}
          {paper.upvotes != null && paper.upvotes > 0 && (
            <Badge color='#f59e0b'>HF ▲{paper.upvotes}</Badge>
          )}
          {citationCount != null && (
            <Badge color='#64748b'>{copy.cited(citationCount)}</Badge>
          )}
          {(paper.comment || paper.journalRef) && (
            <Badge color='#94a3b8'>{paper.journalRef || paper.comment?.slice(0, 40)}</Badge>
          )}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              onClick={e => { e.stopPropagation(); onToggleRead?.() }}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                fontSize: 13, lineHeight: 1, color: isRead ? '#4ade80' : '#334155',
                transition: 'color 0.15s', fontFamily: 'inherit' }}
              title={isRead ? copy.markUnread : copy.markRead}
            >{isRead ? copy.read : copy.unread}</button>
            <button
              onClick={e => { e.stopPropagation(); onToggleFavorite?.() }}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                fontSize: 16, lineHeight: 1, color: isFavorite ? '#f59e0b' : '#334155',
                transition: 'color 0.15s' }}
              title={isFavorite ? copy.removeFavorite : copy.addFavorite}
            >{isFavorite ? '★' : '☆'}</button>
            <a href={paper.url} target="_blank" rel="noreferrer"
              onClick={e => e.stopPropagation()}
              style={{ fontSize: 11, color: '#475569', textDecoration: 'none' }}>arXiv</a>
            <span style={{ fontSize: 14, color: expanded ? cat.color : '#334155', transition: 'color 0.15s' }}>
              {expanded ? '▴' : '▾'}
            </span>
          </div>
        </div>

        {/* Title and author row */}
        {paper.orgSource === 'arxiv_affiliation' && paper.org && (
          <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>{paper.org}</div>
        )}
        <div style={{ fontSize: 'clamp(13px,3.5vw,15px)', color: '#e2e8f0', lineHeight: 1.6, fontWeight: 500 }}>{paper.title}</div>
        {lang === 'ja' && <div style={{ fontSize: 'clamp(12px,3vw,14px)', color: '#94a3b8', lineHeight: 1.6, marginTop: 3 }}>{paper.titleJa || paper.title}</div>}
        {paper.authors?.length > 0 && (
          <div style={{ fontSize: 11, color: '#475569', lineHeight: 1.6, marginTop: 4 }}>
            {paper.authors.join(', ')}
          </div>
        )}
        {!expanded && localized(paper, 'what', lang) && (
          <div style={{ fontSize: 13, color: '#64748b', lineHeight: 1.8, marginTop: 8,
            paddingLeft: 8, borderLeft: '2px solid #1e293b' }}>
            {stripPrefix(localized(paper, 'what', lang))}
          </div>
        )}
      </div>

      {expanded && (
        <div style={{ borderTop: `1px solid ${cat.color}18`, animation: 'fd 0.2s ease both' }}>

          {/* Datasets and arXiv categories */}
          {(paper.datasets?.length > 0 || paper.categories?.length > 0) && (
            <div style={{ padding: '10px 18px', borderBottom: `1px solid ${cat.color}10`,
              display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              {paper.datasets?.map((d, i) => (
                <span key={i} style={{ fontSize: 11, padding: '1px 6px', borderRadius: 2,
                  border: '1px solid #1e293b', color: '#64748b' }}>{d}</span>
              ))}
              {paper.categories?.slice(0, 4).map((c, i) => (
                <span key={i} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 2,
                  border: '1px solid #1e293b', color: '#334155' }}>{c}</span>
              ))}
            </div>
          )}

          {SECTIONS.map((sm, si) => (
            <div key={sm.key} style={{
              borderTop: si === 0 ? 'none' : `1px solid ${cat.color}10`,
              padding: '11px 18px',
            }}>
              <div style={{ fontSize: 11, color: sm.color, fontWeight: 600, letterSpacing: 1.5, marginBottom: 6 }}>
                {sm.icon} {copy.sections[si]}
              </div>
              {sm.key === 'nextReads' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {(paper.nextReads ?? []).filter(r => r?.verified === true).map((r, i) => (
                    <div key={i} style={{ fontSize: 13, lineHeight: 1.7 }}>
                      <span style={{ color: sm.color, marginRight: 5, opacity: 0.6 }}>-</span>
                      {r.url
                        ? <a href={r.url} target="_blank" rel="noreferrer" className="refLink" style={{ color: sm.color }}>{r.label}</a>
                        : <span style={{ color: '#94a3b8' }}>{r.label}</span>}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.9,
                  paddingLeft: 8, borderLeft: `2px solid ${sm.color}40` }}>
                  {stripPrefix(localized(paper, sm.key, lang))}
                </div>
              )}
            </div>
          ))}

          {(lang === 'ja' ? (paper.abstractJa || paper.abstract) : (paper.abstract || paper.abstractJa)) && (
            <div style={{ borderTop: `1px solid ${cat.color}10`, padding: '11px 18px' }}>
              <div style={{ fontSize: 11, color: '#475569', fontWeight: 600, letterSpacing: 1.5, marginBottom: 6 }}>
                {copy.abstract}
              </div>
              <div style={{ fontSize: 13, color: '#94a3b8', lineHeight: 1.9,
                paddingLeft: 8, borderLeft: '2px solid #38bdf840' }}>
                {lang === 'ja' ? (paper.abstractJa || paper.abstract) : (paper.abstract || paper.abstractJa)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
