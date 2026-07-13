export const DEFAULT_LANGUAGE = 'en'
export const SUPPORTED_LANGUAGES = ['ja', 'en']
export const LANGUAGE_STORAGE_KEY = 'arxiv-language'

const messages = {
  ja: {
    siteTitle: '音響AI週報',
    subtitle: '音の基盤モデル・音源分離・異音検知',
    showingPapers: n => `${n} 論文 表示中`,
    period: '期間:', allPeriod: '全期間', count: n => `${n}件`, all: 'すべて',
    search: 'キーワード検索...', citations: '引用数順', date: '日付順',
    favorites: 'お気に入り', papers: n => `${n} 論文`,
    trendTitle: '◈ 今週の技術トレンド（3行まとめ）',
    loading: '読み込み中...', loadingOlder: '過去の週を読み込み中...', allLoaded: '- 全期間を読み込みました -',
    footer: '音響AI週報 - arXiv cs.SD / eess.AS - GitHub Models (GPT-4o) 使用 - 毎週金曜更新',
    read: '既読', unread: '未読', markRead: '既読にする', markUnread: '未読に戻す',
    addFavorite: 'お気に入りに追加', removeFavorite: 'お気に入り解除', scrollTop: 'トップへ戻る',
    abstract: '要旨', cited: n => `引用 ${n}`, week: '週',
    sections: ['どんなもの？', '先行研究より優れた点', '技術・手法のキモ', '有効性の検証', '議論・限界', '次に読むべき論文'],
    pageTitle: '音響AI週報',
  },
  en: {
    siteTitle: 'Audio AI Weekly',
    subtitle: 'Audio foundation models, source separation, and anomalous sound detection',
    showingPapers: n => `Showing ${n} papers`,
    period: 'Period:', allPeriod: 'All time', count: n => `${n} papers`, all: 'All',
    search: 'Search keywords...', citations: 'Most cited', date: 'Newest',
    favorites: 'Favorites', papers: n => `${n} papers`,
    trendTitle: "◈ This week's technical trends (3 highlights)",
    loading: 'Loading...', loadingOlder: 'Loading older weeks...', allLoaded: '- all weeks loaded -',
    footer: 'Audio AI Weekly - arXiv cs.SD / eess.AS - POWERED BY GitHub Models (GPT-4o) - UPDATED FRIDAYS',
    read: 'Read', unread: 'Unread', markRead: 'Mark as read', markUnread: 'Mark as unread',
    addFavorite: 'Add to favorites', removeFavorite: 'Remove from favorites', scrollTop: 'Back to top',
    abstract: 'Abstract', cited: n => `cited ${n}`, week: 'WEEK',
    sections: ['What is it?', 'Advantages over prior work', 'Technical core', 'Validation', 'Discussion and limitations', 'What to read next'],
    pageTitle: 'Audio AI Weekly',
  },
}

export function isLanguage(value) { return SUPPORTED_LANGUAGES.includes(value) }
export function browserLanguage(languages = globalThis.navigator?.languages ?? [globalThis.navigator?.language]) {
  return languages.filter(Boolean)[0]?.toLowerCase().startsWith('ja') ? 'ja' : DEFAULT_LANGUAGE
}
export function resolveLanguage(queryValue, storedValue, languages) {
  if (isLanguage(queryValue)) return queryValue
  if (isLanguage(storedValue)) return storedValue
  return browserLanguage(languages)
}
export function t(lang) { return messages[isLanguage(lang) ? lang : DEFAULT_LANGUAGE] }
export function localized(paper, key, lang, fallbackKey = key) {
  if (lang === 'en') return paper[`${key}En`] || paper[fallbackKey] || ''
  return paper[key] || paper[`${key}En`] || paper[fallbackKey] || ''
}
