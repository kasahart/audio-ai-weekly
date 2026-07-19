import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import App from '../App'

const FEATURE = {
  slug: 'spatial-audio-agents',
  type: 'primer',
  date: '2026-07-18',
  readTimeMinutes: 8,
  sourceCount: 4,
  title: '空間オーディオエージェント入門',
  titleEn: 'A Primer on Spatial Audio Agents',
  dek: '音を聞いて空間を理解するモデルの現在地を解説する。',
  dekEn: 'A concise guide to models that listen and reason about space.',
}

class IntersectionObserverStub {
  observe() {}
  disconnect() {}
}

function jsonResponse(data) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(data) })
}

describe('App feature integration', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('arxiv-language', 'ja')
    window.history.replaceState({}, '', '/')
    vi.stubGlobal('IntersectionObserver', IntersectionObserverStub)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('loads the latest feature independently when the weekly index fails', async () => {
    vi.stubGlobal('fetch', vi.fn(url => {
      if (url === './data/features/index.json') return jsonResponse({ features: [FEATURE] })
      if (url === './data/index.json') return Promise.reject(new Error('weekly unavailable'))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    }))

    render(<App />)

    expect(await screen.findByRole('heading', { name: FEATURE.title })).toBeInTheDocument()
    expect(screen.getByText('AI生成（タイトル・抄録ベース）・人手未校閲')).toBeInTheDocument()
    expect(screen.getByText(/Thank you to arXiv for use of its open access interoperability/)).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('./data/features/index.json')
  })

  it('keeps the spotlight hidden when the feature index is missing', async () => {
    vi.stubGlobal('fetch', vi.fn(url => {
      if (url === './data/features/index.json') {
        return Promise.resolve({ ok: false, status: 404, json: vi.fn() })
      }
      if (url === './data/index.json') return jsonResponse({ weeks: [] })
      return Promise.reject(new Error(`unexpected request: ${url}`))
    }))

    render(<App />)

    await waitFor(() => expect(screen.queryByText('読み込み中...')).not.toBeInTheDocument())
    expect(screen.queryByText(/LATEST FEATURE/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: FEATURE.title })).not.toBeInTheDocument()
  })
})
