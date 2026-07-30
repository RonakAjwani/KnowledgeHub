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
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Words shorter than this carry too little signal to anchor a match - "the"
// appears on every page. Below it, the search is skipped rather than risk
// highlighting an unrelated line.
const MIN_ANCHOR_CHARS = 12;
// How much of the cited passage to search for. The whole span often crosses a
// column or page break in the text layer and then matches nothing; its opening
// is enough to place the highlight.
const ANCHOR_CHARS = 180;

/** Collapse whitespace so the text layer and `normalized_text` compare fairly. */
function normalise(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

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
 * the passage is not found, which the caller renders as "no highlight" rather
 * than as an error.
 */
function locate(
  items: { str: string; transform: number[]; width: number; height: number }[],
  needle: string,
): Rect[] {
  const target = normalise(needle).slice(0, ANCHOR_CHARS);
  if (target.length < MIN_ANCHOR_CHARS) return [];

  const spans: { start: number; end: number; item: (typeof items)[number] }[] = [];
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
          wrapper.className =
            "relative mx-auto mb-4 w-fit shadow-sm ring-1 ring-zinc-200 dark:ring-zinc-800";
          wrapper.dataset.page = String(n);

          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "block max-w-full h-auto";
          wrapper.appendChild(canvas);

          const context = canvas.getContext("2d");
          if (!context) continue;
          await pdfPage.render({ canvas, canvasContext: context, viewport }).promise;
          if (cancelled) return;

          if (cited && (page === null || page === n)) {
            const content = await pdfPage.getTextContent();
            const rects = locate(
              content.items as Parameters<typeof locate>[0],
              cited,
            );
            for (const rect of rects) {
              const box = document.createElement("div");
              const [x, y] = viewport.convertToViewportPoint(rect.left, rect.top);
              box.className =
                "pointer-events-none absolute bg-amber-300/45 mix-blend-multiply dark:bg-amber-400/30";
              box.style.left = `${x}px`;
              box.style.top = `${y - rect.height * 1.4}px`;
              box.style.width = `${rect.width * 1.4}px`;
              box.style.height = `${rect.height * 1.4}px`;
              wrapper.appendChild(box);
            }
            if (rects.length) wrapper.dataset.hit = "1";
          }

          container.appendChild(wrapper);
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
          setError(cause instanceof Error ? cause.message : "Could not render this PDF.");
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
    <div className="min-h-0 flex-1 overflow-y-auto bg-zinc-100 p-4 dark:bg-zinc-950">
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

export function DocumentView({ mime, data, text, page, cited }: DocumentViewProps) {
  const isPdf = mime === "application/pdf";
  const isMarkdown = mime === "text/markdown" || mime === "text/x-markdown";

  // Markdown is rendered from `normalized_text` rather than the raw upload, so
  // what is shown is still what the model read - sanitisation included. For
  // Markdown the two differ only in whitespace, so nothing legible is lost.
  const body = useMemo(() => text, [text]);

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
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="prose-kh mx-auto max-w-[42rem] text-[15px] leading-7 text-zinc-800 dark:text-zinc-200">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <pre className="mx-auto max-w-[42rem] whitespace-pre-wrap break-words font-sans text-[15px] leading-7 text-zinc-800 dark:text-zinc-200">
        {body}
      </pre>
    </div>
  );
}
