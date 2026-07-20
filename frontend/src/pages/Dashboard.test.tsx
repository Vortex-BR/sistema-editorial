// @vitest-environment jsdom
import {cleanup, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {adminApi} from '../lib/api'
import {Dashboard} from './Dashboard'

vi.mock('../lib/api', () => ({adminApi: vi.fn()}))

const mockAdminApi = vi.mocked(adminApi)
const dashboard = {
  stats: {
    total_projects: 2,
    completed: 1,
    blocked_runs: 1,
    failed_runs: 1,
    cancelled_runs: 1,
    approved_facts: 12,
    distinct_sources: 4,
    total_cost_usd: 2.5,
  },
  recent_projects: [
    {
      id: 'approved-project',
      name: 'Artigo aprovado preservado',
      topic: 'Conteúdo válido anterior',
      status: 'completed',
      last_run_status: 'cancelled',
      current_stage: 'completed',
      created_at: '2026-07-13T10:00:00Z',
    },
    {
      id: 'failed-project',
      name: 'Falha de provedor',
      topic: 'Erro técnico real',
      status: 'failed',
      last_run_status: 'failed',
      current_stage: 'researcher',
      created_at: '2026-07-13T11:00:00Z',
    },
  ],
}

beforeEach(() => {
  mockAdminApi.mockReset()
  mockAdminApi.mockResolvedValue(dashboard)
})

afterEach(cleanup)

describe('Dashboard cancellation semantics', () => {
  it('shows editorial state separately from the last run', async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>)

    const approvedRow = (await screen.findByText('Artigo aprovado preservado')).closest('tr')
    expect(approvedRow).not.toBeNull()
    expect(within(approvedRow!).getByText('Concluído')).toBeTruthy()
    expect(within(approvedRow!).getByText('Cancelado pelo operador')).toBeTruthy()

    const failedRow = screen.getByText('Falha de provedor').closest('tr')
    expect(within(failedRow!).getAllByText('Falha técnica')).toHaveLength(2)
    expect(screen.getByText('Cancelados', {selector: 'span'}).nextElementSibling?.textContent).toBe('1')
    expect(screen.getByText('Falhas técnicas', {selector: 'span'}).nextElementSibling?.textContent).toBe('1')
    expect(screen.getByText('Bloqueios editoriais', {selector: 'span'}).nextElementSibling?.textContent).toBe('1')
  })

  it('filters cancelled runs without treating them as failures', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><Dashboard /></MemoryRouter>)
    await screen.findByText('Artigo aprovado preservado')

    await user.selectOptions(screen.getByLabelText('Filtrar por último run'), 'cancelled')

    await waitFor(() => expect(screen.queryByText('Falha de provedor')).toBeNull())
    expect(screen.getByText('Artigo aprovado preservado')).toBeTruthy()

    await user.selectOptions(screen.getByLabelText('Filtrar por último run'), 'failed')

    expect(screen.getByText('Falha de provedor')).toBeTruthy()
    expect(screen.queryByText('Artigo aprovado preservado')).toBeNull()
  })
})
