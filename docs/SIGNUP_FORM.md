# Signup Form (`/signup`)

## Overview

The signup page uses a **2-step form** designed to maximize conversion:

1. **Step 1 — "Personalize Your Alerts"**: State dropdown + topic interest cards. Low-friction, zero-commitment fields that feel like customization rather than data collection. No required fields — users can skip straight to Step 2.
2. **Step 2 — "Almost Done!"**: Name (optional) + email (required). By the time users reach this step they've already invested effort in Step 1, creating commitment momentum (sunk-cost effect).

**Why 2 steps?** Single-page forms with 4+ fields suffer from perceived complexity. Splitting into two steps hides the email ask behind a low-friction first step, reducing bounce rates. The "Next" button acts as a micro-commitment. Research shows this pattern increases completion by 20-40% for email signup forms.

**No progress bar.** On a 2-step form, progress indicators add visual noise without improving completion (Conrad et al., NNGroup). A simple "Step 1 of 2" text label is sufficient.

---

## URL Prepopulation

All query parameters are optional and silently ignored if invalid. The user always starts on Step 1 regardless of which parameters are present — no auto-advancing.

| Parameter | Type | Description | Matching |
|-----------|------|-------------|----------|
| `state` | string | US state | Case-insensitive. Accepts abbreviation (`CA`, `ny`), full name (`California`), or slug (`new-york`). Unknown values ignored. |
| `topics` | string | Comma-separated topic names | Case-insensitive match against: Stocks, Personal Injury, Product Recalls, Drugs & Pharmacy, Financial, Online/Privacy |
| `name` | string | Full name | Pre-fills name field (shown in Step 2) |
| `e` | string | Email address | Pre-fills email field (shown in Step 2). Short param name intentional for cleaner campaign URLs. |
| `utm_source` | string | UTM source | Passed through to subscriber record |
| `utm_campaign` | string | UTM campaign | Passed through to subscriber record |

### Behavior Rules

- **Step 1 fields** (`state`, `topics`) are filled immediately on page load — the user sees them pre-selected when the page renders.
- **Step 2 fields** (`name`, `e`) are pre-filled in the DOM on load so they're ready when the user clicks "Next" and arrives at Step 2.
- **No auto-advance**: Even if all fields are pre-filled via URL, the user always starts on Step 1. This ensures they see and confirm their preferences.
- **State matching priority**: abbreviation (`CA`) → full name (`California`) → slug (`california`) → ignored if no match.

### Example Campaign URLs

**1. Paid ad campaign — targeting California financial settlements**
```
/signup?state=CA&topics=Financial,Stocks&utm_source=google&utm_campaign=ca-financial-2026
```
User lands on Step 1 with California selected and Financial + Stocks topics checked. UTM params tracked.

**2. Email re-engagement — subscriber who previously unsubscribed**
```
/signup?e=jane@example.com&name=Jane+Doe&state=FL&topics=Personal+Injury
```
All fields pre-filled. User sees Florida + Personal Injury on Step 1, clicks Next, sees their name and email ready on Step 2.

**3. Simple email-only campaign — newsletter CTA**
```
/signup?e=john@example.com
```
Step 1 shows defaults (no state, no topics). Step 2 has email pre-filled. Minimal friction.

---

## Backend Fields

All fields sent to `addSubscriber()` on form submission:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Subscriber email address |
| `name` | string | No | Full name |
| `state` | string | No | State slug (e.g., `california`, `new-york`) or empty |
| `topics` | string[] | No | Array of selected topic names (e.g., `["Stocks", "Financial"]`) |
| `source` | string | Auto | Always `signup_page` for this form |
| `utm_source` | string | No | From URL param |
| `utm_campaign` | string | No | From URL param |

### Supabase Table: `subscribers`

The `state` and `topics` columns must exist on the `subscribers` table:

```sql
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS topics jsonb;
```

The upsert uses `(site_id, email)` as the conflict key. Re-subscribing resets `status` to `active` and `unsubscribed_at` to `null`, and updates state/topics preferences.

---

## Mobile Behavior

- **Touch targets**: All interactive elements (buttons, topic cards, dropdown, inputs) have a minimum height of 48px (exceeds 44px guideline).
- **Layout**: Single-column on all screen sizes. Topic cards use a 2-column grid that stacks naturally.
- **No horizontal overflow**: Tested down to 320px viewport width.
- **Scroll on step change**: Smooth scroll to top when navigating between steps.
- **Focus management**: On Step 2, the email input is auto-focused (or name, if email is pre-filled).
