import { useState } from 'react';
import { useLocation } from 'wouter';
import { setToken } from '@/hooks/use-api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Shield } from 'lucide-react';

export default function AuthPage() {
  const [token, setTokenInput] = useState('');
  const [, setLocation] = useLocation();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (token.trim()) {
      setToken(token.trim());
      setLocation('/');
    }
  };

  return (
    <div className="min-h-[100dvh] w-full flex items-center justify-center bg-background">
      <div className="w-full max-w-md p-8">
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center mb-4">
            <Shield className="w-8 h-8 text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-foreground">
            Options Engine Terminal
          </h1>
          <p className="text-sm text-muted-foreground mt-1 font-mono">
            Admin Authentication Required
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="token"
              className="block text-sm font-medium text-foreground mb-2"
            >
              Admin Token
            </label>
            <Input
              id="token"
              type="password"
              value={token}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="Enter admin token"
              className="font-mono"
              autoFocus
              data-testid="input-token"
            />
          </div>

          <Button
            type="submit"
            className="w-full"
            disabled={!token.trim()}
            data-testid="button-submit"
          >
            Authenticate
          </Button>
        </form>

        <div className="mt-8 p-4 bg-muted/50 rounded border border-border">
          <p className="text-xs text-muted-foreground leading-relaxed">
            This terminal provides forensic-grade access to the options trading
            pipeline. All actions are logged and cryptographically verified.
          </p>
        </div>
      </div>
    </div>
  );
}
