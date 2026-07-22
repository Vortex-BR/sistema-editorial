// @vitest-environment jsdom
import {act,cleanup,render,screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {useEffect,useState} from 'react'
import {adminApi,clearAdminToken,setAdminTokenRequester} from '../lib/api'
import {AdminTokenProvider} from './AdminTokenProvider'

let fetchMock:ReturnType<typeof vi.fn>

beforeEach(()=>{
  clearAdminToken()
  setAdminTokenRequester(null)
  fetchMock=vi.fn((url:string|URL|Request)=>Promise.resolve(
    new Response(JSON.stringify({path:String(url)}),{status:200}),
  ))
  vi.stubGlobal('fetch',fetchMock)
})

afterEach(()=>{
  cleanup()
  clearAdminToken()
  setAdminTokenRequester(null)
  vi.unstubAllGlobals()
})

function renderProvider(){
  return render(<AdminTokenProvider><span>Protected content</span></AdminTokenProvider>)
}

describe('AdminTokenProvider concurrency',()=>{
  it('waits for provider registration when a child reads on direct mount',async()=>{
    const user=userEvent.setup()

    function DirectProtectedRead(){
      const [result,setResult]=useState('loading')
      useEffect(()=>{
        adminApi('/dashboard')
          .then(()=>setResult('loaded'))
          .catch(reason=>setResult((reason as Error).message))
      },[])
      return <span>{result}</span>
    }

    render(<AdminTokenProvider><DirectProtectedRead/></AdminTokenProvider>)

    expect(await screen.findByRole('dialog')).toBeTruthy()
    expect(screen.queryByText(/Autorização administrativa necessária/)).toBeNull()
    await user.type(screen.getByLabelText('Token'),'direct-route-token')
    await user.click(screen.getByRole('button',{name:'Autorizar'}))

    expect(await screen.findByText('loaded')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('opens one modal and one submission resolves two concurrent calls',async()=>{
    const user=userEvent.setup()
    renderProvider()
    let resultsPromise!:Promise<unknown[]>

    act(()=>{
      resultsPromise=Promise.all([
        adminApi('/config/credentials'),
        adminApi('/config'),
      ])
    })

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    await user.type(screen.getByLabelText('Token'),'one-shared-token')
    await user.click(screen.getByRole('button',{name:'Autorizar'}))
    await expect(resultsPromise).resolves.toHaveLength(2)

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    for(const [,init] of fetchMock.mock.calls){
      expect(new Headers(init?.headers).get('X-Admin-Token')).toBe(
        'one-shared-token',
      )
    }
  })

  it('cancels every concurrent waiter from the single modal',async()=>{
    const user=userEvent.setup()
    renderProvider()
    let resultsPromise!:Promise<PromiseSettledResult<unknown>[]>

    act(()=>{
      resultsPromise=Promise.allSettled([
        adminApi('/config/credentials'),
        adminApi('/config'),
      ])
    })

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    await user.click(screen.getByRole('button',{name:'Cancelar'}))
    const results=await resultsPromise

    expect(results.map(result=>result.status)).toEqual(['rejected','rejected'])
    for(const result of results){
      if(result.status==='rejected'){
        expect(result.reason).toBeInstanceOf(Error)
        expect(result.reason.message).toContain('administrativa')
      }
    }
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('settles every waiter when the provider unmounts',async()=>{
    const view=renderProvider()
    let resultsPromise!:Promise<PromiseSettledResult<unknown>[]>

    act(()=>{
      resultsPromise=Promise.allSettled([
        adminApi('/config/credentials'),
        adminApi('/config'),
        adminApi('/dashboard'),
      ])
    })

    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    view.unmount()
    const results=await resultsPromise

    expect(results.map(result=>result.status)).toEqual([
      'rejected',
      'rejected',
      'rejected',
    ])
    for(const result of results){
      if(result.status==='rejected'){
        expect(result.reason).toBeInstanceOf(Error)
        expect(result.reason.message).toContain('administrativa')
      }
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
