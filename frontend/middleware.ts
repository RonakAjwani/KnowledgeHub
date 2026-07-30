/**
 * Clerk route protection - active only when a publishable key is configured.
 *
 * With no key, this is a pass-through so the app runs unauthenticated against a
 * backend in `AUTH_MODE=dev`. `clerkMiddleware()` with no key throws at request
 * time, which would turn "no Clerk account yet" into a 500 on every page.
 */

import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

/**
 * Constructed once, and only when Clerk is actually enabled - the SDK logs its
 * own deprecation notice (in favour of resource-based `auth.protect()` calls
 * per page) the moment this is called, and that would otherwise print on every
 * dev server boot even in `AUTH_MODE=dev`, where Clerk is not in the picture at
 * all. Left as path-based middleware protection rather than migrated to the
 * resource-based pattern: this project has no live Clerk key to verify a
 * migration against, and a working-but-soon-to-be-legacy check beats an
 * unverified rewrite of it.
 */
const isPublicRoute = clerkEnabled
  ? createRouteMatcher(["/", "/sign-in(.*)", "/sign-up(.*)"])
  : null;

export default clerkEnabled
  ? clerkMiddleware(async (auth, req) => {
      if (!isPublicRoute!(req)) await auth.protect();
    })
  : () => NextResponse.next();

export const config = {
  matcher: [
    // Skip Next internals and static files unless referenced in a search param.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
