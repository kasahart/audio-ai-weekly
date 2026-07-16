import { t } from '../i18n.js'

export default function Header({ total, loading, lang = 'ja', onLanguageChange }) {
  const copy = t(lang)
  const featureArchiveHref = lang === 'en' ? './features/en/' : './features/'
  return (
    <div style={{ position: 'relative', borderBottom: '1px solid #1e293b', background: '#0a0d14',
      padding: 'clamp(12px,3vw,18px) clamp(12px,4vw,26px) 14px' }}>
      <div style={{ fontSize: 10, color: '#38bdf8', letterSpacing: 4, opacity: 0.7, marginBottom: 6 }}>
        ARXIV MONITOR / CS.SD - EESS.AS
      </div>
      <div style={{ fontFamily: "'Space Mono',monospace", fontWeight: 700,
        color: '#f1f5f9', letterSpacing: -0.5, lineHeight: 1.4,
        fontSize: 'clamp(15px,4vw,21px)' }}>
        {copy.siteTitle}
        <span style={{ fontSize: 'clamp(10px,2.5vw,12px)', color: '#475569',
          fontWeight: 400, marginLeft: 10 }}>
          {copy.subtitle}
        </span>
      </div>
      {!loading && total > 0 && (
        <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>
          {copy.showingPapers(total)}
        </div>
      )}
      <nav aria-label={copy.primaryNavigation} style={{ marginTop: 6 }}>
        <a href={featureArchiveHref} style={{ color: '#f472b6', fontSize: 11, letterSpacing: 1,
          textDecoration: 'none', borderBottom: '1px solid #f472b680', display: 'inline-flex',
          alignItems: 'center', minHeight: 28 }}>
          {copy.featureArchive} <span aria-hidden="true">→</span>
        </a>
      </nav>
      <div aria-label="Language" style={{ position: 'absolute', top: 16, right: 20, fontSize: 11 }}>
        {['ja', 'en'].map((value, index) => <span key={value}>
          {index > 0 && <span style={{ color: '#334155', margin: '0 6px' }}>/</span>}
          <button onClick={() => onLanguageChange?.(value)} aria-pressed={lang === value}
            style={{ background: 'none', border: 0, cursor: 'pointer', fontFamily: 'inherit',
              color: lang === value ? '#38bdf8' : '#475569', fontWeight: lang === value ? 700 : 400 }}>
            {value.toUpperCase()}
          </button>
        </span>)}
      </div>
    </div>
  )
}
