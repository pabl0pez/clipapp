/** TanStack Query hooks for everything the UI reads from or writes to the engine. */
import {
  MutationCache,
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "../components/ui/Toast";
import {
  api,
  call,
  type Job,
  type Project,
  type ProjectPatch,
  type SystemReport,
  type ThumbIndex,
} from "./client";

/** Query keys; `["projects"]` prefixes every project key, so invalidating it hits them all. */
export const keys = {
  projects: ["projects"] as const,
  project: (id: number) => ["projects", id] as const,
  jobs: ["jobs"] as const,
  thumbs: (id: number) => ["thumbs", id] as const,
  health: ["health"] as const,
  system: ["system"] as const,
};

/** Query client of the app: one retry, and every failed mutation is shown as a toast. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
    mutationCache: new MutationCache({
      onError: (error) => {
        toast(error.message, "error");
      },
    }),
  });
}

const projectPath = (id: number) => ({ params: { path: { project_id: id } } });

/** Every project, most recently updated first. */
export function useProjects() {
  return useQuery({ queryKey: keys.projects, queryFn: () => call(api.GET("/projects")) });
}

/** One project. */
export function useProject(id: number) {
  return useQuery({
    queryKey: keys.project(id),
    queryFn: () => call(api.GET("/projects/{project_id}", projectPath(id))),
  });
}

/** Every job, newest first; `useEngineEvents` keeps progress and status live. */
export function useJobs() {
  return useQuery({ queryKey: keys.jobs, queryFn: () => call(api.GET("/exports")) });
}

/** The newest prepare job of a project, once the job list is loaded. */
export function usePrepareJob(projectId: number): Job | undefined {
  return useJobs().data?.find((j) => j.kind === "prepare" && j.target_id === projectId);
}

/** Thumbnail sprite layout; only fetched once the project is prepared. */
export function useThumbIndex(projectId: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.thumbs(projectId),
    enabled,
    staleTime: Infinity,
    queryFn: async () =>
      (await call(
        api.GET("/projects/{project_id}/media/thumbnails/index", projectPath(projectId)),
      )) as ThumbIndex,
  });
}

/** Engine version. */
export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: () => call(api.GET("/health")) });
}

/** Doctor report (takes seconds on the engine, so it stays cached until refreshed by hand). */
export function useSystem() {
  return useQuery({
    queryKey: keys.system,
    staleTime: Infinity,
    queryFn: async () => (await call(api.GET("/system"))) as unknown as SystemReport,
  });
}

function useProjectSaved() {
  const qc = useQueryClient();
  return (project: Project) => {
    qc.setQueryData(keys.project(project.id), project);
    void qc.invalidateQueries({ queryKey: keys.projects, exact: true });
    void qc.invalidateQueries({ queryKey: keys.jobs });
  };
}

/** Import a recording by path (creates the project and queues its preparation). */
export function useImportProject() {
  const saved = useProjectSaved();
  return useMutation({
    mutationFn: (videoPath: string) => call(api.POST("/projects", { body: { path: videoPath } })),
    onSuccess: saved,
  });
}

/** Rename a project or change its tracks. */
export function usePatchProject(id: number) {
  const saved = useProjectSaved();
  return useMutation({
    mutationFn: (body: ProjectPatch) =>
      call(api.PATCH("/projects/{project_id}", { ...projectPath(id), body })),
    onSuccess: saved,
  });
}

/** Delete a project's data (never the original video). */
export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => call(api.DELETE("/projects/{project_id}", projectPath(id))),
    onSuccess: (_data, id) => {
      qc.removeQueries({ queryKey: keys.project(id) });
      void qc.invalidateQueries({ queryKey: keys.projects, exact: true });
    },
  });
}

/** Queue a failed or cancelled job again. */
export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) =>
      call(api.POST("/exports/{job_id}/retry", { params: { path: { job_id: jobId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.jobs }),
  });
}
