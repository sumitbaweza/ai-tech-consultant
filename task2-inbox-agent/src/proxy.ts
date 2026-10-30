// Runs before every matched request (Next.js 16 renamed middleware to proxy). Auth.js checks
// the session cookie and sends anyone without one to /signin, via the `authorized` callback.
export { auth as proxy } from "@/auth";

export const config = {
  // Everything except Auth.js's routes, the sign-in page and Next.js's static files
  matcher: ["/((?!api/auth|signin|_next/static|_next/image|favicon.ico).*)"],
};
