ALTER TYPE "inbox"."folder" ADD VALUE 'archived';--> statement-breakpoint
ALTER TABLE "inbox"."mailboxes" ADD COLUMN "history_id" text;--> statement-breakpoint
ALTER TABLE "inbox"."mailboxes" ADD COLUMN "last_synced_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "inbox"."mailboxes" ADD COLUMN "last_sync_error" text;--> statement-breakpoint
ALTER TABLE "inbox"."mailboxes" ADD COLUMN "needs_sign_in" boolean DEFAULT false NOT NULL;