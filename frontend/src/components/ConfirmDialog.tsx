import {useEffect,useRef} from 'react'

type ConfirmDialogProps = {
  open:boolean
  title:string
  description:string
  confirmLabel:string
  busy?:boolean
  danger?:boolean
  onCancel:()=>void
  onConfirm:()=>void
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  busy=false,
  danger=false,
  onCancel,
  onConfirm,
}:ConfirmDialogProps){
  const dialogRef=useRef<HTMLElement>(null)
  useEffect(()=>{
    if(!open) return
    const onKeyDown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'&&!busy) onCancel()
      if(event.key==='Tab'){
        const focusable=dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled)')
        if(!focusable?.length) return
        const first=focusable[0]
        const last=focusable[focusable.length-1]
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
      }
    }
    window.addEventListener('keydown',onKeyDown)
    return ()=>window.removeEventListener('keydown',onKeyDown)
  },[busy,onCancel,open])

  if(!open) return null
  return <div className="admin-auth-backdrop" role="presentation">
    <section ref={dialogRef} className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description">
      <span className="eyebrow">CONFIRME A DECISÃO</span>
      <h2 id="confirm-title">{title}</h2>
      <p id="confirm-description">{description}</p>
      <div className="admin-auth-actions">
        <button autoFocus type="button" className="button secondary" disabled={busy} onClick={onCancel}>Cancelar</button>
        <button type="button" className={`button ${danger?'danger':'primary'}`} disabled={busy} onClick={onConfirm}>
          {busy?'Processando...':confirmLabel}
        </button>
      </div>
    </section>
  </div>
}
