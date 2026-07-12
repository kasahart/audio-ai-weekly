import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CategoryFilter from '../components/CategoryFilter'

const CATS = [
  { id: 'foundation', label: '音の基盤モデル', color: '#38bdf8', papers: [] },
  { id: 'separation',  label: '音源分離',       color: '#4ade80', papers: [] },
  { id: 'anomaly',     label: '異音検知',        color: '#fb923c', papers: [] },
]

describe('CategoryFilter', () => {
  it('renders すべて button', () => {
    render(<CategoryFilter categories={CATS} active="all" onChange={() => {}} />)
    expect(screen.getByText('すべて')).toBeInTheDocument()
  })

  it('renders all category buttons', () => {
    render(<CategoryFilter categories={CATS} active="all" onChange={() => {}} />)
    expect(screen.getByText('音の基盤モデル')).toBeInTheDocument()
    expect(screen.getByText('音源分離')).toBeInTheDocument()
    expect(screen.getByText('異音検知')).toBeInTheDocument()
  })

  it('calls onChange with correct id when clicked', () => {
    const onChange = vi.fn()
    render(<CategoryFilter categories={CATS} active="all" onChange={onChange} />)
    fireEvent.click(screen.getByText('音源分離'))
    expect(onChange).toHaveBeenCalledWith('separation')
  })

  it('calls onChange with all when すべて clicked', () => {
    const onChange = vi.fn()
    render(<CategoryFilter categories={CATS} active="foundation" onChange={onChange} />)
    fireEvent.click(screen.getByText('すべて'))
    expect(onChange).toHaveBeenCalledWith('all')
  })

  it('renders nothing for empty categories', () => {
    const { container } = render(<CategoryFilter categories={[]} active="all" onChange={() => {}} />)
    expect(container.querySelectorAll('button')).toHaveLength(1) // Only the "All" button.
  })
})
