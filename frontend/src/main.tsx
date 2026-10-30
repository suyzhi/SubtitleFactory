import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { initializeBackendSession } from './api/backend.ts'

void initializeBackendSession().catch(error => {
  console.error('初始化本地后端会话失败', error)
}).finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
