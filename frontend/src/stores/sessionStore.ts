import { create } from "zustand";
import type { Session } from "../lib/types";

interface SessionState {
  sessions: Session[];
  activeId: string | null;
  loading: boolean;
  setSessions: (s: Session[]) => void;
  setActive: (id: string | null) => void;
  prepend: (s: Session) => void;
  remove: (id: string) => void;
  rename: (id: string, title: string) => void;
  setLoading: (b: boolean) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  activeId: null,
  loading: false,
  setSessions: (sessions) => set({ sessions }),
  setActive: (activeId) => set({ activeId }),
  prepend: (s) => set((state) => ({ sessions: [s, ...state.sessions] })),
  remove: (id) =>
    set((state) => ({ sessions: state.sessions.filter((s) => s.id !== id), activeId: state.activeId === id ? null : state.activeId })),
  rename: (id, title) =>
    set((state) => ({
      sessions: state.sessions.map((s) => (s.id === id ? { ...s, title } : s)),
    })),
  setLoading: (loading) => set({ loading }),
}));
