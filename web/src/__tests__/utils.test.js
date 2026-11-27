import { describe, it, expect } from 'vitest'
import { stripPrefix, readUrlState, buildUrlSearch, buildShareText, buildXShareUrl } from '../utils.js'
import { browserLanguage, resolveLanguage } from '../i18n.js'

describe('language resolution', () => {
  it('uses the primary browser language when no preference exists', () => {
    expect(browserLanguage(['ja-JP', 'en-US'])).toBe('ja')
    expect(browserLanguage(['en-US', 'ja-JP'])).toBe('en')
  })
  it('prioritizes query, then storage, then browser', () => {
    expect(resolveLanguage('en', 'ja', ['ja-JP'])).toBe('en')
    expect(resolveLanguage(null, 'ja', ['en-US'])).toBe('ja')
    expect(resolveLanguage('invalid', 'invalid', ['ja-JP'])).toBe('ja')
  })
})

describe('stripPrefix', () => {
  it('removes circled number prefix ①', () => {
    expect(stripPrefix('① 概要（抄録ベース）')).toBe('概要（抄録ベース）')
  })
  it('removes circled number ⑨', () => {
    expect(stripPrefix('⑨ テキスト')).toBe('テキスト')
  })
  it('leaves plain text unchanged', () => {
    expect(stripPrefix('プレーンテキスト')).toBe('プレーンテキスト')
  })
  it('returns empty string for empty input', () => {
    expect(stripPrefix('')).toBe('')
  })
  it('handles null/undefined gracefully', () => {
    expect(stripPrefix(null)).toBe('')
    expect(stripPrefix(undefined)).toBe('')
  })
  it('removes prefix with trailing space', () => {
    expect(stripPrefix('② テキスト')).toBe('テキスト')
  })
})

describe('readUrlState', () => {
  it('returns defaults for empty search', () => {
    const state = readUrlState('')
    expect(state.toDate).toBeNull()
    expect(state.fromDate).toBeNull()
    expect(state.activeCat).toBe('all')
    expect(state.search).toBe('')
    expect(state.sortByCitations).toBe(false)
    expect(state.showFavoritesOnly).toBe(false)
  })

  it('parses week param', () => {
    expect(readUrlState('?week=2026-0426').toDate).toBe('2026-0426')
  })

  it('parses from param', () => {
    expect(readUrlState('?week=2026-0426&from=2026-0109').fromDate).toBe('2026-0109')
  })

  it('parses cat param', () => {
    expect(readUrlState('?cat=foundation').activeCat).toBe('foundation')
  })

  it('parses search query', () => {
    expect(readUrlState('?q=separation').search).toBe('separation')
  })

  it('parses sort flag', () => {
    expect(readUrlState('?sort=1').sortByCitations).toBe(true)
    expect(readUrlState('?sort=0').sortByCitations).toBe(false)
  })

  it('parses fav flag', () => {
    expect(readUrlState('?fav=1').showFavoritesOnly).toBe(true)
    expect(readUrlState('').showFavoritesOnly).toBe(false)
  })

  it('uses a valid lang query before stored language', () => {
    localStorage.setItem('arxiv-language', 'ja')
    expect(readUrlState('?lang=en').lang).toBe('en')
  })

  it('ignores an invalid lang query and uses stored language', () => {
    localStorage.setItem('arxiv-language', 'ja')
    expect(readUrlState('?lang=xx').lang).toBe('ja')
  })
})

describe('buildUrlSearch', () => {
  it('returns empty string when all defaults', () => {
    expect(buildUrlSearch({
      toDate: null, fromDate: null, activeCat: 'all',
      search: '', sortByCitations: false, showFavoritesOnly: false,
    })).toBe('')
  })

  it('includes week when toDate is set', () => {
    const result = buildUrlSearch({ toDate: '2026-0426', fromDate: null, activeCat: 'all', search: '', sortByCitations: false, showFavoritesOnly: false })
    expect(result).toContain('week=2026-0426')
  })

  it('includes cat when not all', () => {
    const result = buildUrlSearch({ toDate: null, fromDate: null, activeCat: 'foundation', search: '', sortByCitations: false, showFavoritesOnly: false })
    expect(result).toContain('cat=foundation')
  })

  it('omits cat when all', () => {
    const result = buildUrlSearch({ toDate: '2026-0426', fromDate: null, activeCat: 'all', search: '', sortByCitations: false, showFavoritesOnly: false })
    expect(result).not.toContain('cat=')
  })

  it('roundtrips through readUrlState', () => {
    const original = { lang: 'en', toDate: '2026-0426', fromDate: '2026-0109', activeCat: 'foundation', search: 'separation', sortByCitations: true, showFavoritesOnly: false }
    const search = buildUrlSearch(original)
    const restored = readUrlState(search)
    expect(restored.toDate).toBe(original.toDate)
    expect(restored.fromDate).toBe(original.fromDate)
    expect(restored.activeCat).toBe(original.activeCat)
    expect(restored.search).toBe(original.search)
    expect(restored.sortByCitations).toBe(original.sortByCitations)
    expect(restored.lang).toBe(original.lang)
  })
})

describe('share helpers', () => {
  const p = { title: 'T', titleJa: '訳', url: 'https://arxiv.org/abs/1' }
  it('builds share text per language', () => {
    expect(buildShareText(p, 'ja')).toBe('訳\nT\nhttps://arxiv.org/abs/1')
    expect(buildShareText(p, 'en')).toBe('T\nhttps://arxiv.org/abs/1')
  })
  it('appends non-empty analysis sections, verified related papers and the abstract', () => {
    const full = { ...p, what: '①概要本文', whatEn: 'Overview text', method: '手法本文', abstract: 'Abs', abstractJa: '抄録',
      nextReads: [{ label: 'Unverified', url: 'u0' }, { label: 'Rel', url: 'https://arxiv.org/abs/2', verified: true }] }
    expect(buildShareText(full, 'ja')).toBe([
      '訳\nT\nhttps://arxiv.org/abs/1',
      '1. 概要（抄録ベース）\n概要本文',
      '3. 抄録で説明される技術・手法\n手法本文',
      '6. 検証済みの関連論文候補\n- Rel https://arxiv.org/abs/2',
      'AIによる抄録和訳（未校閲）\n抄録',
    ].join('\n\n'))
    const en = buildShareText(full, 'en')
    expect(en).toContain('1. Overview (abstract-based)\nOverview text')
    expect(en).toContain('Original abstract\nAbs')
  })
  it('builds an X intent URL', () => {
    const u = new URL(buildXShareUrl(p, 'en'))
    expect(u.searchParams.get('text')).toBe('T')
    expect(u.searchParams.get('url')).toBe('https://arxiv.org/abs/1')
  })
})
