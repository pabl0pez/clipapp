/** Import a recording (from the native dialog or a dropped path) and open its project. */
import { open } from "@tauri-apps/plugin-dialog";
import { useCallback } from "react";
import { useNavigate } from "react-router";
import { useImportProject } from "../../api/queries";

export const VIDEO_EXTENSIONS = ["mp4", "mkv", "mov"];

/** True when the path ends with a supported video extension. */
export const isVideo = (path: string) =>
  VIDEO_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(`.${ext}`));

export function useImportVideo() {
  const { mutate, isPending } = useImportProject();
  const navigate = useNavigate();

  const importPath = useCallback(
    (path: string) => {
      mutate(path, {
        // ?tracks=1 opens the track selector right after importing
        onSuccess: (project) => void navigate(`/projects/${String(project.id)}?tracks=1`),
      });
    },
    [mutate, navigate],
  );

  const pickAndImport = useCallback(async () => {
    const path = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "Video", extensions: VIDEO_EXTENSIONS }],
    });
    if (typeof path === "string") importPath(path);
  }, [importPath]);

  return { importPath, pickAndImport, pending: isPending };
}
