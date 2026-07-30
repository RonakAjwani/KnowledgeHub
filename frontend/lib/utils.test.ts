/**
 * `conversationLabel` exists because `Conversation.title` is always `null` -
 * no backend path sets it. Anything that renders or *searches* a conversation
 * has to go through the fallback, or it silently displays and matches nothing.
 */

import { describe, expect, it } from "vitest";

import { fileKind } from "./fileKind";
import { cn, conversationLabel } from "./utils";

describe("conversationLabel", () => {
  it("falls back when the title is null, which is the only case in production today", () => {
    expect(conversationLabel({ title: null })).toBe("Untitled chat");
  });

  it("falls back on an empty string rather than rendering a blank row", () => {
    expect(conversationLabel({ title: "" })).toBe("Untitled chat");
  });

  it("handles a missing conversation entirely", () => {
    expect(conversationLabel(null)).toBe("Untitled chat");
    expect(conversationLabel(undefined)).toBe("Untitled chat");
  });

  it("uses a real title once the backend starts setting one", () => {
    expect(conversationLabel({ title: "Q3 margins" })).toBe("Q3 margins");
  });
});

describe("cn", () => {
  it("lets a later Tailwind class win over an earlier conflicting one", () => {
    // The whole reason twMerge is here rather than plain clsx.
    expect(cn("px-2", "px-4")).toBe("px-4");
  });

  it("drops falsy branches", () => {
    expect(cn("a", false && "b", null, undefined, "c")).toBe("a c");
  });
});

describe("fileKind", () => {
  it("recognises a PDF by mime", () => {
    expect(fileKind("report.pdf", "application/pdf").label).toBe("PDF");
  });

  it("recognises Markdown by either extension", () => {
    expect(fileKind("notes.md", "text/plain").label).toBe("Markdown");
    expect(fileKind("notes.markdown", "text/plain").label).toBe("Markdown");
  });

  it("falls back to a PDF swatch on extension when the mime is generic", () => {
    // Browsers hand over application/octet-stream often enough that trusting
    // mime alone mislabels real uploads.
    expect(fileKind("report.pdf", "application/octet-stream").label).toBe("PDF");
  });

  it("treats anything else as plain text", () => {
    expect(fileKind("data.txt", "text/plain").label).toBe("Text");
    expect(fileKind("noextension", "").label).toBe("Text");
  });

  it("is case-insensitive about the extension", () => {
    expect(fileKind("REPORT.PDF", "").label).toBe("PDF");
  });
});
