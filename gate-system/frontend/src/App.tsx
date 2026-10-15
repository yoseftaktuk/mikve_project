import { Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/AppLayout'
import { DashboardPage } from './routes/pages/DashboardPage/DashboardPage'
import { FingerprintEnrollPage } from './routes/pages/FingerprintEnrollPage/FingerprintEnrollPage'
import { ManagementPage } from './routes/pages/ManagementPage/ManagementPage'
import { MoneyTopupPage } from './routes/pages/MoneyTopupPage/MoneyTopupPage'

/** Root router for the gate dashboard pages. */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/money-topup" element={<MoneyTopupPage />} />
        <Route path="/fingerprint-enroll" element={<FingerprintEnrollPage />} />
        <Route path="/management" element={<ManagementPage />} />
        <Route path="/admin" element={<Navigate to="/management" replace />} />
        <Route path="/login" element={<Navigate to="/dashboard" replace />} />
        <Route path="/register" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
