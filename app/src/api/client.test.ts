import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { makeProject, server, url } from "../test/server";
import { api, call, EngineError, mediaUrl } from "./client";

describe("engine client", () => {
  it("sends the session token and returns the data", async () => {
    let token: string | null = null;
    server.use(
      http.get(url("/projects"), ({ request }) => {
        token = request.headers.get("X-ClipApp-Token");
        return HttpResponse.json([makeProject()]);
      }),
    );
    const projects = await call(api.GET("/projects"));
    expect(token).toBe("test-token");
    expect(projects[0]?.name).toBe("partida ñ");
  });

  it("turns the engine's detail into an EngineError", async () => {
    server.use(
      http.get(url("/projects/7"), () =>
        HttpResponse.json({ detail: "project 7 not found" }, { status: 404 }),
      ),
    );
    const error = await call(
      api.GET("/projects/{project_id}", { params: { path: { project_id: 7 } } }),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(EngineError);
    expect(error).toMatchObject({ status: 404, message: "project 7 not found" });
  });

  it("joins FastAPI validation messages", async () => {
    server.use(
      http.post(url("/projects"), () =>
        HttpResponse.json(
          { detail: [{ msg: "Field required", loc: ["body", "path"] }] },
          { status: 422 },
        ),
      ),
    );
    await expect(call(api.POST("/projects", { body: { path: "" } }))).rejects.toMatchObject({
      status: 422,
      message: "Field required",
    });
  });

  it("reports an unreachable engine as status 0", async () => {
    server.use(http.get(url("/health"), () => HttpResponse.error()));
    await expect(call(api.GET("/health"))).rejects.toMatchObject({ status: 0 });
  });

  it("puts the token in media URLs", () => {
    expect(mediaUrl("/projects/1/media/proxy")).toBe(
      "http://127.0.0.1:9999/projects/1/media/proxy?token=test-token",
    );
  });
});
