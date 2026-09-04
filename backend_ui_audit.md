# 🏢 Real Estate CRM — Backend vs UI Feature Audit Report
**Prepared by:** Solution Engineer  
**Date:** August 26, 2026  
**Codebase:** `real_estate_backend` — FastAPI (Python) backend + Next.js dashboard  

---

## Executive Summary

The backend is a **production-grade, fully-automated Real Estate CRM** with AI Voice Agent, WhatsApp automation, multi-tenant architecture, and a rich sales pipeline engine. However, the Next.js dashboard currently exposes **only ~25% of the backend's capabilities**. A significant majority of features — especially the AI automation layer, financial tracking, and lead management sub-features — are **fully implemented and API-ready but completely invisible in the UI**.

---

## 📊 Current UI Coverage Map

| Section | UI Page Exists? | API Calls Made | Completeness |
|---|---|---|---|
| Dashboard Home | ✅ Yes | 8 stat counts | 🟡 Read-only stats only |
| Leads List | ✅ Yes | List only | 🔴 No filters, no actions |
| Lead Detail | ✅ Yes | Lead + customer + interests | 🔴 Actions disabled (greyed out) |
| Customers | ✅ Yes | List only | 🔴 No create/edit/detail |
| Bookings | ✅ Yes | List only | 🔴 No create/update/cancel |
| Appointments | ✅ Yes | List only | 🔴 No scheduling/cancel actions |
| Sales | ✅ Yes | List only | 🔴 No payments, no balance |
| Projects | ✅ Yes | List only | 🔴 No create/update/detail |
| Properties | ✅ Yes | List only | 🔴 No milestones, no reserve |
| Ownership & Resale | ✅ Yes | List only | 🔴 No co-owners, no resale mgmt |
| WhatsApp Conversations | ✅ Yes | Message list | 🟡 No send, no templates |
| Voice Calls | ✅ Yes | Call list | 🔴 No job creation, no scheduling |
| Sales Handoffs | ✅ Yes | Handoff list | 🔴 Accept button missing |
| Team | ✅ Yes | Users + roles | 🔴 No role assign/remove actions |
| Settings | ✅ Yes | Tenant + me | 🔴 Read-only, no edits |
| Follow-ups | ❌ **MISSING** | None | 🔴 **No page at all** |
| Locations | ❌ **MISSING** | None | 🔴 **No page at all** |
| AI Agent Tools | ❌ **MISSING** | None | 🔴 **No page at all** |
| Call Scheduler | ❌ **MISSING** | None | 🔴 **No UI at all** |
| WhatsApp Templates | ❌ **MISSING** | None | 🔴 **No UI at all** |
| Public Lead Intake | ❌ **MISSING** | None | 🔴 **No embed/config UI** |

---

## 🔴 CRITICAL GAPS — Features With Zero UI Exposure

### 1. Follow-ups Module (`/api/v1/followups`)
**Backend:** Fully implemented with 5 endpoints. Follow-ups are the CRM's task/reminder engine — every call, WhatsApp, and site-visit action can create follow-up tasks.

| Endpoint | Method | Business Value |
|---|---|---|
| `POST /followups` | Create | Schedule a task for any lead |
| `GET /followups/{id}` | Read | View a specific follow-up |
| `GET /followups` | List | Full task board with 7 filters |
| `PATCH /followups/{id}` | Update | Reschedule or change details |
| `POST /followups/{id}/complete` | Complete | Mark done with completion notes |

**Filters available (not used):** `lead_id`, `customer_id`, `agent_id`, `status`, `follow_up_type`, `scheduled_before`, `scheduled_after`

> [!CAUTION]
> **The AI Voice Agent creates follow-ups automatically** (`create_follow_up_tool`, `reschedule_follow_up_tool`, `cancel_follow_up_tool`). These tasks exist in the database but sales agents have **no UI to see or act on them**. This is the biggest operational gap in the CRM.

**Suggested UI:** A dedicated **Task Board** or **My Follow-ups** page showing overdue/today/upcoming tasks with filters. Also embed upcoming follow-ups in the Lead Detail page right-sidebar.

---

### 2. Locations Master Data (`/api/v1/locations`)
**Backend:** Full CRUD — create, read, update, list with city/active filters.

| Endpoint | Business Value |
|---|---|
| `POST /locations` | Define service areas (cities/localities) |
| `GET /locations` | List all active areas |
| `PATCH /locations/{id}` | Deactivate areas or rename |

**Why it matters:** Locations feed into lead filtering and property search. If an admin can't manage locations through the UI, they can't configure the CRM properly.

**Suggested UI:** A **Locations** tab inside Settings → a simple list + add/edit modal.

---

