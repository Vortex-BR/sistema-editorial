import {FormEvent, ReactNode, useEffect, useRef, useState} from 'react'
import {clearAdminToken, setAdminTokenRequester} from '../lib/api'

type Resolver = (token:string|null)=>void
type PendingRequest = {promise:Promise<string|null>;resolve:Resolver}

export function AdminTokenProvider({children}:{children:ReactNode}){
  const [open,setOpen]=useState(false)
  const [token,setToken]=useState('')
  const pendingRequest=useRef<PendingRequest|null>(null)

  useEffect(()=>{
    setAdminTokenRequester(()=>{
      if(pendingRequest.current) return pendingRequest.current.promise
      let resolveRequest:Resolver=()=>undefined
      const promise=new Promise<string|null>(resolve=>{
        resolveRequest=resolve
      })
      pendingRequest.current={promise,resolve:resolveRequest}
      setToken('')
      setOpen(true)
      return promise
    })
    return ()=>{
      const pending=pendingRequest.current
      pendingRequest.current=null
      pending?.resolve(null)
      setAdminTokenRequester(null)
      clearAdminToken()
    }
  },[])

  function finish(value:string|null){
    const pending=pendingRequest.current
    pendingRequest.current=null
    setToken('')
    setOpen(false)
    pending?.resolve(value)
  }

  function submit(event:FormEvent){
    event.preventDefault()
    if(!token.trim()) return
    finish(token)
  }

  return <>
    {children}
    {open&&<div className="admin-auth-backdrop" role="presentation">
      <form className="admin-auth-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-auth-title" onSubmit={submit}>
        <span className="eyebrow">AÇÃO PROTEGIDA</span>
        <h2 id="admin-auth-title">Token administrativo</h2>
        <p>Informe o token somente para autorizar esta área. Ele será mantido apenas na memória desta página.</p>
        <label>Token<input autoFocus autoComplete="off" type="password" value={token} onChange={event=>setToken(event.target.value)}/></label>
        <div className="admin-auth-actions">
          <button type="button" className="button secondary" onClick={()=>finish(null)}>Cancelar</button>
          <button disabled={!token.trim()} className="button primary">Autorizar</button>
        </div>
      </form>
    </div>}
  </>
}
