import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../../api/client";
import { createQueryClient } from "../../api/queries";
import App from "../../App";
import { useUiStore } from "../../stores/ui";
import { FakeWebSocket, makeJob, makeProject, server, url } from "../../test/server";

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(() => Promise.resolve("C:\\Grabaciones\\partida ñ.mkv")),
}));

describe("import flow", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    useUiStore.setState({ connection: "connecting" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("imports a video, shows its preparation and saves the chosen tracks", async () => {
    let project: Project | null = null;
    const posted: unknown[] = [];
    const patched: unknown[] = [];
    server.use(
      http.get(url("/projects"), () => HttpResponse.json(project ? [project] : [])),
      http.post(url("/projects"), async ({ request }) => {
        posted.push(await request.json());
        project = makeProject();
        return HttpResponse.json(project, { status: 201 });
      }),
      http.get(url("/projects/1"), () => HttpResponse.json(project)),
      http.patch(url("/projects/1"), async ({ request }) => {
        const body = (await request.json()) as { mic_track: number; game_track: number };
        patched.push(body);
        project = makeProject({ media: { ...makeProject().media, ...body } });
        return HttpResponse.json(project);
      }),
      http.get(url("/exports"), () => HttpResponse.json([makeJob({ progress: 0.5 })])),
    );
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Todavía no hay proyectos")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Importar video" }));
    expect(posted).toEqual([{ path: "C:\\Grabaciones\\partida ñ.mkv" }]);

    expect(await screen.findByRole("textbox", { name: "Nombre del proyecto" })).toHaveValue(
      "partida ñ",
    );
    expect(screen.getByText(/1:02:05 · 1920×1080 · 60.00 fps · 3 pistas/)).toBeInTheDocument();
    expect(await screen.findByRole("progressbar", { name: "Proxy 720p" })).toHaveAttribute(
      "aria-valuenow",
      "47",
    );
    for (const tab of screen.getAllByRole("tab")) expect(tab).toBeDisabled();

    const dialog = await screen.findByRole("dialog", { name: "Pistas de audio" });
    expect(within(dialog).getAllByRole("listitem")[1]).toHaveTextContent("Pista 2 · Mic");
    expect(
      within(dialog).getByRole("button", { name: "Escuchar 5 s de la pista 3" }),
    ).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText("Micrófono"), "Pista 1 · Mix");
    await user.click(within(dialog).getByRole("button", { name: "Guardar" }));

    expect(patched).toEqual([{ mic_track: 0, game_track: 2 }]);
    expect(await screen.findByText("Micrófono: Pista 1 · Mix")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
