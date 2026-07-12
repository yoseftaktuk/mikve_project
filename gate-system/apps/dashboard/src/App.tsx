import { Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/AppLayout'
import { ChargeChipPage } from './routes/pages/ChargeChipPage/ChargeChipPage'
import { DashboardPage } from './routes/pages/DashboardPage/DashboardPage'
import { ManagementPage } from './routes/pages/ManagementPage/ManagementPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/charge-chip" element={<ChargeChipPage />} />
        <Route path="/management" element={<ManagementPage />} />
        <Route path="/admin" element={<Navigate to="/management" replace />} />
        <Route path="/login" element={<Navigate to="/dashboard" replace />} />
        <Route path="/register" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
