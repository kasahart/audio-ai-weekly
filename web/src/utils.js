/** Remove a leading circled-number prefix such as ①. */
export function stripPrefix(text) {
  return text?.replace(/^[①-⑨]\s*/, '') ?? ''
}

/** Read filter state from URL query parameters. */
export function readUrlState(search = window.location.search) {
  const p = new URLSearchParams(search)
  let storedLanguage = null
  try { storedLanguage = localStorage.getItem(LANGUAGE_STORAGE_KEY) } catch {}
  return {
    lang:              resolveLanguage(p.get('lang'), storedLanguage),
    toDate:            p.get('week') || null,
    fromDate:          p.get('from') || null,
    activeCat:         p.get('cat')  || 'all',
    search:            p.get('q')    || '',
    sortByCitations:   p.get('sort') === '1',
    showFavoritesOnly: p.get('fav')  === '1',
  }
}

/** Convert filter state to URL query parameters. */
export function buildUrlSearch({ lang, toDate, fromDate, activeCat, search, sortByCitations, showFavoritesOnly }) {
  const p = new URLSearchParams()
  if (lang)                p.set('lang', lang)
  if (toDate)              p.set('week', toDate)
  if (fromDate)            p.set('from', fromDate)
  if (activeCat !== 'all') p.set('cat',  activeCat)
  if (search)              p.set('q',    search)
  if (sortByCitations)     p.set('sort', '1')
  if (showFavoritesOnly)   p.set('fav',  '1')
  return p.toString() ? `?${p}` : ''
}
import { LANGUAGE_STORAGE_KEY, resolveLanguage } from './i18n.js'
