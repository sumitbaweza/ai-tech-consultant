-- db/init/01-schemas.sh creates the inbox schema, owned by the inbox role, which may not
-- create schemas itself; so drizzle-kit's CREATE SCHEMA line was removed from this file.
CREATE TYPE "inbox"."folder" AS ENUM('inbox', 'drafts', 'sent', 'spam', 'trash');--> statement-breakpoint
CREATE TABLE "inbox"."emails" (
	"id" serial PRIMARY KEY NOT NULL,
	"mailbox_id" integer NOT NULL,
	"source_id" text NOT NULL,
	"thread_id" text NOT NULL,
	"folder" "inbox"."folder" NOT NULL,
	"from_name" text NOT NULL,
	"from_address" text NOT NULL,
	"to_addresses" text[] NOT NULL,
	"subject" text NOT NULL,
	"body_text" text NOT NULL,
	"received_at" timestamp with time zone NOT NULL,
	"is_read" boolean DEFAULT false NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "emails_mailbox_source_key" UNIQUE("mailbox_id","source_id")
);
--> statement-breakpoint
CREATE TABLE "inbox"."llm_calls" (
	"id" serial PRIMARY KEY NOT NULL,
	"prompt" varchar(100) NOT NULL,
	"prompt_version" integer,
	"model" varchar(100) NOT NULL,
	"input_tokens" integer DEFAULT 0 NOT NULL,
	"output_tokens" integer DEFAULT 0 NOT NULL,
	"cache_read_tokens" integer DEFAULT 0 NOT NULL,
	"cache_write_tokens" integer DEFAULT 0 NOT NULL,
	"cost_usd" numeric(12, 6) NOT NULL,
	"latency_ms" integer NOT NULL,
	"is_batch" boolean DEFAULT false NOT NULL,
	"success" boolean NOT NULL,
	"stop_reason" varchar(30),
	"error" text,
	"run_tag" varchar(100),
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "inbox"."mailboxes" (
	"id" serial PRIMARY KEY NOT NULL,
	"address" text NOT NULL,
	"provider" text NOT NULL,
	"refresh_token" text,
	"access_token" text,
	"access_token_expires_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "mailboxes_address_unique" UNIQUE("address")
);
--> statement-breakpoint
ALTER TABLE "inbox"."emails" ADD CONSTRAINT "emails_mailbox_id_mailboxes_id_fk" FOREIGN KEY ("mailbox_id") REFERENCES "inbox"."mailboxes"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "emails_folder_list_idx" ON "inbox"."emails" USING btree ("mailbox_id","folder","received_at");