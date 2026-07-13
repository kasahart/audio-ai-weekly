import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
    expect(screen.getByText(/What is it\?/)).toBeInTheDocument()
  })
})
