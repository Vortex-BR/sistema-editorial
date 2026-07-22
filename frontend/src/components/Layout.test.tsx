// @vitest-environment jsdom
import {cleanup, render, screen} from '@testing-library/react'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {getReadiness} from '../lib/api'
import {Layout} from './Layout'

vi.mock('../lib/api',async importOriginal=>{
  const actual=await importOriginal<typeof import('../lib/api')>()
  return {...actual,getReadiness:vi.fn()}
})

const mockGetReadiness=vi.mocked(getReadiness)

function renderLayout(){
  return render(
    <MemoryRouter>
      <Routes>
        <Route element={<Layout/>}>
          <Route index element={<div>Conteúdo</div>}/>
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(()=>vi.clearAllMocks())
afterEach(cleanup)

describe('Layout operational status',()=>{
  it('shows operational only after the readiness endpoint confirms every component',async()=>{
    mockGetReadiness.mockResolvedValue({status:'ready',components:{worker:{status:'ready'}}})

    renderLayout()

    expect(await screen.findAllByText('Operacional')).toHaveLength(2)
    expect(screen.queryByText('Saudável')).toBeNull()
  })

  it('does not display a false healthy state while a critical component is missing',async()=>{
    mockGetReadiness.mockResolvedValue({
      status:'not_ready',
      components:{worker:{status:'missing'},beat:{status:'ready'}},
    })

    renderLayout()

    expect(await screen.findAllByText('Atenção')).toHaveLength(2)
    expect(screen.getByText(/worker: ausente/)).toBeTruthy()
    expect(screen.queryByText('Saudável')).toBeNull()
  })
})
