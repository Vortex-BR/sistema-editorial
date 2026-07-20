import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  adminDownload,
  adminApi,
  api,
  clearAdminToken,
  safePublicMessage,
  safeDownloadFilename,
  setAdminTokenRequester,
} from './api'

function deferred<T>(){
  let resolve!:(value:T|PromiseLike<T>)=>void
  const promise=new Promise<T>(complete=>{resolve=complete})
  return {promise,resolve}
}

afterEach(() => {
  clearAdminToken()
  setAdminTokenRequester(null)
  vi.unstubAllGlobals()
})

describe('safePublicMessage', () => {
  it.each([
    'Traceback (most recent call last)',
    'sqlalchemy.exc.DataError',
    'asyncpg.exceptions.UntranslatableCharacterError',
    'INSERT INTO agent_runs VALUES ($1)',
    'SELECT secret FROM credentials',
    'parameters: {password: hidden}',
    'File "/app/workers/tasks.py", line 1',
  ])('hides technical details: %s', value => {
    const result = safePublicMessage(value)
    expect(result).toContain('detalhes técnicos foram registrados internamente')
    expect(result).not.toContain(value)
  })

  it('preserves a concise public message', () => {
    expect(safePublicMessage('Credencial do provider não configurada.')).toBe(
      'Credencial do provider não configurada.',
    )
  })

  it('turns a FastAPI validation issue into a field-specific message', () => {
    expect(safePublicMessage([{
      type:'string_too_long',
      loc:['body','briefing','additional_context'],
      msg:'String should have at most 20000 characters',
      ctx:{max_length:20_000},
    }])).toBe('Contexto adicional: use no máximo 20.000 caracteres.')
  })

  it('reads a public message from a structured API error', () => {
    expect(safePublicMessage({
      error_code:'EDITORIAL_V3_EXECUTION_DISABLED',
      message:'A execução V3 está desabilitada.',
    })).toBe('A execução V3 está desabilitada. Código: EDITORIAL_V3_EXECUTION_DISABLED.')
  })
})

