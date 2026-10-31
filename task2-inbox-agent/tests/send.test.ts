import { describe, expect, it } from "vitest";
import { buildReplyMime } from "@/lib/send";

describe("buildReplyMime", () => {
  it("threads the reply and keeps any language intact", () => {
    const mime = buildReplyMime({
      from: "support@demo.test",
      to: "rosa@example.es",
      subject: "Re: ¿Cómo cambio el pie de página?",
      body: "Hola Rosa,\nVe a Diseño > Pie de página.",
      inReplyTo: "<abc@mail.example>",
    });
    const [headers = "", body = ""] = mime.split("\r\n\r\n");

    expect(headers).toContain("In-Reply-To: <abc@mail.example>");
    expect(headers).toContain("References: <abc@mail.example>");
    expect(headers).toMatch(/^Subject: =\?UTF-8\?B\?/m);
    const subject = /Subject: =\?UTF-8\?B\?(.+)\?=/.exec(headers)?.[1] ?? "";
    expect(Buffer.from(subject, "base64").toString("utf8")).toBe("Re: ¿Cómo cambio el pie de página?");
    expect(Buffer.from(body.replaceAll("\r\n", ""), "base64").toString("utf8")).toBe(
      "Hola Rosa,\r\nVe a Diseño > Pie de página.",
    );
  });

  it("leaves plain subjects readable and skips threading headers it doesn't have", () => {
    const mime = buildReplyMime({ from: "a@b.test", to: "c@d.test", subject: "Re: Footer", body: "Hi", inReplyTo: null });
    expect(mime).toContain("Subject: Re: Footer");
    expect(mime).not.toContain("In-Reply-To");
  });
});
