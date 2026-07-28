/**
 * Clerk route protection — active only when a publishable key is configured.
 *
 * With no key, this is a pass-through so the app runs unauthenticated against a
 * backend in `AUTH_MODE=dev`. `clerkMiddleware()` with no key throws at request
 * time, which would turn "no Clerk account yet" into a 500 on every page.
 */

import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

export default clerkEnabled ? clerkMiddleware() : () => NextResponse.next();

export const config = {
  matcher: [
    // Skip Next internals and static files unless referenced in a search param.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
