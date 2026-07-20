import { Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { useAuthStatus } from './hooks/useAuth'
import { ComposeProvider, useCompose } from './hooks/useCompose'
import { Sidebar } from './components/layout/Sidebar'
import { CommandPalette } from './components/layout/CommandPalette'
import { ComposeSheet } from './components/mail/ComposeSheet'
import { LoginPage } from './pages/LoginPage'
import { NowPage } from './pages/NowPage'
import { ReviewPage } from './pages/ReviewPage'
import { InboxPage } from './pages/InboxPage'
import { SearchPage } from './pages/SearchPage'
import { FoldersPage } from './pages/FoldersPage'
import { HistoryPage } from './pages/HistoryPage'
import { InsightsPage } from './pages/InsightsPage'
import { AutomatePage } from './pages/AutomatePage'

function Shell() {
  const { openCompose } = useCompose()
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg text-text">
      <Sidebar onCompose={() => openCompose({ mode: 'new' })} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Navigate to="/now" replace />} />
          <Route path="/now" element={<NowPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/folders" element={<FoldersPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/insights" element={<InsightsPage />} />
          <Route path="/automate" element={<AutomatePage />} />
          <Route path="*" element={<Navigate to="/now" replace />} />
        </Routes>
      </main>
      <CommandPalette onCompose={() => openCompose({ mode: 'new' })} />
      <ComposeSheet />
    </div>
  )
}

export default function App() {
  const { data, isLoading } = useAuthStatus()

  if (isLoading) return <div className="flex h-screen items-center justify-center bg-bg text-text-faint">Loading…</div>
  if (data && data.required && !data.authenticated) return <LoginPage />

  return (
    <ComposeProvider>
      <Toaster theme="dark" position="bottom-right" richColors />
      <Shell />
    </ComposeProvider>
  )
}
