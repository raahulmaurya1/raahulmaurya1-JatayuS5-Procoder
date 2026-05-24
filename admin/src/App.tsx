import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { AdminPanel } from './pages/AdminPanel';
import { ReviewQueuePanel } from './pages/ReviewQueuePanel';
import { BankBranchPanel } from './pages/BankBranchPanel';

function ProtectedRoute({ children }: { children: React.JSX.Element }) {
  const isLoggedIn = localStorage.getItem('isAdminLoggedIn') === 'true';
  if (!isLoggedIn) {
    return <Navigate to="/login" />;
  }
  return children;
}

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route 
          path="/" 
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          } 
        />
        <Route 
          path="/admin-panel" 
          element={
            <ProtectedRoute>
              <AdminPanel />
            </ProtectedRoute>
          } 
        />
        <Route 
          path="/review-queue" 
          element={
            <ProtectedRoute>
              <ReviewQueuePanel />
            </ProtectedRoute>
          } 
        />
        <Route 
          path="/bank-branches" 
          element={
            <ProtectedRoute>
              <BankBranchPanel />
            </ProtectedRoute>
          } 
        />
      </Routes>
    </Router>
  );
}

export default App;
