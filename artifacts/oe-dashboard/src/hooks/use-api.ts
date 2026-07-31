import { useCallback } from 'react';
import { useLocation } from 'wouter';

const API_BASE = '/stock-api';
const TOKEN_KEY = 'oe_admin_token';

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function useApi() {
  const [, setLocation] = useLocation();

  const apiFetch = useCallback(
    async <T = unknown>(endpoint: string, options: RequestInit = {}): Promise<T> => {
      const token = getToken();
      
      if (!token) {
        setLocation('/auth');
        throw new Error('No authentication token');
      }

      const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;

      const response = await fetch(url, {
        ...options,
        headers: {
          'X-Admin-Token': token,
          'Content-Type': 'application/json',
          ...options.headers,
        },
        credentials: 'include',
      });

      if (response.status === 401) {
        clearToken();
        setLocation('/auth');
        throw new Error('Unauthorized');
      }

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`API error: ${response.status} ${text}`);
      }

      return response.json();
    },
    [setLocation]
  );

  return { apiFetch };
}
