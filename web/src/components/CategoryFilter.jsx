import { t } from '../i18n.js'
export default function CategoryFilter({ categories, active, onChange, lang = 'ja' }) {
  const all = [{ id: 'all', label: t(lang).all, labelEn: t(lang).all, color: '#64748b', papers: [] }, ...categories]
  return (
    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
      {all.map(cat => (
        <button key={cat.id} className="catBtn"
          style={active === cat.id ? { borderColor: cat.color, color: cat.color, background: `${cat.color}10` } : {}}
          onClick={() => onChange(cat.id)}>
          {lang === 'en' ? (cat.labelEn || cat.label) : cat.label}
        </button>
      ))}
    </div>
  )
}
