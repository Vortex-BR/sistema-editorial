import {Route, Routes} from 'react-router-dom'
import {AdminTokenProvider} from './components/AdminTokenProvider'
import {Layout} from './components/Layout'
import {AdminCuration} from './pages/AdminCuration'
import {Config} from './pages/Config'
import {Dashboard} from './pages/Dashboard'
import {NewProject} from './pages/NewProject'
import {Pipeline} from './pages/Pipeline'
import {PublicationProfiles} from './pages/PublicationProfiles'

export function App(){
  return (
    <AdminTokenProvider>
      <Routes>
        <Route element={<Layout/>}>
          <Route index element={<Dashboard/>}/>
          <Route path="/novo" element={<NewProject/>}/>
          <Route path="/perfis" element={<PublicationProfiles/>}/>
          <Route path="/projetos/:id" element={<Pipeline/>}/>
          <Route path="/config" element={<Config/>}/>
          <Route path="/admin/curadoria" element={<AdminCuration/>}/>
        </Route>
      </Routes>
    </AdminTokenProvider>
  )
}
