"use client";

/**
 * The document as the user uploaded it - a PDF drawn as a PDF, Markdown
 * rendered as Markdown - with the cited passage highlighted on top.
 *
 * **How the highlight is located here, and why it differs from the text view.**
 * Citation offsets index `normalized_text` (I5), which is the sanitised,
 * block-joined string the model actually read. Nothing in the schema maps those
 * offsets back to coordinates on a PDF page, because no bounding boxes are
 * persisted. So this view finds the passage the only way it can without a
 * schema change: it renders the page, reads pdf.js's own text layer, and
 * matches the cited text against it.
 *
 * That makes the highlight here **best effort**. Sanitisation, derived blocks
 * and our line joining all mean `normalized_text` is not byte-identical to the
 * page's text layer, so a match can miss. When it does, the page still renders
 * and scrolls to the cited chunk's recorded page - the reader lands in the
 * right place with nothing highlighted, rather than seeing a confidently wrong
 * box.
 *
 * The exact, provable citation stays available in the Text tab, where the
 * offsets are authoritative and the highlight cannot be wrong. Neither view
 * replaces the other: this one is for reading the document, that one is for
 * proving what was cited.
 */

import { AlertCircle, Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  HIGHLIGHT_NODE_TAG,
  remarkHighlightSnippet,
} from "@/lib/markdown-highlight";
import { MIN_ANCHOR_CHARS, anchorOf, normalise } from "@/lib/text-match";

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Rectangles covering `needle` inside a rendered page, in canvas pixels.
 *
 * pdf.js returns the page as positioned text items. Concatenating them gives a
 * searchable string; the index of a match then maps back to the items it spans,
 * and each item already carries its own transform. Returns an empty list when
 * the passage is not found, which the caller renders as"no highlight"rather
 * than as an error.
 *
 * `normalise` collapsing whitespace to one space (see its own comment) is
 * what makes this reliable across a citation spanning several pdf.js items:
 * `needle` and the haystack built from separate items below both end up with
 * exactly one space at every item boundary, a paragraph break, or a plain
 * word gap alike - regardless of which of those it originally was. Confirmed
 * against a real citation and a real page, not theorised: with the older
 * strip-to-nothing version, a citation crossing even one line wrap failed to
 * match at all, which was true of almost every real citation longer than a
 * few words.
 */
function locate(
  items: { str: string; transform: number[]; width: number; height: number }[],
  needle: string,
): Rect[] {
  const target = anchorOf(needle);
  if (target.length < MIN_ANCHOR_CHARS) return [];

  const spans: { start: number; end: number; item: (typeof items)[number] }[] =
    [];
  let haystack = "";
  for (const item of items) {
    const piece = normalise(item.str);
    if (!piece) continue;
    const start = haystack.length ? haystack.length + 1 : 0;
    haystack = haystack.length ? `${haystack} ${piece}` : piece;
    spans.push({ start, end: haystack.length, item });
  }

  const at = haystack.indexOf(target);
  if (at < 0) return [];
  const until = at + target.length;

  return spans
    .filter((s) => s.start < until && s.end > at)
    .map(({ item }) => ({
      // transform is [a, b, c, d, e, f]; e/f are the x/y translation, and y is
      // measured from the page bottom while the canvas measures from the top.
      left: item.transform[4],
      top: item.transform[5],
      width: item.width,
      height: item.height,
    }));
}

interface PdfViewProps {
  data: ArrayBuffer;
  page: number | null;
  cited: string | null;
}