describe('administrative requests', () => {
  it('keeps public requests free of administrative credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({status:'healthy'}), {status:200}),
    )
    const tokenRequester = vi.fn().mockResolvedValue('unused-secret')
    vi.stubGlobal('fetch', fetchMock)
    setAdminTokenRequester(tokenRequester)

    await api('/health')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).not.toContain('unused-secret')
    expect(new Headers(init.headers).has('X-Admin-Token')).toBe(false)
    expect(tokenRequester).not.toHaveBeenCalled()
  })

  it('sends the token only in the protected request header and never persists it', async () => {
    const token = 'fictional-admin-secret'
    const body = JSON.stringify({agent_role:'researcher',primary_model:'test-model'})
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({saved:true}), {status:200}),
    )
    const localStorageMock = {
      getItem:vi.fn(),
      setItem:vi.fn(),
      removeItem:vi.fn(),
      clear:vi.fn(),
      key:vi.fn(),
      length:0,
    }
    const sessionStorageMock = {
      getItem:vi.fn(),
      setItem:vi.fn(),
      removeItem:vi.fn(),
      clear:vi.fn(),
      key:vi.fn(),
      length:0,
    }
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('localStorage', localStorageMock)
    vi.stubGlobal('sessionStorage', sessionStorageMock)
    const tokenRequester=vi.fn().mockResolvedValue(token)
    setAdminTokenRequester(tokenRequester)

    await adminApi('/config/routes/researcher', {method:'PUT', body})

    const [url, init] = fetchMock.mock.calls[0]
    const headers = new Headers(init.headers)
    expect(url).not.toContain(token)
    expect(init.body).toBe(body)
    expect(String(init.body)).not.toContain(token)
    expect(headers.get('X-Admin-Token')).toBe(token)
    expect(tokenRequester).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(localStorageMock.setItem).not.toHaveBeenCalled()
    expect(sessionStorageMock.setItem).not.toHaveBeenCalled()
  })

  it('shares one token request across five concurrent administrative calls', async () => {
    const pendingToken=deferred<string|null>()
    const tokenRequester=vi.fn(()=>pendingToken.promise)
    const fetchMock=vi.fn((url:string|URL|Request,_init?:RequestInit)=>Promise.resolve(
      new Response(JSON.stringify({url:String(url)}), {status:200}),
    ))
    vi.stubGlobal('fetch', fetchMock)
    setAdminTokenRequester(tokenRequester)

    const requests=Array.from({length:5},(_,index)=>adminApi(`/concurrent/${index}`))
    const resultsPromise=Promise.all(requests)

    await vi.waitFor(()=>expect(tokenRequester).toHaveBeenCalledTimes(1))
    expect(fetchMock).not.toHaveBeenCalled()
    pendingToken.resolve('shared-five-call-token')
    const results=await resultsPromise

    expect(results).toHaveLength(5)
    expect(fetchMock).toHaveBeenCalledTimes(5)
    for(const [,init] of fetchMock.mock.calls){
      expect(new Headers(init?.headers).get('X-Admin-Token')).toBe(
        'shared-five-call-token',
      )
    }
  })

  it('clears a rejected token and requests a replacement', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({detail:'Acesso administrativo não autorizado.'}),
          {status:401},
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({saved:true}), {status:200}),
      )
    const tokenRequester = vi.fn()
      .mockResolvedValueOnce('rejected-token')
      .mockResolvedValueOnce('replacement-token')
    vi.stubGlobal('fetch', fetchMock)
    setAdminTokenRequester(tokenRequester)

    await adminApi('/config/routes/researcher', {method:'PUT', body:'{}'})

    expect(tokenRequester).toHaveBeenCalledTimes(2)
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('X-Admin-Token')).toBe('rejected-token')
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get('X-Admin-Token')).toBe('replacement-token')
  })

  it('single-flights reauthentication for two concurrent 401 responses', async () => {
    const delayedUnauthorized=deferred<Response>()
    const replacementToken=deferred<string|null>()
    const fetchMock=vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({detail:'unauthorized'}),{status:401}))
      .mockImplementationOnce(()=>delayedUnauthorized.promise)
      .mockResolvedValueOnce(new Response(JSON.stringify({request:'first'}),{status:200}))
      .mockResolvedValueOnce(new Response(JSON.stringify({request:'second'}),{status:200}))
    const tokenRequester=vi.fn()
      .mockResolvedValueOnce('rejected-shared-token')
      .mockImplementationOnce(()=>replacementToken.promise)
    vi.stubGlobal('fetch',fetchMock)
    setAdminTokenRequester(tokenRequester)

    const requests=Promise.all([
      adminApi<{request:string}>('/concurrent/first'),
      adminApi<{request:string}>('/concurrent/second'),
    ])

    await vi.waitFor(()=>expect(tokenRequester).toHaveBeenCalledTimes(2))
    replacementToken.resolve('replacement-shared-token')
    await vi.waitFor(()=>expect(fetchMock).toHaveBeenCalledTimes(3))
    delayedUnauthorized.resolve(
      new Response(JSON.stringify({detail:'unauthorized'}),{status:401}),
    )
    const results=await requests

    expect(results).toEqual([{request:'first'},{request:'second'}])
    expect(tokenRequester).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenCalledTimes(4)
    for(const call of fetchMock.mock.calls.slice(2)){
      expect(new Headers(call[1]?.headers).get('X-Admin-Token')).toBe(
        'replacement-shared-token',
      )
    }
  })

  it('does not request a third token when the retry also returns 401', async () => {
    const fetchMock=vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({detail:'unauthorized'}),{status:401}))
      .mockResolvedValueOnce(new Response(JSON.stringify({detail:'still unauthorized'}),{status:401}))
    const tokenRequester=vi.fn()
      .mockResolvedValueOnce('first-rejected-token')
      .mockResolvedValueOnce('second-rejected-token')
    vi.stubGlobal('fetch',fetchMock)
    setAdminTokenRequester(tokenRequester)

    await expect(adminApi('/retry-once')).rejects.toThrow('still unauthorized')

    expect(tokenRequester).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('downloads a blob with the admin token only in the header', async () => {
    const token = 'fictional-export-token'
    const payload = new Blob(['valid zip bytes'], {type:'application/zip'})
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(payload, {
        status:200,
        headers:{'Content-Disposition':'attachment; filename="projeto-v2-20260713.zip"'},
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    setAdminTokenRequester(async () => token)

    const result = await adminDownload('/projects/project-1/export')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/v1/projects/project-1/export')
    expect(url).not.toContain(token)
    expect(init.method).toBe('GET')
    expect(new Headers(init.headers).get('X-Admin-Token')).toBe(token)
    expect(result.filename).toBe('projeto-v2-20260713.zip')
    expect(result.blob.type).toBe('application/zip')
  })

  it('sanitizes an unsafe content-disposition filename', () => {
    expect(safeDownloadFilename('attachment; filename="../../evil.zip"')).toBe('evil.zip')
    expect(safeDownloadFilename('attachment; filename="not-a-zip.exe"')).toBe('pacote-editorial.zip')
  })
})
