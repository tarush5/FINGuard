import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface UiState {
  sidebarCollapsed: boolean;
  commandOpen: boolean;
  notificationsOpen: boolean;
  windowDays: number;
  toggleSidebar: () => void;
  setCommandOpen: (open: boolean) => void;
  setNotificationsOpen: (open: boolean) => void;
  setWindowDays: (days: number) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      commandOpen: false,
      notificationsOpen: false,
      windowDays: 30,
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setCommandOpen: (commandOpen) => set({ commandOpen }),
      setNotificationsOpen: (notificationsOpen) => set({ notificationsOpen }),
      setWindowDays: (windowDays) => set({ windowDays }),
    }),
    { name: 'finguard.ui', partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed, windowDays: state.windowDays }) },
  ),
);