### 3. AI Agent Call Job Management (`/api/v1/agent/call-jobs`)
**Backend:** The backend has a full **automated AI dialer** with:
- Create call jobs (`POST /agent/call-jobs`)
- Get eligible jobs (`GET /agent/call-jobs/eligible`)
- Prepare call (`POST /agent/calls/prepare`)
- Start call (`POST /agent/calls/start`)
- Complete call (`POST /agent/calls/complete`)
- Reconcile stuck jobs (`POST /agent/calls/reconcile-stuck`)

**Background worker** (`call_scheduler.py`): Runs every 15 seconds, Redis-locked, automatically places calls for scheduled jobs.

> [!IMPORTANT]
> Business owners have **zero visibility** into this automated dialer. They cannot see the call queue, cannot manually trigger a call for a specific lead, and cannot see which leads are awaiting calls. The Voice Calls page shows _completed_ calls but not the job queue.

**Suggested UI:**
- **Call Queue panel** in the Calls page showing jobs in `queued` / `ready` / `calling` states
- **"Queue AI Call"** button on the Lead Detail page (connects to `POST /agent/call-jobs`)
- **Stuck Jobs Alert** widget on the Dashboard

---

### 4. Lead Notes (`/api/v1/leads/{id}/notes`)
**Backend:** Full notes CRUD on leads — add notes, list notes (with author and timestamp).

**Current UI:** Lead Detail page shows the `notes` field from the lead record, but **never fetches or displays the notes list** (`GET /leads/{id}/notes`). There is no way to add a new note from the UI.

> [!IMPORTANT]
> Sales agents call `add_lead_note` AI tool and these notes are stored permanently, but no human staff member can **read the AI's notes** in the dashboard. This defeats the purpose of AI-to-human knowledge transfer.

**Suggested UI:** A **Notes & Activity Timeline** section on the Lead Detail page with a text area to add notes and a scrollable list of all past notes.

---

### 5. Lead Assignment (`POST /leads/{id}/assign`)
**Backend:** A dedicated assignment endpoint with permission-gated logic (`LEAD_ASSIGN` permission).

**Current UI:** No assignment functionality anywhere. The lead detail page is fully read-only.

**Suggested UI:** A **"Assign to Agent"** dropdown on the Lead Detail right-sidebar.

---

### 6. WhatsApp — Send Messages & Templates
**Backend:**
- `POST /whatsapp/messages` — Send any message to a customer
- `GET /whatsapp/templates` — List approved/pending Meta WhatsApp templates

**Current UI (Conversations page):** Shows message history list only. No compose box, no template picker.

> [!WARNING]
> Staff cannot send WhatsApp messages from the dashboard at all, even though the backend fully handles 24-hour session window enforcement, template vs. free-form message routing, and delivery tracking.

**Suggested UI:**
- A **compose/send panel** in the conversation view
- A **Templates** tab or modal for selecting approved templates

---

### 7. Sales Handoff — Accept Action
**Backend:** `POST /agent/sales-handoffs/{id}/accept` — places a real Superfone click-to-call bridge and marks the handoff accepted.

**Current UI (Handoffs page):** Lists handoffs with status but **the Accept button is not connected**. This is the most actionable feature for a sales agent — they should be able to click "Accept" and get a bridged call instantly.

**Suggested UI:** An **"Accept & Bridge Call"** button on each open handoff row in the Handoffs page.

---

### 8. Property — Construction Milestones (`/api/v1/properties/{id}/construction-milestones`)
**Backend:** 
- `POST /properties/{id}/construction-milestones` — Register a stage (foundation, structure, brickwork, electrical, finishing, handover)
- `PATCH /properties/{id}/construction-milestones/{milestone}` — Update status/dates/verifier

**Current UI:** Property list/detail pages exist but show no construction progress.

**Suggested UI:** A **Construction Timeline** component on the Property Detail page showing milestone progress bars and dates.

---

### 9. Property Reserve (`POST /properties/{id}/reserve`)
**Backend:** Concurrently reserves a property using `SELECT FOR UPDATE` row-level locking — prevents double-booking race conditions.

**Current UI:** No reserve/hold button anywhere in the Properties UI.

**Suggested UI:** A **"Reserve Unit"** button on Property Detail with a modal to link to a lead.

---

### 10. Property Aggregated Detail View (`GET /properties/{id}/detail`)
**Backend:** Returns a rich aggregated object:
- Base property fields
- Project + location context
- Construction milestones
- Current owner + full ownership history
- Co-owners per period
- Open resale listing
- Current price

**Current UI:** The property list fetches basic `GET /properties/{id}` only. The `/detail` endpoint is never called.

**Suggested UI:** Use this endpoint for the Property Detail page to show all context in one load.

---

### 11. Sale Payments & Balance Tracking
**Backend:**
- `POST /property-sales/{id}/payments` — Record an installment
- `GET /property-sales/{id}/payments` — List all payments with status filter
- `PATCH /property-sales/{id}/payments/{payment_id}` — Correct bounced/refunded payments
- `GET /property-sales/{id}/balance` — Outstanding balance rollup

