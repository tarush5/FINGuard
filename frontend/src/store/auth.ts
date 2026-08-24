import { create } from 'zustand';
import { api, ApiError, onUnauthorized, tokenStore } from '@/lib/api';

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  department?: string | null;
  roles: string[];
  permissions: string[];
  can_view_pii?: boolean;
  platform_mode?: string;
}

interface AuthState {
  user: CurrentUser | null;
  status: 'idle' | 'loading' | 'authenticated' | 'anonymous';
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  bootstrap: () => Promise<void>;
  can: (permission: string) => boolean;
  hasRole: (...roles: string[]) => boolean;
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  status: 'idle',
  error: null,

  async login(email, password) {
    set({ status: 'loading', error: null });
    try {
      const data = await api.post<{ access_token: string; refresh_token: string; user: CurrentUser }>(
        '/auth/login',
        { email, password },
      );
      tokenStore.set(data.access_token, data.refresh_token);
      const profile = await api.get<CurrentUser>('/auth/me');
      set({ user: profile, status: 'authenticated', error: null });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Unable to sign in right now.';
      set({ status: 'anonymous', error: message, user: null });
      throw error;
    }
  },

  async logout() {
    const refresh = tokenStore.refresh();
    try {
      if (refresh) await api.post('/auth/logout', { refresh_token: refresh });
    } catch {
      /* logging out locally is what matters */
    }
    tokenStore.clear();
    set({ user: null, status: 'anonymous', error: null });
  },

  async bootstrap() {
    if (!tokenStore.access()) {
      set({ status: 'anonymous' });
      return;
    }
    set({ status: 'loading' });
    try {
      const profile = await api.get<CurrentUser>('/auth/me');
      set({ user: profile, status: 'authenticated' });
    } catch {
      tokenStore.clear();
      set({ user: null, status: 'anonymous' });
    }
  },

  can(permission) {
    return get().user?.permissions.includes(permission) ?? false;
  },

  hasRole(...roles) {
    const current = get().user?.roles ?? [];
    return roles.some((role) => current.includes(role));
  },
}));

// A refresh failure anywhere in the app drops the session immediately.
onUnauthorized(() => {
  useAuth.setState({ user: null, status: 'anonymous' });
});
