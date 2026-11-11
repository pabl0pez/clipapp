/** Interface-only state (nothing that comes from the engine lives here). */
import { create } from "zustand";

export type Connection = "connecting" | "connected" | "reconnecting" | "offline";
export type ProjectTab = "silences" | "clips" | "export";

interface UiState {
  openProjectId: number | null;
  tab: ProjectTab;
  selectedClipId: number | null;
  zoom: number;
  connection: Connection;
  /** Open a project (or none); switching project resets its tab and selection. */
  openProject: (id: number | null) => void;
  setTab: (tab: ProjectTab) => void;
  selectClip: (id: number | null) => void;
  setZoom: (zoom: number) => void;
  setConnection: (connection: Connection) => void;
}

export const useUiStore = create<UiState>()((set) => ({
  openProjectId: null,
  tab: "silences",
  selectedClipId: null,
  zoom: 1,
  connection: "connecting",
  openProject: (id) => {
    set((s) =>
      s.openProjectId === id ? s : { openProjectId: id, tab: "silences", selectedClipId: null },
    );
  },
  setTab: (tab) => {
    set({ tab });
  },
  selectClip: (selectedClipId) => {
    set({ selectedClipId });
  },
  setZoom: (zoom) => {
    set({ zoom });
  },
  setConnection: (connection) => {
    set({ connection });
  },
}));