function PdfView({ data, page, cited }: PdfViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    (async () => {
      setBusy(true);
      setError(null);
      try {
        // Imported here rather than at module scope: pdf.js touches browser
        // globals and must not be pulled into the server bundle.
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();

        // pdf.js takes ownership of the buffer it is given, so a copy is passed
        // - re-rendering with the original would fail on a detached buffer.
        const doc = await pdfjs.getDocument({ data: data.slice(0) }).promise;
        if (cancelled) return;
        container.replaceChildren();

        for (let n = 1; n <= doc.numPages; n += 1) {
          const pdfPage = await doc.getPage(n);
          if (cancelled) return;

          const viewport = pdfPage.getViewport({ scale: 1.4 });
          const wrapper = document.createElement("div");
          // A page reads as paper set down on the panel, not a bordered
          // rectangle flush against it: `overflow-hidden` so the rounding
          // actually clips the canvas (a `<canvas>` ignores its own
          // `border-radius` otherwise), a real shadow rather than the
          // previous flat 1px ring, and enough `mb` between pages that the
          // shadow reads as separation, not clutter.
          wrapper.className =
            "relative mx-auto mb-8 w-fit overflow-hidden rounded-sm shadow-[0_1px_2px_rgba(0,0,0,0.08),0_12px_28px_-8px_rgba(0,0,0,0.35)] ring-1 ring-black/5 dark:shadow-[0_12px_32px_-6px_rgba(0,0,0,0.65)] dark:ring-white/10";
          wrapper.dataset.page = String(n);

          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "block max-w-full h-auto";
          wrapper.appendChild(canvas);

          const context = canvas.getContext("2d");
          if (!context) continue;
          await pdfPage.render({ canvas, canvasContext: context, viewport })
            .promise;
          if (cancelled) return;

          // Attached before computing overlay positions, not after: the
          // canvas's `max-w-full h-auto` (so a wide PDF page shrinks to fit a
          // narrow panel rather than overflowing it) means its *displayed*
          // size is routinely smaller than `canvas.width`/`canvas.height`,
          // the intrinsic pixel buffer `convertToViewportPoint` computes in -
          // and `clientWidth` only reflects the real, laid-out size once the
          // element is actually in the document.
          container.appendChild(wrapper);

          if (cited && (page === null || page === n)) {
            const content = await pdfPage.getTextContent();
            const rects = locate(
              content.items as Parameters<typeof locate>[0],
              cited,
            );
            // How much smaller the canvas is on screen than the coordinate
            // space every position below is computed in. 1 whenever the page
            // fits the panel at its intrinsic size and `max-w-full` never
            // engages - most of the time this is genuinely a no-op, not dead
            // code for a case that cannot happen.
            const displayScale = canvas.width
              ? canvas.clientWidth / canvas.width
              : 1;
            for (const rect of rects) {
              const box = document.createElement("div");
              const [x, y] = viewport.convertToViewportPoint(
                rect.left,
                rect.top,
              );
              box.className =
                "pointer-events-none absolute bg-amber-300/45 mix-blend-multiply dark:bg-amber-400/30";
              box.style.left = `${x * displayScale}px`;
              box.style.top = `${(y - rect.height * 1.4) * displayScale}px`;
              box.style.width = `${rect.width * 1.4 * displayScale}px`;
              box.style.height = `${rect.height * 1.4 * displayScale}px`;
              wrapper.appendChild(box);
            }
            if (rects.length) wrapper.dataset.hit = "1";
          }
        }

        // Scroll to the highlight if one was placed, otherwise to the page the
        // citation recorded - the reader still lands in the right place.
        const target =
          container.querySelector<HTMLElement>("[data-hit='1']") ??
          (page
            ? container.querySelector<HTMLElement>(`[data-page='${page}']`)
            : null);
        target?.scrollIntoView({ block: "center", behavior: "smooth" });
      } catch (cause) {
        if (!cancelled) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not render this PDF.",
          );
        }
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [data, page, cited]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-zinc-100 p-6 dark:bg-zinc-950">
      {busy && (
        <p className="flex items-center justify-center gap-2 py-8 text-xs text-zinc-500">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Rendering document...
        </p>
      )}
      {error && (
        <p className="flex items-center justify-center gap-2 py-8 text-xs text-zinc-500">
          <AlertCircle className="size-3.5" aria-hidden />
          {error} The Text tab still shows the exact cited passage.
        </p>
      )}
      <div ref={containerRef} />
    </div>
  );
}

/**
 * Renders `remarkHighlightSnippet`'s custom node. Same visual language as the
 * Text tab's `<mark>` (`SourcePane.tsx`) - both are an actual highlighted DOM
 * span, unlike the PDF view's semi-transparent overlay box, which has to stay
 * translucent so it does not obscure the pixels underneath it. Defined once
 * at module scope, not inline in the component: it takes no props derived
 * from `DocumentView`'s own, so recreating it every render would just be
 * churn for `ReactMarkdown`'s memoisation to see through.
 */
