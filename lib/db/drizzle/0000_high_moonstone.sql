CREATE TABLE "questions" (
	"id" serial PRIMARY KEY NOT NULL,
	"question_number" integer NOT NULL,
	"category" text NOT NULL,
	"text" text NOT NULL,
	"options" jsonb NOT NULL,
	"correct_letter" text NOT NULL,
	"explanation" text NOT NULL,
	"question_type" text DEFAULT 'single' NOT NULL,
	"image_url" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "answers" (
	"id" serial PRIMARY KEY NOT NULL,
	"session_id" text NOT NULL,
	"question_id" integer NOT NULL,
	"selected_letter" text NOT NULL,
	"correct" boolean NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "sessions" (
	"id" serial PRIMARY KEY NOT NULL,
	"session_id" text NOT NULL,
	"questions_answered" integer DEFAULT 0 NOT NULL,
	"is_subscribed" boolean DEFAULT false NOT NULL,
	"subscription_end_date" timestamp with time zone,
	"email" text,
	"stripe_customer_id" text,
	"stripe_subscription_id" text,
	"referral_code" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "sessions_session_id_unique" UNIQUE("session_id")
);
--> statement-breakpoint
CREATE TABLE "affiliates" (
	"id" serial PRIMARY KEY NOT NULL,
	"code" text NOT NULL,
	"name" text NOT NULL,
	"stripe_connect_id" text,
	"commission_pct" integer DEFAULT 50 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "affiliates_code_unique" UNIQUE("code")
);
