import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App.tsx'
import { initializeBackendSession } from './api/backend.ts'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
})

void initializeBackendSession().catch(error => {
  console.error('初始化本地后端会话失败', error)
}).finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}><App /></QueryClientProvider>
    </StrictMode>,
  )
})
