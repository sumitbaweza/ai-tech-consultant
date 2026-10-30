import { redirect } from "next/navigation";
import { AuthError } from "next-auth";
import { signIn, signInOptions } from "@/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

async function googleSignIn(): Promise<void> {
  "use server";
  await signIn("google", { redirectTo: "/mail/inbox" });
}

async function demoSignIn(formData: FormData): Promise<void> {
  "use server";
  const password = formData.get("password");
  try {
    await signIn("demo", {
      password: typeof password === "string" ? password : "",
      redirectTo: "/mail/inbox",
    });
  } catch (error) {
    // A wrong password is an AuthError. Anything else, including Next's redirect, goes on up.
    if (error instanceof AuthError) redirect("/signin?error=demo");
    throw error;
  }
}

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const options = signInOptions();

  return (
    <main className="bg-muted grid min-h-dvh place-items-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">Inbox Agent</CardTitle>
          <CardDescription>Sign in to read and answer the support inbox.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          {options.google && (
            <form action={googleSignIn}>
              <Button type="submit" className="w-full">
                Sign in with Google
              </Button>
            </form>
          )}
          {options.demo && (
            <form action={demoSignIn} className="flex flex-col gap-3">
              <Label htmlFor="password">Demo password (INBOX_DEMO_PASSWORD in .env)</Label>
              <Input
                id="password"
                name="password"
                type="password"
                required
                autoComplete="current-password"
              />
              {error === "demo" && <p className="text-destructive text-sm">That password is wrong.</p>}
              <Button type="submit" variant={options.google ? "outline" : "default"}>
                Sign in to the demo inbox
              </Button>
            </form>
          )}
          {!options.google && !options.demo && (
            <p className="text-sm">
              No way to sign in is set up. In .env, set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or
              INBOX_DEMO_PASSWORD with INBOX_MODE=fixtures.
            </p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
