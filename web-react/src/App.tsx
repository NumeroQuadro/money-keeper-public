import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { resolveLegacyViewRedirect } from './app/legacyViewRedirect';
import { AppLayout } from './components/AppLayout';
import { NetWorthPage } from './pages/NetWorthPage';
import { OverviewPage } from './pages/OverviewPage';
import { ReviewPage } from './pages/ReviewPage';
import { RulesPage } from './pages/RulesPage';
import { StatementsPage } from './pages/StatementsPage';
import { TransactionsPage } from './pages/TransactionsPage';

function RootRedirect() {
  const location = useLocation();
  const target = resolveLegacyViewRedirect(location.search);
  return <Navigate to={target.to} state={target.state} replace />;
}

function App() {
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/transactions" element={<TransactionsPage />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/accounts" element={<NetWorthPage />} />
        <Route path="/statements" element={<StatementsPage />} />
        <Route path="/settings" element={<Navigate to="/settings/automation" replace />} />
        <Route path="/settings/automation" element={<RulesPage />} />
        <Route path="/transfers" element={<Navigate to="/review" replace />} />
        <Route path="/rules" element={<Navigate to="/settings/automation" replace />} />
        <Route path="/exceptions" element={<Navigate to="/review" replace />} />
        <Route path="/net-worth" element={<Navigate to="/accounts" replace />} />
        <Route path="/networth" element={<Navigate to="/accounts" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
}

export default App;
