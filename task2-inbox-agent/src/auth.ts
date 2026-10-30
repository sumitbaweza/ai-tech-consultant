import NextAuth, { type NextAuthConfig } from "next-auth";
import type { Provider } from "next-auth/providers";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import { z } from "zod";
import { getDb } from "@/db/client";
import { getEnv } from "@/env";
import { checkDemoPassword } from "@/lib/demo-password";
import { saveGoogleMailbox } from "@/lib/mailbox";

// Read mail, write drafts, send replies. Nothing that deletes or changes settings.
export const GMAIL_SCOPES = [
  "openid",
  "email",
  "profile",
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/gmail.compose",
  "https://www.googleapis.com/auth/gmail.send",
];

export const DEMO_USER = { id: "demo", name: "Demo user", email: "demo@inbox.local" };

/** Which ways to sign in exist depends on .env, so the sign-in page asks this too. */
export function signInOptions() {
  const env = getEnv();
  return {
    google: env.GOOGLE_CLIENT_ID !== undefined && env.GOOGLE_CLIENT_SECRET !== undefined,
    demo: env.INBOX_MODE === "fixtures" && env.INBOX_DEMO_PASSWORD !== undefined,
  };
}

function buildConfig(): NextAuthConfig {
  const env = getEnv();
  const options = signInOptions();
  const providers: Provider[] = [];

  if (options.google) {
    providers.push(
      Google({
        clientId: env.GOOGLE_CLIENT_ID,
        clientSecret: env.GOOGLE_CLIENT_SECRET,
        authorization: {
          // offline + consent: Google returns a refresh token, which the background sync needs
          params: { scope: GMAIL_SCOPES.join(" "), access_type: "offline", prompt: "consent" },
        },
      }),
    );
  }

  if (options.demo) {
    providers.push(
      Credentials({
        id: "demo",
        name: "Demo",
        credentials: { password: { label: "Password", type: "password" } },
        authorize(raw) {
          const parsed = z.object({ password: z.string() }).safeParse(raw);
          if (!parsed.success) return null;
          return checkDemoPassword(parsed.data.password, env.INBOX_DEMO_PASSWORD) ? DEMO_USER : null;
        },
      }),
    );
  }

  return {
    providers,
    // The session lives in a signed cookie (a JWT), so sign-in needs no session table
    session: { strategy: "jwt" },
    pages: { signIn: "/signin" },
    callbacks: {
      // Used by src/proxy.ts: every page except sign-in needs a session
      authorized({ auth }) {
        return auth !== null;
      },
      // `account` is only set at the moment of sign-in
      async jwt({ token, account }) {
        if (account?.provider === "google" && typeof token.email === "string") {
          await saveGoogleMailbox(getDb(), token.email, {
            refreshToken: account.refresh_token,
            accessToken: account.access_token,
            expiresAtSeconds: account.expires_at,
          });
        }
        return token;
      },
    },
  };
}

// A function, not an object: the config reads .env on the first request, not during the build
export const { handlers, auth, signIn, signOut } = NextAuth(buildConfig);
