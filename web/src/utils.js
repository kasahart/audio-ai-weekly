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

/** Build the text used when sharing a paper (title line + arXiv URL). */
export function buildShareText(paper, lang = 'ja') {
  const title = lang === 'ja' && paper.titleJa ? `${paper.titleJa}\n${paper.title}` : paper.title
  return [title, paper.url].filter(Boolean).join('\n')
}

/** Build an X (Twitter) post intent URL for a paper. */
export function buildXShareUrl(paper, lang = 'ja') {
  const title = lang === 'ja' && paper.titleJa ? paper.titleJa : paper.title
  const p = new URLSearchParams()
  if (title) p.set('text', title)
  if (paper.url) p.set('url', paper.url)
  return `https://x.com/intent/post?${p}`
}

/** Copy text to the clipboard, falling back to execCommand where the async API is unavailable. */
export async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {}
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
