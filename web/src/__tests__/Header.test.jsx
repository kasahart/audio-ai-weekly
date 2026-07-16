import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Header from '../components/Header'

describe('Header', () => {
  it('renders site title', () => {
    render(<Header total={0} loading={false} />)
    expect(screen.getByText('音響AI週報')).toBeInTheDocument()
  })

  it('shows paper count when not loading and total > 0', () => {
    render(<Header total={42} loading={false} />)
    expect(screen.getByText(/42 論文 表示中/)).toBeInTheDocument()
  })

  it('hides paper count when loading', () => {
    render(<Header total={42} loading={true} />)
    expect(screen.queryByText(/42 論文/)).not.toBeInTheDocument()
  })

  it('hides paper count when total is 0', () => {
    render(<Header total={0} loading={false} />)
    expect(screen.queryByText(/0 論文/)).not.toBeInTheDocument()
  })

  it('renders subtitle text', () => {
    render(<Header total={0} loading={false} />)
    expect(screen.getByText(/音の基盤モデル/)).toBeInTheDocument()
  })

  it('links to the feature archive', () => {
    render(<Header total={0} loading={false} />)
    expect(screen.getByRole('link', { name: /特集アーカイブ/ }))
      .toHaveAttribute('href', './features/')
  })

  it('renders English copy and switches language', () => {
    const onLanguageChange = vi.fn()
    render(<Header total={2} loading={false} lang="en" onLanguageChange={onLanguageChange} />)
    expect(screen.getByText('Audio AI Weekly')).toBeInTheDocument()
    expect(screen.getByText('Showing 2 papers')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Feature archive/ }))
      .toHaveAttribute('href', './features/en/')
    fireEvent.click(screen.getByText('JA'))
    expect(onLanguageChange).toHaveBeenCalledWith('ja')
  })
})
