import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import FeatureSpotlight from '../components/FeatureSpotlight'

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

describe('FeatureSpotlight', () => {
  it('renders the Japanese feature metadata and links', () => {
    render(<FeatureSpotlight feature={FEATURE} lang="ja" />)

    expect(screen.getByRole('heading', { name: FEATURE.title })).toBeInTheDocument()
    expect(screen.getByText(FEATURE.dek)).toBeInTheDocument()
    expect(screen.getByText('分野を解く')).toBeInTheDocument()
    expect(screen.getByText('読了 8分')).toBeInTheDocument()
    expect(screen.getByText('出典 4件')).toBeInTheDocument()
    expect(screen.getByText('AI生成・自動検証')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: `特集を読む: ${FEATURE.title}` }))
      .toHaveAttribute('href', './features/spatial-audio-agents/')
    expect(screen.getByRole('link', { name: /特集アーカイブ/ }))
      .toHaveAttribute('href', './features/')
  })

  it('renders English copy and links directly to the English summary', () => {
    render(<FeatureSpotlight feature={FEATURE} lang="en" />)

    expect(screen.getByRole('heading', { name: FEATURE.titleEn })).toBeInTheDocument()
    expect(screen.getByText(FEATURE.dekEn)).toBeInTheDocument()
    expect(screen.queryByText(FEATURE.dek)).not.toBeInTheDocument()
    expect(screen.getByText('Field Primer')).toBeInTheDocument()
    expect(screen.getByText('8 min read')).toBeInTheDocument()
    expect(screen.getByText('4 sources')).toBeInTheDocument()
    expect(screen.getByText('AI-generated · auto-verified')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: `Read feature: ${FEATURE.titleEn}` }))
      .toHaveAttribute('href', './features/spatial-audio-agents/#english-summary')
  })

  it('renders nothing without a feature', () => {
    const { container } = render(<FeatureSpotlight feature={null} lang="ja" />)
    expect(container).toBeEmptyDOMElement()
  })
})
