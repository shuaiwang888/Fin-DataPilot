import { create } from "zustand";
import type { SkillItem } from "../lib/types";

interface SkillState {
  skills: SkillItem[];
  drawerOpen: boolean;
  setSkills: (s: SkillItem[]) => void;
  toggle: (name: string) => void;
  setEnabled: (name: string, enabled: boolean) => void;
  setDrawerOpen: (b: boolean) => void;
  /** Insert a newly uploaded skill (or replace existing entry by name). */
  addSkill: (item: SkillItem) => void;
  /** Remove a skill (e.g. after delete). */
  removeSkill: (name: string) => void;
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
  addSkill: (item) =>
    set((state) => {
      const idx = state.skills.findIndex((s) => s.spec.name === item.spec.name);
      if (idx >= 0) {
        const next = [...state.skills];
        next[idx] = item;
        return { skills: next };
      }
      return { skills: [...state.skills, item] };
    }),
  removeSkill: (name) =>
    set((state) => ({
      skills: state.skills.filter((s) => s.spec.name !== name),
    })),
}));