const MARKDOWN_HIGHLIGHT_COMPONENTS = {
  [HIGHLIGHT_NODE_TAG]: ({ children }: { children?: ReactNode }) => (
    <mark className="rounded-sm bg-accent-200/80 text-inherit ring-1 ring-accent-400/60 dark:bg-accent-500/30 dark:ring-accent-400/40">
      {children}
    </mark>
  ),
} as unknown as Components;

export interface DocumentViewProps {
  mime: string;
  /** Raw uploaded bytes, from `GET /documents/{id}/blob`. */
  data: ArrayBuffer | null;
  /** The document's normalized text, used to render Markdown and plain text. */
  text: string;
  /** Page the citation recorded, when the source is a PDF. */
  page: number | null;
  /** The cited passage itself, sliced out of `normalized_text` by the caller. */
  cited: string | null;
}

export function DocumentView({
  mime,
  data,
  text,
  page,
  cited,
}: DocumentViewProps) {
  const isPdf = mime === "application/pdf";
  const isMarkdown = mime === "text/markdown" || mime === "text/x-markdown";

  // Markdown is rendered from `normalized_text` rather than the raw upload, so
  // what is shown is still what the model read - sanitisation included. For
  // Markdown the two differ only in whitespace, so nothing legible is lost.
  const body = useMemo(() => text, [text]);

  // Scrolls the Markdown or plain-text view to its highlight the same way
  // `PdfView` scrolls to its own (and `SourcePane`'s Text tab scrolls to its
  // `<mark>` ref) - a citation deep in a long document is exactly as
  // invisible without this as no highlight at all. One ref shared by both
  // branches below rather than one each: they are mutually exclusive returns
  // from the same component, so only one of them ever actually mounts a
  // `<div>` onto it in a given render.
  const textContainerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = textContainerRef.current;
    if (!container) return;
    container
      .querySelector("mark")
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [text, cited]);

  // Plain text has no syntax to preserve a highlight through - `cited` is an
  // exact slice of this exact `body`, so a plain substring split is enough,
  // unlike Markdown's node-aware split or the PDF search. `indexOf` rather
  // than the offsets themselves: they are not threaded through this far, and
  // slicing at the match `indexOf` finds is equivalent whenever the snippet
  // is unique in the document, which a citation-length passage almost always
  // is.
  const plainTextHighlight = useMemo(() => {
    if (!cited) return null;
    const at = body.indexOf(cited);
    return at < 0 ? null : { before: body.slice(0, at), match: cited, after: body.slice(at + cited.length) };
  }, [body, cited]);

  if (isPdf) {
    if (!data) {
      return (
        <p className="flex flex-1 items-center justify-center gap-2 p-8 text-xs text-zinc-500">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Loading document...
        </p>
      );
    }
    return <PdfView data={data} page={page} cited={cited} />;
  }

  if (isMarkdown) {
    return (
      <div
        ref={textContainerRef}
        className="min-h-0 flex-1 overflow-y-auto px-6 py-5"
      >
        <div className="prose-kh mx-auto max-w-[42rem] text-[15px] leading-7 text-zinc-800 dark:text-zinc-200">
          {/* `[remarkHighlightSnippet, cited]`, not `remarkHighlightSnippet(cited)`:
              unified calls whatever sits in this array as the *attacher*,
              exactly once, to obtain a transformer back - passing the
              already-produced transformer directly gets it invoked a second
              time as if it *were* the attacher, with no `tree` argument, which
              crashed inside `collectTextNodes` on `undefined.type`. The tuple
              form is how unified threads an argument through that first,
              attacher-level call instead. */}
          <ReactMarkdown
            remarkPlugins={[remarkGfm, [remarkHighlightSnippet, cited]]}
            components={MARKDOWN_HIGHLIGHT_COMPONENTS}
          >
            {body}
          </ReactMarkdown>
        </div>
      </div>
    );
  }

  return (
    <div ref={textContainerRef} className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <pre className="mx-auto max-w-[42rem] whitespace-pre-wrap break-words font-sans text-[15px] leading-7 text-zinc-800 dark:text-zinc-200">
        {plainTextHighlight ? (
          <>
            {plainTextHighlight.before}
            <mark className="rounded-sm bg-accent-200/80 text-inherit ring-1 ring-accent-400/60 dark:bg-accent-500/30 dark:ring-accent-400/40">
              {plainTextHighlight.match}
            </mark>
            {plainTextHighlight.after}
          </>
        ) : (
          body
        )}
      </pre>
    </div>
  );
}
