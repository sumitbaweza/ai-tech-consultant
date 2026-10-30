import { describe, expect, it, vi } from "vitest";
import { BrainRequestError, BrainUnavailable, createBrainClient } from "@/lib/brain";

const answer = {
  question: "How do I change the logo?",
  standalone_question: "How do I change the logo in the header?",
  answered: true,
  answer: "Go to Design Settings > Logo.",
  citations: [
    {
      source_id: "S1",
      origin: "article",
      chunk_id: 10,
      page_id: 3,
      title: "Changing your logo",
      url: "https://support.example/logo",
      image_url: null,
      snippet: "Design Settings > Logo",
    },
  ],
  confidence: 0.82,
  outcome: "answered",
  sources: [],
  expert_task_id: null,
};

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function client(fetchImpl: typeof fetch, apiKey: string | undefined) {
  return createBrainClient({ baseUrl: "http://brain:8000/", apiKey, fetch: fetchImpl });
}

describe("Brain client", () => {
  it("sends the question with the API key and checks the answer", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(200, answer));

    const result = await client(fetchImpl, "test-key").ask("How do I change the logo?");

    expect(result.answered).toBe(true);
    expect(result.citations[0]?.url).toBe("https://support.example/logo");
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe("http://brain:8000/v1/ask");
    expect(init?.headers).toMatchObject({ "X-API-Key": "test-key" });
    const body = typeof init?.body === "string" ? init.body : "";
    expect(JSON.parse(body)).toEqual({ question: "How do I change the logo?", history: [] });
  });

  it("calls the Brain unavailable when it's down, overloaded or not configured", async () => {
    const refused = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("fetch failed"));
    await expect(client(refused, "test-key").ask("q")).rejects.toBeInstanceOf(BrainUnavailable);

    const broken = vi.fn<typeof fetch>().mockResolvedValue(json(503, { detail: "down" }));
    await expect(client(broken, "test-key").ask("q")).rejects.toBeInstanceOf(BrainUnavailable);

    const neverCalled = vi.fn<typeof fetch>();
    await expect(client(neverCalled, undefined).ask("q")).rejects.toThrow("BRAIN_API_KEY is not set");
    expect(neverCalled).not.toHaveBeenCalled();
  });

  it("treats a rejected request or an unknown reply as a bug, not an outage", async () => {
    const rejected = vi.fn<typeof fetch>().mockResolvedValue(json(422, { detail: "question too long" }));
    await expect(client(rejected, "test-key").ask("q")).rejects.toBeInstanceOf(BrainRequestError);

    const changed = vi.fn<typeof fetch>().mockResolvedValue(json(200, { answer: "no other fields" }));
    await expect(client(changed, "test-key").ask("q")).rejects.toBeInstanceOf(BrainRequestError);
  });

  it("returns undefined for an article the Brain doesn't have", async () => {
    const missing = vi.fn<typeof fetch>().mockResolvedValue(json(404, { detail: "Not found" }));
    await expect(client(missing, "test-key").getArticle(999)).resolves.toBeUndefined();
  });

  it("flags a wrong answer as an expert task", async () => {
    const created = vi
      .fn<typeof fetch>()
      .mockResolvedValue(json(200, { id: 7, question: "q", status: "open", asked_count: 1, reason: "thumbs_down" }));

    const task = await client(created, "test-key").flagWrongAnswer("q", "a wrong answer");

    expect(task).toEqual({ id: 7, question: "q", status: "open", asked_count: 1 });
    expect(created.mock.calls[0]?.[0]).toBe("http://brain:8000/v1/expert-tasks");
  });
});
