CREATE TABLE "inbox"."analyses" (
	"email_id" integer PRIMARY KEY NOT NULL,
	"status" text NOT NULL,
	"intent" text,
	"product_area" text,
	"sentiment" text,
	"urgency" text,
	"language" text,
	"risk_flags" text[] DEFAULT '{}' NOT NULL,
	"guard_flags" text[] DEFAULT '{}' NOT NULL,
	"questions" text[] DEFAULT '{}' NOT NULL,
	"summary" text,
	"error" text,
	"prompt_version" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "inbox"."autonomy_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"category_key" text NOT NULL,
	"kind" text NOT NULL,
	"detail" text NOT NULL,
	"draft_id" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "inbox"."category_state" (
	"category_key" text PRIMARY KEY NOT NULL,
	"unedited_streak" integer DEFAULT 0 NOT NULL,
	"state" text DEFAULT 'manual' NOT NULL,
	"active_playbook_version" integer DEFAULT 0 NOT NULL,
	"last_reason" text,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "inbox"."drafts" (
	"id" serial PRIMARY KEY NOT NULL,
	"email_id" integer NOT NULL,
	"category_key" text NOT NULL,
	"subject" text NOT NULL,
	"body" text NOT NULL,
	"facts" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"brain_status" text NOT NULL,
	"confidence" real,
	"open_questions" text[] DEFAULT '{}' NOT NULL,
	"cited_source_ids" text[] DEFAULT '{}' NOT NULL,
	"prompt_version" integer NOT NULL,
	"playbook_version" integer DEFAULT 0 NOT NULL,
	"example_draft_ids" integer[] DEFAULT '{}' NOT NULL,
	"blockers" text[] DEFAULT '{}' NOT NULL,
	"status" text DEFAULT 'ready' NOT NULL,
	"scheduled_for" timestamp with time zone,
	"sent_at" timestamp with time zone,
	"sent_by" text,
	"final_subject" text,
	"final_body" text,
	"edited" boolean,
	"edit_ratio" real,
	"sampled_for_review" boolean DEFAULT false NOT NULL,
	"review_result" text,
	"reviewed_at" timestamp with time zone,
	"learned_at" timestamp with time zone,
	"learn_error" text,
	"send_error" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "inbox"."playbooks" (
	"id" serial PRIMARY KEY NOT NULL,
	"category_key" text NOT NULL,
	"version" integer NOT NULL,
	"rules" jsonb NOT NULL,
	"note" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "playbooks_category_version_key" UNIQUE("category_key","version")
);
--> statement-breakpoint
CREATE TABLE "inbox"."product_areas" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"active" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "product_areas_name_unique" UNIQUE("name")
);
--> statement-breakpoint
CREATE TABLE "inbox"."settings" (
	"id" integer PRIMARY KEY DEFAULT 1 NOT NULL,
	"auto_send_after" integer DEFAULT 20 NOT NULL,
	"sample_every" integer DEFAULT 10 NOT NULL,
	"undo_minutes" integer DEFAULT 5 NOT NULL,
	"voice_guide" text DEFAULT '' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "inbox"."emails" ADD COLUMN "message_id_header" text;--> statement-breakpoint
ALTER TABLE "inbox"."analyses" ADD CONSTRAINT "analyses_email_id_emails_id_fk" FOREIGN KEY ("email_id") REFERENCES "inbox"."emails"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "inbox"."drafts" ADD CONSTRAINT "drafts_email_id_emails_id_fk" FOREIGN KEY ("email_id") REFERENCES "inbox"."emails"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "drafts_email_idx" ON "inbox"."drafts" USING btree ("email_id");--> statement-breakpoint
CREATE INDEX "drafts_status_idx" ON "inbox"."drafts" USING btree ("status");