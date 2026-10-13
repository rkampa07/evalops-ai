import { ApiError, api } from "./client";

describe("api client", () => {
  it("parses the backend error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "conflict", message: "dataset exists" } }), { status: 409 })),
    );
    const err = await api.createDataset({ name: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "conflict", message: "dataset exists" });
  });

  it("reports network failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api.health()).rejects.toMatchObject({ code: "network_error" });
  });

  it("handles non-JSON error bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    await expect(api.health()).rejects.toMatchObject({ status: 502, message: "Request failed (HTTP 502)" });
  });

  it("returns undefined for 204 and sends JSON bodies", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.deleteRun(3)).resolves.toBeUndefined();
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toMatch(/\/evaluations\/runs\/3$/);
  });

  it("omits empty query params", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })));
    vi.stubGlobal("fetch", fetchMock);
    await api.listRuns({ dataset_id: 2, model_name: undefined });
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toMatch(/\/evaluations\/runs\?dataset_id=2$/);
  });
});
