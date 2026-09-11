# WY Wallet Mobile

A separate Android-first Flutter version of WY Wallet. It does **not** reuse or modify the Streamlit UI. The mobile data model is isolated in `wallet_*` Supabase tables so multiple friends can safely use the same backend.

## Included in V1

- Google account sign-in through Supabase Auth
- Strict per-user data isolation with Row Level Security
- Dashboard: current-month income, expense and balance
- Fixed, non-zoomable 12-month spending chart
- Manual transaction entry and editing
- Transaction search, delete confirmation and pull-to-refresh
- Annual report with fixed 1–12 month bars and category pie/ranking
- AI receipt scan from camera or photo gallery
- Gemini 3.6 Flash receipt extraction through a Supabase Edge Function
- AI monthly spending insight through a Supabase Edge Function
- User categories and Google-account logout
- System light/dark theme

## Security architecture

The APK contains only the Supabase project URL and **publishable key**. This is expected for a mobile client when RLS is enabled.

The Gemini API key is **never** embedded in the APK. `GEMINI_API_KEY` is stored as a Supabase Edge Function secret, and authenticated users call the function through Supabase.

The existing Streamlit `transactions` and `categories` tables are not touched. Mobile uses:

- `wallet_profiles`
- `wallet_categories`
- `wallet_transactions`

## One-time Supabase setup

### 1. Database

Open Supabase SQL Editor and run:

`supabase/migrations/001_mobile_wallet.sql`

This creates the mobile-only tables, default categories, indexes, RLS policies and a new-user trigger.

### 2. Google login

In Google Cloud Console create/configure an OAuth client for Supabase Google login.

Google OAuth authorized redirect URI:

`https://<YOUR_PROJECT_REF>.supabase.co/auth/v1/callback`

Then in Supabase Dashboard:

- Authentication → Providers → Google → enable it and paste the Google client ID/secret.
- Authentication → URL Configuration → Redirect URLs → add:

`com.wuyaoenterprise.wywallet://login-callback`

The Android manifest is patched during build to handle this callback.

### 3. Gemini secret and Edge Functions

Set the Edge Function secret:

`GEMINI_API_KEY=<your Gemini API key>`

Deploy both functions:

- `analyze-receipt`
- `ai-insights`

Both functions verify the signed-in Supabase user before calling Gemini 3.6 Flash.

## GitHub build secrets

Add these repository Actions secrets:

- `MOBILE_SUPABASE_URL`
- `MOBILE_SUPABASE_PUBLISHABLE_KEY`

The publishable key can be obtained from Supabase → Connect / API keys. Do **not** use a service-role/secret key in the APK.

## Build APK

GitHub Actions → **Build WY Wallet APK** → Run workflow.

Enter a version such as `0.1.0`. The workflow builds an Android APK and publishes it under GitHub Releases for sharing.

The `Mobile app CI` workflow also builds a placeholder debug APK purely to verify that the Flutter source compiles. That debug artifact is not connected to your real Supabase project.

## APK signing

The initial workflow produces an installable release APK using the Flutter-generated signing setup. Before distributing updates broadly or publishing to Google Play, configure a permanent private Android signing key so every future version can update the installed app without requiring uninstall/reinstall.

## Future iPhone version

The app is Flutter-based. The same Dart UI/data code can be reused for iOS after adding the iOS platform project, Apple signing and iOS deep-link configuration.