**Current UI (Sales page):** Shows sale records only. No payment tracking, no balance view.

> [!CAUTION]
> This is critical for real estate finance. A developer selling ₹1CR+ properties needs to track installments. The backend handles this perfectly but the UI shows nothing.

**Suggested UI:** In the Sale Detail page — a **Payment Schedule table** with record/update buttons and a **Balance Summary card** (total sale amount, discount, received, outstanding).

---

### 12. Ownership — Co-Owners & Resale Management
**Backend (all 100% implemented, zero UI):**
- `POST /property-ownerships/{id}/co-owners` — Add joint owner
- `GET /property-ownerships/{id}/co-owners` — List co-owners
- `DELETE /property-ownerships/{id}/co-owners/{co_owner_id}` — Remove co-owner
- `PATCH /property-ownerships/{id}` — Verify ownership record
- `GET /property-ownerships/{id}/history` — Full chain of ownership

**Current UI (Ownership page):** Lists ownerships only — no co-owners, no history chain, no verification action.

---

### 13. Tenant Settings — Edit Company Profile
**Backend:** `PATCH /tenants/{id}` — Update name, contact, address, branding.

**Current UI (Settings page):** Shows company profile as **read-only rows** only. There's no edit form connected.

**Suggested UI:** Convert the Company Profile card into editable fields with a Save button.

---

### 14. Tenant Integration Configs (Super-Admin)
**Backend:**
- `PUT/GET /tenants/{id}/whatsapp-config` — Configure Meta WhatsApp API credentials
- `PUT/GET /tenants/{id}/superfone-crm-config` — Configure Superfone CRM webhook secret

**Current UI:** Zero exposure. A super-admin has no dashboard surface to configure these integrations.

**Suggested UI:** An **Integrations** sub-tab in Settings for super-admins.

---

### 15. Role Management Actions (Team Page)
**Backend:**
- `POST /users/{id}/roles` — Assign a role (with escalation prevention)
- `DELETE /users/{id}/roles/{role_id}` — Remove a role (self-removal blocked)

**Current UI (Team page):** Shows user roles as badges only. No assign or remove functionality.

**Suggested UI:** Inline role management — a **"+ Add Role"** button and **✕** remove per role badge.

---

### 16. Public Lead Intake Widget (`POST /public/intake/{tenant_slug}/leads`)
**Backend:** A rate-limited, JWT-free public endpoint for website embeds and campaign landing pages. It auto-creates a customer + lead and queues an AI outbound call.

**Current UI:** No embed code, no configuration panel, no widget builder.

**Suggested UI:** A **"Lead Capture Widget"** section in Settings showing the embed URL and a code snippet for the business website.

---

### 17. Pre-Call Context Snapshot (`GET /agent/context/{lead_id}`)
**Backend:** Returns an authoritative, deterministic snapshot of everything the AI Voice Agent knows before a call — lead data, customer preferences, recent call summaries, property interests, open follow-ups.

**Current UI:** Never fetched. Invisible to staff.

**Suggested UI:** A **"AI Pre-Call Briefing"** collapsible card on the Lead Detail page — summary of what the AI knows about this lead before calling.

---

## 🟡 PARTIAL GAPS — Features Present But Incomplete

### 18. Lead Detail — Quick Actions (Greyed Out)
The Lead Detail page has `Call Now`, `Send WhatsApp`, and `Schedule Site Visit` buttons that are **hardcoded `disabled`**. The backend has all three:
- `POST /agent/call-jobs` (Call Now)
- `POST /whatsapp/messages` (Send WhatsApp)
- `POST /appointments` (Schedule Site Visit)

**Fix:** Wire up these buttons with API calls and confirmation dialogs.

---

### 19. Appointments — Cancel Action Missing
**Backend:** `POST /appointments/{id}/cancel` with cancellation reason.
**UI:** Lists appointments but no cancel button.

---

### 20. Bookings — Cancel/Conversion Status Missing
**Backend:** `PATCH /property-bookings/{id}` can cancel a booking. Bookings auto-convert to `converted` when a sale is recorded.
**UI:** No status update action in UI. Conversion flow is opaque.

---

### 21. Dashboard — Missing Key Business Metrics
The dashboard stat tiles exist but many valuable counts are missing:

| Missing Stat | Backend Endpoint | Value |
|---|---|---|
| Properties Available (not sold) | `GET /properties?status=available` | Inventory health |
| Follow-ups Due Today | `GET /followups?scheduled_before=today` | Agent task load |
| Overdue Follow-ups | `GET /followups?status=pending&scheduled_before=now` | SLA monitoring |
| Properties Under Construction | `GET /properties?status=under_construction` | Project tracking |
| Pending Sale Payments | `GET /property-sales/{id}/balance` | Revenue pipeline |
| WhatsApp Conversations (last 24h) | `GET /whatsapp/messages` | Engagement rate |

