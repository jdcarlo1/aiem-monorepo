import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Route, Switch, Router as WouterRouter, useLocation } from 'wouter';
import { useEffect } from 'react';
import { getToken } from '@/hooks/use-api';
import { AppShell } from '@/components/layout/AppShell';

import AuthPage from '@/pages/auth';
import LiveDecisionsPage from '@/pages/live-decisions';
import DecisionsPage from '@/pages/decisions';
import PositionsPage from '@/pages/positions';
import WhyTradePage from '@/pages/why-trade';
import CalibrationPage from '@/pages/calibration';
import StatusPage from '@/pages/status';
import StrategiesPage from '@/pages/strategies';
import NotFound from '@/pages/not-found';

const queryClient = new QueryClient();

function ProtectedRoute({ component: Component }: { component: React.ComponentType }) {
  const [, setLocation] = useLocation();

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLocation('/auth');
    }
  }, [setLocation]);

  const token = getToken();
  if (!token) {
    return null;
  }

  return (
    <AppShell>
      <Component />
    </AppShell>
  );
}

function Router() {
  return (
    <Switch>
      <Route path="/auth" component={AuthPage} />
      <Route path="/">
        <ProtectedRoute component={LiveDecisionsPage} />
      </Route>
      <Route path="/decisions">
        <ProtectedRoute component={DecisionsPage} />
      </Route>
      <Route path="/positions">
        <ProtectedRoute component={PositionsPage} />
      </Route>
      <Route path="/strategies">
        <ProtectedRoute component={StrategiesPage} />
      </Route>
      <Route path="/why/:traceId">
        <ProtectedRoute component={WhyTradePage} />
      </Route>
      <Route path="/calibration">
        <ProtectedRoute component={CalibrationPage} />
      </Route>
      <Route path="/status">
        <ProtectedRoute component={StatusPage} />
      </Route>
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
