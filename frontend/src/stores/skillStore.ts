import { create } from "zustand";
import type { SkillItem } from "../lib/types";

interface SkillState {
  skills: SkillItem[];
  drawerOpen: boolean;
  setSkills: (s: SkillItem[]) => void;
  toggle: (name: string) => void;
  setEnabled: (name: string, enabled: boolean) => void;
  setDrawerOpen: (b: boolean) => void;
}

export const useSkillStore = create<SkillState>((set) => ({
  skills: [],
  drawerOpen: false,
  setSkills: (skills) => set({ skills }),
  toggle: (name) =>
    set((state) => ({
      skills: state.skills.map((s) =>
        s.spec.name === name ? { ...s, enabled: !s.enabled } : s
      ),
    })),
  setEnabled: (name, enabled) =>
    set((state) => ({
      skills: state.skills.map((s) =>
        s.spec.name === name ? { ...s, enabled } : s
      ),
    })),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
}));
