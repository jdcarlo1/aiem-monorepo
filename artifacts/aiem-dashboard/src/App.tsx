import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";

import NotFound from "@/pages/not-found";
import { AppLayout } from "@/components/layout/AppLayout";

// Pages
import Login from "@/pages/login";
import CommandCenter from "@/pages/CommandCenter";
import Opportunities from "@/pages/Opportunities";
import PaperTrades from "@/pages/PaperTrades";
import Decisions from "@/pages/Decisions";
import Proof from "@/pages/Proof";
import Risk from "@/pages/Risk";
import Council from "@/pages/Council";
import Signals from "@/pages/Signals";
import Regime from "@/pages/Regime";
import Scheduler from "@/pages/Scheduler";
import Options from "@/pages/Options";
import Learning from "@/pages/Learning";
import Alerts from "@/pages/Alerts";
import Performance from "@/pages/Performance";
import Probability from "@/pages/Probability";
import Calibration from "@/pages/Calibration";
import Audit from "@/pages/Audit";

const queryClient = new QueryClient();

function Router() {
  return (
    <AppLayout>
      <Switch>
        <Route path="/" component={Login} />
        <Route path="/command" component={CommandCenter} />
        <Route path="/opportunities" component={Opportunities} />
        <Route path="/paper-trades" component={PaperTrades} />
        <Route path="/decisions" component={Decisions} />
        <Route path="/proof" component={Proof} />
        <Route path="/risk" component={Risk} />
        <Route path="/council" component={Council} />
        <Route path="/signals" component={Signals} />
        <Route path="/regime" component={Regime} />
        <Route path="/scheduler" component={Scheduler} />
        <Route path="/options" component={Options} />
        <Route path="/learning" component={Learning} />
        <Route path="/alerts" component={Alerts} />
        <Route path="/performance" component={Performance} />
        <Route path="/probability" component={Probability} />
        <Route path="/calibration" component={Calibration} />
        <Route path="/audit" component={Audit} />
        <Route component={NotFound} />
      </Switch>
    </AppLayout>
  );
}

function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
            <Router />
          </WouterRouter>
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;
