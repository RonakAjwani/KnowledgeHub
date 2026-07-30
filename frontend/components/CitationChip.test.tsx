/**
 * The citation chip is where invariant I2 becomes something a user can see.
 *
 * `verified` has three states and they must *look* like three states. Collapsing
 * `null` into `false` would put a warning on every citation of every answer
 * whose verification was merely slow - reporting a finding nobody made. These
 * tests assert the distinction survives at the accessible-name level, not just
 * in a class string, because a colour-only difference is not a distinction.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Citation } from "@/lib/types";

import { CitationChip } from "./CitationChip";

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    marker: 2,
    chunk_id: "chunk-2",
    doc_id: "doc-1",
    filename: "factsheet.pdf",
    section: "Fund performance",
    page: 7,
    char_start: 120,
    char_end: 240,
    verified: null,
    ...overrides,
  };
}

describe("the three verification states", () => {
  it("describes an unchecked citation as not yet checked, never as unsupported", () => {
    render(<CitationChip citation={citation({ verified: null })} onClick={vi.fn()} />);

    const chip = screen.getByRole("button");
    expect(chip).toHaveAccessibleName(/not yet checked/i);
    expect(chip).not.toHaveAccessibleName(/not supported/i);
  });

  it("describes a supported citation as verified", () => {
    render(<CitationChip citation={citation({ verified: true })} onClick={vi.fn()} />);

    expect(screen.getByRole("button")).toHaveAccessibleName(/verified against the source/i);
  });

  it("flags an unsupported citation explicitly", () => {
    render(<CitationChip citation={citation({ verified: false })} onClick={vi.fn()} />);

    expect(screen.getByRole("button")).toHaveAccessibleName(/not supported by the cited passage/i);
  });

  it("styles the three states differently, not only by colour", () => {
    const { rerender } = render(
      <CitationChip citation={citation({ verified: null })} onClick={vi.fn()} />,
    );
    const pending = screen.getByRole("button").className;

    rerender(<CitationChip citation={citation({ verified: true })} onClick={vi.fn()} />);
    const supported = screen.getByRole("button").className;

    rerender(<CitationChip citation={citation({ verified: false })} onClick={vi.fn()} />);
    const unsupported = screen.getByRole("button").className;

    expect(new Set([pending, supported, unsupported]).size).toBe(3);
    // Pending is the only dashed one - provisional, nothing asserted yet.
    expect(pending).toContain("border-dashed");
    expect(supported).not.toContain("border-dashed");
  });
});

describe("provenance and interaction", () => {
  it("renders the marker the answer text refers to", () => {
    render(<CitationChip citation={citation({ marker: 3 })} onClick={vi.fn()} />);

    expect(screen.getByRole("button")).toHaveTextContent("[3]");
  });

  it("names the file, section and page so the chip is identifiable before clicking", () => {
    render(<CitationChip citation={citation()} onClick={vi.fn()} />);

    const name = screen.getByRole("button").getAttribute("aria-label") ?? "";
    expect(name).toContain("factsheet.pdf");
    expect(name).toContain("Fund performance");
    expect(name).toContain("page 7");
  });

  it("omits an absent section and page rather than printing null", () => {
    render(
      <CitationChip
        citation={citation({ section: null, page: null })}
        onClick={vi.fn()}
      />,
    );

    const name = screen.getByRole("button").getAttribute("aria-label") ?? "";
    expect(name).not.toMatch(/null|undefined/);
  });

  it("hands the whole citation to the click handler, so the pane can resolve its offsets", () => {
    const onClick = vi.fn();
    const target = citation({ char_start: 120, char_end: 240 });
    render(<CitationChip citation={target} onClick={onClick} />);

    screen.getByRole("button").click();

    expect(onClick).toHaveBeenCalledWith(target);
  });
});
