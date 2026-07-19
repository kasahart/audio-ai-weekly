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
    footer: '音響AI週報 - arXiv cs.SD / eess.AS - AIによる分類・翻訳を含みます - 毎週金曜更新',
    read: '既読', unread: '未読', markRead: '既読にする', markUnread: '未読に戻す',
    addFavorite: 'お気に入りに追加', removeFavorite: 'お気に入り解除', scrollTop: 'トップへ戻る',
    abstract: 'AIによる抄録和訳（未校閲）', cited: n => `引用 ${n}`, week: '週',
    latestFeature: 'LATEST FEATURE / 最新特集', featureArchive: '特集アーカイブ',
    featureTypes: { primer: '分野を解く', debate: '論点を読む' },
    readTime: n => `読了 ${n}分`, sourceCount: n => `出典 ${n}件`, readFeature: '特集を読む',
    weeklyDisclosure: 'AI生成（タイトル・抄録ベース）・人手未校閲',
    weeklyCaution: '誤訳、誤要約、過度な一般化を含む可能性があります。研究上の判断は原論文で確認してください。',
    featureDisclosure: 'AI生成（タイトル・抄録ベース）・出典と翻訳の機械的整合性チェック済み・人手未校閲',
    arxivAcknowledgement: 'Thank you to arXiv for use of its open access interoperability. This service was not reviewed or approved by, nor does it necessarily express or reflect the policies or opinions of, arXiv.',
    arxivAcknowledgementLabel: 'arXiv公式英文（原文）',
    primaryNavigation: 'メインナビゲーション',
    sections: ['概要（抄録ベース）', '著者が主張する新規性・差分（抄録ベース）', '抄録で説明される技術・手法', '抄録に記載された検証', '抄録から読み取れる注意点（推定を含む）', '検証済みの関連論文候補'],
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
    footer: 'Audio AI Weekly - arXiv cs.SD / eess.AS - Includes AI-assisted classification and translation - UPDATED FRIDAYS',
    read: 'Read', unread: 'Unread', markRead: 'Mark as read', markUnread: 'Mark as unread',
    addFavorite: 'Add to favorites', removeFavorite: 'Remove from favorites', scrollTop: 'Back to top',
    abstract: 'Original abstract', cited: n => `cited ${n}`, week: 'WEEK',
    latestFeature: 'LATEST FEATURE', featureArchive: 'Feature archive',
    featureTypes: { primer: 'Field Primer', debate: 'Debate Brief' },
    readTime: n => `${n} min read`, sourceCount: n => `${n} sources`, readFeature: 'Read feature',
    weeklyDisclosure: 'AI-generated from titles and abstracts · not human-reviewed',
    weeklyCaution: 'May contain mistranslations, inaccurate summaries, or overgeneralizations; consult the original paper for research decisions.',
    featureDisclosure: 'AI-generated from titles and abstracts · machine-checked for source and translation consistency · not human-reviewed',
    arxivAcknowledgement: 'Thank you to arXiv for use of its open access interoperability. This service was not reviewed or approved by, nor does it necessarily express or reflect the policies or opinions of, arXiv.',
    arxivAcknowledgementLabel: 'Official arXiv statement',
    primaryNavigation: 'Primary navigation',
    sections: ['Overview (abstract-based)', 'Author-claimed novelty and differences (abstract-based)', 'Method described in the abstract', 'Validation reported in the abstract', 'Cautions inferred from the abstract', 'Verified related-paper candidates'],
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
