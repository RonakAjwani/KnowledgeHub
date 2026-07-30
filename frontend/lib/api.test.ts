/**
 * Tests for the REST client.
 *
 * The logic worth testing here is not the URL of each endpoint - it is the
 * shared request wrapper: turning the backend's error envelope into a typed
 * `ApiError`, not choking on a non-JSON error body, leaving `content-type`
 * unset for multipart uploads so the browser can write its own boundary, and
 * not calling `.json()` on a 204.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";

function mockFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("error handling", () => {
  it("raises the backend error envelope as a typed ApiError", async () => {
    mockFetch(
      jsonResponse(
        {
          error: {
            code: "not_found",
            message: "No such document",
            request_id: "req-11",
          },
        },
        404,
      ),
    );

    const error = await api.getDocument("missing").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 404,
      code: "not_found",
      message: "No such document",
      requestId: "req-11",
    });
  });

  it("still raises a typed error when the body is not JSON", async () => {
    // A proxy 502 arrives as HTML; parsing it must not crash the client.
    mockFetch(new Response("<html>bad gateway</html>", { status: 502 }));

    const error = (await api
      .listDocuments()
      .catch((e: unknown) => e)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.code).toBe("dependency_unavailable");
    expect(error.message).toContain("502");
  });
});

describe("request shape", () => {
  it("attaches the bearer token when one is supplied", async () => {
    const fetchMock = mockFetch(jsonResponse([]));

    await api.listDocuments(null, "tok-1");

    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      Authorization: "Bearer tok-1",
    });
  });

  it("omits the Authorization header in dev mode", async () => {
    const fetchMock = mockFetch(jsonResponse([]));

    await api.listDocuments();

    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty(
      "Authorization",
    );
  });

  it("leaves content-type unset for a multipart upload", async () => {
    // Setting it by hand omits the boundary, and the backend rejects the body.
    const fetchMock = mockFetch(jsonResponse({ id: "doc-1" }, 201));
    const file = new File(["hello"], "notes.md", { type: "text/markdown" });

    await api.uploadDocument(file, "ws-1");

    const init = fetchMock.mock.calls[0][1];
    expect(init.headers).not.toHaveProperty("content-type");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("workspace_id")).toBe("ws-1");
  });

  it("sends content-type json for a normal request", async () => {
    const fetchMock = mockFetch(jsonResponse({ id: "ws-1" }));

    await api.createWorkspace("Research");

    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      "content-type": "application/json",
    });
  });

  it("scopes a document list to a workspace when one is given", async () => {
    const fetchMock = mockFetch(jsonResponse([]));

    await api.listDocuments("ws-7");

    expect(fetchMock.mock.calls[0][0]).toContain("workspace_id=ws-7");
  });

  it("does not parse a body on 204", async () => {
    // `.json()` on an empty body throws; delete returns 204.
    mockFetch(new Response(null, { status: 204 }));

    await expect(api.deleteDocument("doc-1")).resolves.toBeUndefined();
  });
});

describe("binary download", () => {
  // Bodies are given as bytes rather than as a jsdom `Blob`: the two Blob
  // implementations in this environment (jsdom's and the one behind `Response`)
  // are not interchangeable, and a real server sends bytes anyway.
  it("reads the filename out of the content-disposition header", async () => {
    mockFetch(
      new Response("%PDF-1.7", {
        status: 200,
        headers: {
          "content-disposition": 'attachment; filename="quarterly report.pdf"',
        },
      }),
    );

    const { filename } = await api.downloadDocumentBlob("doc-1");

    expect(filename).toBe("quarterly report.pdf");
  });

  it("returns a null filename when the header is absent", async () => {
    mockFetch(new Response("bytes", { status: 200 }));

    const { filename } = await api.downloadDocumentBlob("doc-1");

    expect(filename).toBeNull();
  });
});
