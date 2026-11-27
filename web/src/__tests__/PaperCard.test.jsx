import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PaperCard from '../components/PaperCard'

const cat = { id: 'foundation', label: '音の基盤モデル', labelEn: 'Audio Foundation Models', color: '#38bdf8' }
const paper = {
  id: '2601.00001', date: '2026-01-01', title: 'Original English Title', titleJa: '日本語の論文名訳',
  abstract: 'Original English abstract.', abstractJa: '日本語の要旨。', what: '日本語の解説。', whatEn: 'English explanation.',
  url: 'https://arxiv.org/abs/2601.00001', nextReads: [],
}

describe('PaperCard localization', () => {
  it('shows the original and translated title in Japanese', () => {
    render(<PaperCard paper={paper} cat={cat} lang="ja" />)
    expect(screen.getByText('Original English Title')).toBeInTheDocument()
    expect(screen.getByText('日本語の論文名訳')).toBeInTheDocument()
    expect(screen.getByText('日本語の解説。')).toBeInTheDocument()
  })

  it('uses English analysis and original abstract in English', () => {
    render(<PaperCard paper={paper} cat={cat} lang="en" />)
    expect(screen.queryByText('日本語の論文名訳')).not.toBeInTheDocument()
    expect(screen.getByText('English explanation.')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Original English Title'))
    expect(screen.getByText('Original English abstract.')).toBeInTheDocument()
    expect(screen.getByText(/Overview \(abstract-based\)/)).toBeInTheDocument()
    expect(screen.getByText('Original abstract')).toBeInTheDocument()
  })

  it('shows only sourced affiliations and verified related papers', () => {
    const trustPaper = {
      ...paper,
      org: 'Untrusted legacy value',
      nextReads: [
        { label: 'Legacy candidate', url: 'https://arxiv.org/abs/2401.00001' },
        { label: 'Verified candidate', url: 'https://arxiv.org/abs/2401.00002', verified: true },
      ],
    }
    render(<PaperCard paper={trustPaper} cat={cat} lang="en" />)
    fireEvent.click(screen.getByText('Original English Title'))
    expect(screen.queryByText('Untrusted legacy value')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy candidate')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Verified candidate' })).toBeInTheDocument()
  })
})

describe('PaperCard sharing', () => {
  it('links to an X post intent with the title and arXiv URL', () => {
    render(<PaperCard paper={paper} cat={cat} lang="ja" />)
    const href = screen.getByRole('link', { name: 'Xでポスト' }).getAttribute('href')
    const u = new URL(href)
    expect(u.origin + u.pathname).toBe('https://x.com/intent/post')
    expect(u.searchParams.get('text')).toBe('日本語の論文名訳')
    expect(u.searchParams.get('url')).toBe('https://arxiv.org/abs/2601.00001')
  })

  it('copies the title, URL, analysis and abstract without expanding the card', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<PaperCard paper={paper} cat={cat} lang="en" />)
    fireEvent.click(screen.getByRole('button', { name: 'Copy title, URL, analysis and abstract' }))
    await waitFor(() => expect(screen.getByText('Copied')).toBeInTheDocument())
    expect(writeText).toHaveBeenCalledWith([
      'Original English Title\nhttps://arxiv.org/abs/2601.00001',
      '1. Overview (abstract-based)\nEnglish explanation.',
      'Original abstract\nOriginal English abstract.',
    ].join('\n\n'))
    expect(screen.queryByText('Original English abstract.')).not.toBeInTheDocument()
  })
})
