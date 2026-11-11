/** Files dropped on the window, through Tauri (HTML drop events carry no file paths there). */
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { useEffect, useRef, useState } from "react";
import { toast } from "../../components/ui/Toast";
import { isVideo } from "./useImportVideo";

/**
 * Call `onVideo` with the first dropped video file; non-video drops show a toast.
 *
 * @returns Whether something is being dragged over the window (for an overlay).
 */
export function useFileDrop(onVideo: (path: string) => void): boolean {
  const [dragging, setDragging] = useState(false);
  const latest = useRef(onVideo);
  useEffect(() => {
    latest.current = onVideo;
  });

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void getCurrentWebview()
      .onDragDropEvent(({ payload }) => {
        if (payload.type === "enter") setDragging(true);
        else if (payload.type === "leave") setDragging(false);
        else if (payload.type === "drop") {
          setDragging(false);
          const video = payload.paths.find(isVideo);
          if (video) latest.current(video);
          else toast("Suelta un archivo MP4, MKV o MOV", "error");
        }
      })
      .then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  return dragging;
}
