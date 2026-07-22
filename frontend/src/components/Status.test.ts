import {describe, expect, it} from 'vitest'
import {statusLabel} from './Status'

describe('statusLabel', () => {
  it('describes a generic blocked run as an editorial block, not a policy block', () => {
    expect(statusLabel('blocked')).toBe('Bloqueio editorial')
    expect(statusLabel('blocked').toLowerCase()).not.toContain('política')
  })

  it('translates a successful agent run instead of exposing the internal enum', () => {
    expect(statusLabel('succeeded')).toBe('Sucesso')
  })

  it('shows an invalid provider output corrected by the pipeline as recovered', () => {
    expect(statusLabel('recovered')).toBe('Recuperado')
  })
})
