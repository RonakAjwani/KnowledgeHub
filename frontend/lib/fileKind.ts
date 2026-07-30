/**
 * Filename/mime -> a file-type swatch (icon + label + colour), shared by
 * `DocumentManager`'s rows and `SourcePane`'s header so the two never drift
 * into slightly different treatments of "this is a PDF."
 *
 * Blue/violet rather than reusing emerald/amber/red: those three already carry
 * fixed meaning elsewhere (success/warning/danger on `Badge`, verified states
 * on `CitationChip`) - a red PDF swatch would read as "this document errored."
 */

import { FileCode2, FileText, type LucideIcon } from "lucide-react";

export interface FileKind {
  label: string;
  Icon: LucideIcon;
  swatchClassName: string;
}

const PDF: FileKind = {
  label: "PDF",
  Icon: FileText,
  swatchClassName:
    "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400",
};

const MARKDOWN: FileKind = {
  label: "Markdown",
  Icon: FileCode2,
  swatchClassName:
    "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
};

const TEXT: FileKind = {
  label: "Text",
  Icon: FileText,
  swatchClassName:
    "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
};

export function fileKind(filename: string, mime: string): FileKind {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  if (mime === "application/pdf" || ext === ".pdf") return PDF;
  if (mime === "text/markdown" || ext === ".md" || ext === ".markdown") {
    return MARKDOWN;
  }
  return TEXT;
}