---

### 22. Lead List — No Filtering or Sorting
**Backend supports 8 filters:** `customer_id`, `status`, `sales_stage`, `assigned_sales_agent_id`, `campaign_id`, `property_type_id`, `min_budget`, `max_budget`, `query` (free text).

**UI:** Fetches `/leads?page_size=50` with no filters. No search bar, no filter panel.

---

### 23. Customers — No Detail Page or Create Form
**Backend:** Full CRUD — `POST`, `GET /{id}`, `GET /` (with 4 filters), `PATCH`.
**UI:** Lists customers in a table. No `Link` to a detail page, no create button.

---

## 📋 Priority Prioritization for Business Impact

| Priority | Feature | Effort | Business Impact |
|---|---|---|---|
| 🔥 P0 | Follow-ups Task Board | Medium | Sales agents miss AI-created tasks |
| 🔥 P0 | Lead Notes (read + write) | Low | AI knowledge invisible to humans |
| 🔥 P0 | Handoff Accept Button | Low | Core sales workflow broken |
| 🔥 P0 | Sale Payment Tracking | Medium | Financial visibility gap |
| 🔥 P0 | Call Queue / AI Dialer UI | Medium | Dialer is a black box |
| ⚡ P1 | Lead Assignment | Low | Pipeline management |
| ⚡ P1 | WhatsApp Send + Templates | Medium | Direct customer communication |
| ⚡ P1 | Lead Filters + Search | Low | Operational efficiency |
| ⚡ P1 | Property Reserve Button | Low | Prevent double bookings |
| ⚡ P1 | Construction Milestones | Medium | Project tracking |
| 🔵 P2 | Quick Actions (Lead Detail) | Medium | Workflow speedup |
| 🔵 P2 | Settings Edit + Integrations | Medium | Admin configurability |
| 🔵 P2 | Tenant Public Intake Widget | Medium | Marketing automation |
| 🔵 P2 | Locations Master Data | Low | CRM configuration |
| 🔵 P2 | Dashboard Extra Metrics | Low | Management reporting |
| 🟢 P3 | Property Aggregated Detail | Low | Data richness |
| 🟢 P3 | Ownership Co-owners | Low | Legal compliance |
| 🟢 P3 | Role Management Actions | Low | Access control |

---

## 📁 Backend Modules Summary

```
backend/app/
├── agent/          ✅ Full AI call pipeline, tools, handoffs — ~5% visible in UI
├── appointments/   ✅ Full CRUD — cancel action missing
├── auth/           ✅ JWT/Supabase — used
├── bookings/       ✅ Full CRUD — list-only in UI
├── conversations/  ✅ WhatsApp history — no send UI
├── customers/      ✅ Full CRUD — list-only, no detail page
├── followups/      ✅ Full CRUD — ZERO UI EXPOSURE
├── integrations/   ✅ Superfone + WhatsApp — hidden
├── leads/          🟡 List + detail — notes/assign/filters missing
├── locations/      ✅ Full CRUD — ZERO UI EXPOSURE
├── ownerships/     ✅ Full — list-only, co-owners/verify missing
├── projects/       ✅ Full CRUD — list-only
├── properties/     ✅ Rich API — milestones/reserve/detail endpoint missing
├── public_intake/  ✅ Rate-limited intake — no admin UI
├── sales/          ✅ Full + payments — payments completely missing in UI
├── tenants/        ✅ Full + integrations config — read-only settings
├── users/          🟡 List + roles — no assign/remove actions
├── voice/          ✅ WebSocket AI pipeline — no UI
├── webhooks/       ✅ Superfone + WhatsApp webhooks — infrastructure only
├── whatsapp/       ✅ Send + templates + history — send not wired
└── workers/        ✅ Background call scheduler — no monitoring UI
```

---

## 🎯 Recommended Immediate Actions

1. **Enable Lead Notes tab** on Lead Detail (2 API calls already typed in the page, just `notes` list is missing)
2. **Wire the 3 greyed-out Quick Action buttons** on Lead Detail — `POST /agent/call-jobs`, `POST /whatsapp/messages`, `POST /appointments`
3. **Add Follow-ups sidebar widget** on Lead Detail — `GET /leads/{id}/followups` is one API call away
4. **Connect "Accept" on Handoffs page** — single `POST /agent/sales-handoffs/{id}/accept`
5. **Add Sale Detail page** with payment list and balance — needed for financial tracking

> [!NOTE]
> All backend code is stable, tested, and production-ready. **No backend changes are required** for any of the above. Every gap is purely a frontend implementation task.
