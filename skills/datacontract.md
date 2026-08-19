# PROJECT DATABASE CONTRACT

## Supabase PostgreSQL — Real Estate CRM + AI Voice Sales Platform

You are working on an existing production-oriented FastAPI backend.

The database has already been designed and hardened separately.

This document defines how the FastAPI application must interact with that database.

---

# 1. DATABASE IS THE SOURCE OF TRUTH

The PostgreSQL database is the authoritative source for persistent business data.

The application must NOT recreate, reinterpret, or redesign the database schema without explicit approval.

The actual Supabase/PostgreSQL migration files are the authoritative implementation.

Before writing backend code:

1. Inspect all migration files.
2. Inspect all tables.
3. Inspect columns and data types.
4. Inspect primary keys.
5. Inspect foreign keys.
6. Inspect unique constraints.
7. Inspect check constraints.
8. Inspect indexes.
9. Inspect triggers.
10. Inspect functions.
11. Inspect RLS policies.
12. Inspect views/materialized views.
13. Inspect role/permission structures.

Do not rely solely on documentation.

---

# 2. DATABASE ARCHITECTURE

The database represents a:

```text
Multi-Tenant Real Estate CRM
+
Property Management
+
Sales Management
+
AI Voice Sales Platform
+
Communication Platform
```

Major domains include:

```text
Tenants
Users
Roles
Permissions

Projects
Properties
Property Pricing
Property Status History
Property Media

Customers
Customer Preferences
Customer Notes

Leads
Lead Sources
Lead Property Interests
Lead Notes
Lead Scores
Lead Score History

Campaigns
Campaign Leads

Sales Agents
Sales Assignments

Follow-ups
Appointments
Appointment History

Calls
Call Participants
Call Messages
Call Events

AI Agent Configurations
AI Sessions
Conversation Summaries
AI Usage

WhatsApp
Communication Logs

Activities
Audit Logs
Notifications

Integrations
Webhook Events
```

Do not assume every table exists exactly under these names.

Verify the actual schema.

---

# 3. MULTI-TENANCY MODEL

The system has two scopes.

## Platform scope

```text
super_admin
```

Super admin:

```text
tenant_id = NULL
scope = GLOBAL
```

Super admin can access authorized data across all tenants.

---

## Tenant scope

Normal users belong to exactly one tenant.

Examples:

```text
admin
sales_manager
sales_agent
viewer
```

Their context is:

```text
tenant_id = their tenant
scope = TENANT
```

Tenant users must never access another tenant's data.

---

# 4. TENANT CONTEXT

FastAPI must derive tenant context from the authenticated user.

Never trust:

```text
tenant_id
```

from:

* query parameters
* request body
* URL parameters
* frontend state
* AI tool input

unless the value is being explicitly selected by an authorized `super_admin`.

For normal users:

```text
authenticated user
        ↓
user record
        ↓
tenant_id
        ↓
RequestContext
```

---

# 5. USER CONTEXT

The backend should maintain a trusted request context similar to:

```python
RequestContext(
    user_id,
    tenant_id,
    role,
    permissions,
    is_super_admin,
)
```

For super admin:

```text
tenant_id = None
is_super_admin = true
```

For normal users:

```text
tenant_id = actual tenant
is_super_admin = false
```

Never construct this context from untrusted frontend input.

---

# 6. IMPORTANT DATABASE RULE

FastAPI must respect all PostgreSQL constraints.

Do not work around:

```text
foreign keys
unique constraints
check constraints
RLS
exclusion constraints
triggers
```

by attempting application-level workarounds.

If PostgreSQL rejects an operation because of an integrity rule, handle the error correctly.

Do not disable constraints.

---

# 7. TENANT-OWNED DATA

Any tenant-owned resource must be accessed within tenant scope.

Examples:

```text
Customer
Lead
Project
Property
Campaign
Sales Agent
Call
Appointment
Follow-up
WhatsApp message
Activity
AI session
```

For normal users:

```text
WHERE tenant_id = current_user_tenant
```

or an equivalent safe repository mechanism.

Never query tenant-owned resources solely by ID if the repository does not enforce tenant scope.

---

# 8. OBJECT-LEVEL AUTHORIZATION

Tenant authorization alone is not sufficient.

Example:

```text
Tenant A
    Lead A
    Customer A
```

The backend must also verify that:

```text
Lead A
belongs to
Customer A
```

when an operation requires that relationship.

Similarly:

```text
Appointment
→ Lead
→ Customer
→ Tenant
```

must be validated where appropriate.

Never assume that because a resource belongs to the tenant, every related resource is automatically valid.

---

# 9. DATABASE ACCESS LAYER

Use:

```text
FastAPI
 ↓
Service
 ↓
Repository
 ↓
SQLAlchemy
 ↓
PostgreSQL
```

Repositories must encapsulate database access.

Do not scatter database queries throughout:

```text
routers
AI tools
webhook handlers
background jobs
```

---

# 10. REPOSITORY RULE

Repositories should answer questions such as:

```text
get customer
find customer by phone
get lead
list leads
search properties
get property
get appointments
get call
```

Repositories should not decide:

```text
whether user is authorized
whether AI should book appointment
whether a lead should be qualified
whether customer should be transferred
```

Those decisions belong to services/authorization layers.

---

# 11. SERVICE RULE

Services implement business workflows.

Example:

```text
LeadService
PropertyService
AppointmentService
CallService
CustomerService
CampaignService
```

Services may coordinate:

```text
multiple repositories
transactions
external providers
history records
activities
notifications
```

---

# 12. CUSTOMER MODEL

A customer represents the underlying person/entity.

A customer may have multiple leads.

Conceptually:

```text
Customer
   │
   ├── Lead 1
   ├── Lead 2
   └── Lead 3
```

Do not create duplicate customers unnecessarily.

Customer matching should primarily use normalized identifying information such as phone/email according to the existing schema rules.

Do not implement unsafe fuzzy matching automatically.

---

# 13. LEAD MODEL

A lead represents a sales opportunity/interaction.

A lead may contain:

```text
customer
source
campaign
stage
status
budget
property interests
assigned sales agent
score
follow-ups
appointments
calls
```

Lead lifecycle changes must follow the business rules.

Do not allow arbitrary stage/status transitions.

---

# 14. PROPERTY MODEL

Properties belong to projects or the appropriate property hierarchy defined by the actual database.

The property database is authoritative for:

```text
availability
status
price
area
type
location
amenities
```

The AI must never rely on stale property information when making a reservation or availability decision.

---

# 15. PROPERTY AVAILABILITY

For AI or sales operations:

```text
search properties
        ↓
get current state
        ↓
verify availability
        ↓
perform operation
```

Do not assume that a property returned by an earlier search is still available.

Always re-check current state before:

```text
hold
reserve
sell
```

---

# 16. PROPERTY RESERVATION

Property reservation is a concurrency-sensitive operation.

Never implement:

```text
SELECT property
IF available
UPDATE property
```

as separate unprotected operations.

Use the existing database transaction/concurrency mechanism.

Only one competing request should successfully reserve the same property.

---

# 17. PROPERTY PRICING

Current price and price history are separate concepts.

The application must distinguish:

```text
current price
```

from:

```text
historical price
```

Do not update historical records to represent a new price.

Create appropriate history records through the established database mechanism.

---

# 18. LEAD PROPERTY INTEREST

A lead can have multiple property interests.

Example:

```text
Lead
 ├── Project A / Plot 101
 ├── Project A / Plot 102
 └── Project B / Villa 12
```

Do not assume one lead has only one property.

---

# 19. SALES ASSIGNMENT

Sales assignment must be explicit.

When assigning a lead:

```text
validate sales agent
validate tenant
update assignment
create required history/activity
```

Do not allow a tenant's lead to be assigned to another tenant's sales agent.

---

# 20. APPOINTMENTS / SITE VISITS

The appointment represents a scheduled customer interaction/site visit.

Creating an appointment must validate:

```text
tenant
lead
customer
sales agent
property/project if applicable
time
duration
availability
```

Appointment creation must be transactional.

---

# 21. APPOINTMENT CONCURRENCY

Do not implement appointment availability using only:

```text
SELECT
then
INSERT
```

The database contains concurrency protection.

Use the transaction and constraint behavior correctly.

If a conflict occurs:

```text
return 409 Conflict
```

with a safe business error.

---

# 22. CALL MODEL

A call belongs to the appropriate:

```text
tenant
customer
lead
AI session
```

where those relationships exist in the schema.

Calls must be traceable.

A call should allow reconstruction of:

```text
who was contacted
which lead
which tenant
which AI agent/config
when
duration
outcome
transfer
summary
```

---

# 23. CALL MESSAGES

Call messages are potentially high-volume.

Do not load an entire conversation when only the latest messages are required.

Use:

```text
call_id
sequence
created_at
```

or the actual schema's equivalent ordering mechanism.

Use pagination.

---

# 24. CALL EVENTS

Call events should represent meaningful lifecycle/provider events.

Examples may include:

```text
call_started
call_connected
call_ended
transfer_requested
transfer_completed
provider_error
```

Do not create arbitrary event types without documenting them.

---

# 25. AI SESSION

An AI session represents the AI execution associated with a call.

It should be traceable to:

```text
call
agent configuration
tenant
```

where supported by the database.

Do not make AI session state the authoritative CRM state.

---

# 26. AI CONFIGURATION

AI configuration may define:

```text
STT provider
LLM provider
TTS provider
voice
prompt
model configuration
language
tool configuration
```

Historical calls must remain traceable to the configuration/version used during execution.

Do not overwrite historical configuration semantics.

---

# 27. AI USAGE

AI usage should support tracking:

```text
telephony
STT
LLM
TTS
```

and metrics such as:

```text
duration
input units
output units
provider
model
cost
currency
```

Do not hardcode provider pricing into business logic.

Provider pricing changes.

---

# 28. AI TOOL ACCESS

AI tools must NOT have unrestricted database access.

Tools should operate through FastAPI services.

Example:

```text
AI
 ↓
AI Tool
 ↓
Authorization
 ↓
Service
 ↓
Repository
 ↓
PostgreSQL
```

Never:

```text
AI
 ↓
raw SQL
```

---

# 29. AI TOOL TENANT VALIDATION

Every AI tool must know its tenant context.

Example:

```text
LiveKit Call
    ↓
Call ID
    ↓
Lead
    ↓
Tenant
    ↓
AI Tool Context
```

The AI must not be allowed to supply an arbitrary tenant ID.

---

# 30. AI TOOL CUSTOMER VALIDATION

If the AI calls:

```text
update_lead(lead_id)
```

the backend must verify that the lead belongs to the current authorized tenant/call context.

Do not trust the AI's identifier.

---

# 31. AI TOOL MINIMUM DATA

Tools must return only required fields.

For example:

```text
get_lead_context()
```

should return relevant information such as:

```text
name
budget
requirements
property interests
lead stage
recent interaction summary
```

It should not return:

```text
password
tokens
audit logs
integration credentials
unrelated customer records
```

---

# 32. DO-NOT-CONTACT

The database/business rules for:

```text
do_not_contact
```

must be respected by automated calling.

Before initiating an outbound AI call:

```text
customer/lead
        ↓
check do_not_contact
        ↓
if true
    DO NOT CALL
```

Never rely only on the AI prompt to enforce this.

---

# 33. FOLLOW-UPS

Follow-ups represent future sales actions.

A follow-up should contain the appropriate:

```text
lead
customer
assigned agent
scheduled time
status
notes
```

according to the database.

Follow-up creation must respect tenant and authorization boundaries.

---

# 34. CAMPAIGNS

Campaigns may generate outbound communication.

Campaign execution must respect:

```text
tenant
lead status
do_not_contact
campaign membership
rate limits
duplicate prevention
```

Do not send messages merely because a lead exists in a campaign.

---

# 35. WHATSAPP

WhatsApp operations must be isolated behind the WhatsApp integration layer.

Business services should not contain provider-specific HTTP implementation.

Use:

```text
whatsapp service
    ↓
whatsapp provider client
```

Provider-specific details remain inside the integration layer.

---

# 36. SUPAPHONE

Supaphone integration must be isolated.

Use:

```text
integrations/supaphone/
    client.py
    schemas.py
    service.py
```

FastAPI CRM services should not contain raw Supaphone HTTP requests.

---

# 37. LIVEKIT

LiveKit integration must be isolated.

FastAPI should provide the backend APIs/tools required by the LiveKit agent.

Do not mix LiveKit transport logic with CRM domain logic.

---

# 38. WEBHOOKS

Webhook processing must be:

```text
verify
→ validate
→ identify provider event
→ idempotency check
→ persist
→ process
```

Webhook payloads are untrusted.

Never trust tenant/user identifiers contained in an unverified payload.

---

# 39. AUDIT AND ACTIVITIES

Important state changes should create the appropriate audit/activity records.

Examples:

```text
lead assigned
lead qualified
lead status changed
property reserved
property released
appointment created
appointment cancelled
AI transfer
do-not-contact enabled
```

Do not duplicate audit records unnecessarily.

Follow the existing schema.

---

# 40. DATABASE TRANSACTIONS

Use transactions for workflows involving multiple state changes.

A transaction should either:

```text
all changes succeed
```

or:

```text
all changes rollback
```

Never return successful API response after only part of a critical workflow completed.

---

# 41. SOFT DELETE

Follow the database's existing deletion strategy.

Do not invent new soft-delete columns.

Before deleting important CRM data ask:

```text
Is this historical?
Is it referenced?
Is it auditable?
Should it remain?
```

Follow existing constraints.

---

# 42. API DOES NOT MIRROR EVERY TABLE

Do not automatically create CRUD endpoints for every table.

The database is an implementation detail.

Expose business concepts.

Good:

```text
POST /api/v1/properties/{id}/reserve
```

Not:

```text
PATCH /api/v1/property_status_history/{id}
```

---

# 43. PUBLIC PROPERTY DATA

If public website APIs are introduced, expose only explicitly approved public fields.

Never expose internal:

```text
customer
lead
sales
call
AI
audit
negotiated pricing
```

information.

---

# 44. ADMIN PANEL APIs

The Next.js admin panel communicates through FastAPI.

Do not expose the Supabase service-role key to Next.js.

The browser should never directly receive privileged database credentials.

---

# 45. RESPONSE DATA

Never return entire database rows blindly.

Create response schemas.

Explicitly select/serialize fields.

This prevents accidental leakage when new database columns are added later.

---

# 46. DATABASE ERROR HANDLING

Database errors must be translated into safe application errors.

Examples:

```text
unique violation
→ 409 Conflict

foreign key violation
→ 400/409 depending on business semantics

exclusion constraint violation
→ 409 Conflict
```

Do not expose raw PostgreSQL error messages to clients.

---

# 47. PAGINATION

Every potentially large list must be paginated.

Examples:

```text
leads
customers
properties
calls
call_messages
activities
audit_logs
appointments
```

Never return unlimited records.

---

# 48. FILTERING

Use validated filter schemas.

Examples:

```text
LeadFilter
CustomerFilter
PropertyFilter
CallFilter
AppointmentFilter
```

Do not concatenate SQL based on user-supplied filter strings.

---

# 49. SORTING

Only allow whitelisted sort fields.

Never allow arbitrary SQL expressions in:

```text
sort_by
order_by
```

---

# 50. SEARCH

Property search must support business requirements such as:

```text
project
property type
budget
area
bedrooms
amenities
status
location
```

Use the database's indexes and query capabilities.

Do not fetch thousands of records into Python and filter them there.

---

# 51. PERFORMANCE

For voice-agent operations, latency matters.

Avoid unnecessary round trips.

Prefer:

```text
one optimized database query
```

over:

```text
five sequential queries
```

when the same result can safely be retrieved in one operation.

However:

> Never sacrifice tenant isolation or authorization for latency.

---

# 52. REDIS

Redis is not the source of truth.

Use Redis for:

```text
temporary AI state
short-lived locks
rate limiting
caching
coordination
```

Do not permanently store CRM state only in Redis.

---

# 53. ERROR RESPONSE CONTRACT

Use consistent errors.

Example:

```json
{
  "error": {
    "code": "PROPERTY_ALREADY_RESERVED",
    "message": "This property is no longer available."
  }
}
```

Never expose:

```text
SQL
stack trace
database host
credentials
internal file paths
```

---

# 54. DATABASE SCHEMA CHANGES

If FastAPI development reveals that the database genuinely requires a change:

DO NOT silently modify the schema.

First document:

```text
problem
why existing schema is insufficient
proposed change
migration impact
security impact
```

Then create a versioned migration after approval.

---

# 55. DO NOT CREATE DUPLICATE SOURCES OF TRUTH

Do not create application tables/cache structures that duplicate authoritative data unless there is a clear reason.

Examples:

Do not maintain:

```text
property_status in Redis
```

as a second authoritative state.

PostgreSQL remains authoritative.

---

# 56. FINAL DATABASE CONTRACT

The FastAPI application must respect:

```text
PostgreSQL
    ↓
constraints
    ↓
RLS
    ↓
tenant isolation
    ↓
historical integrity
```

FastAPI adds:

```text
authentication
authorization
business logic
validation
orchestration
external integrations
AI tools
```

Neither layer should attempt to replace the other's responsibilities unnecessarily.

---

# 57. WHEN SCHEMA INFORMATION IS MISSING

If the actual migration files do not establish something clearly:

DO NOT invent the schema.

Instead:

1. Inspect more migration files.
2. Inspect related tables.
3. Inspect constraints.
4. Search the repository.
5. Identify the actual implementation.
6. If still ambiguous, report the ambiguity.

Never silently invent a foreign key, column, table, or enum.

---

# 58. WHEN DOCUMENTATION CONFLICTS WITH SQL

The actual deployed/authoritative database implementation takes precedence.

If documentation says:

```text
property.status
```

but SQL shows:

```text
availability_status
```

use the actual SQL schema.

Report the discrepancy.

Do not silently change the database because documentation differs.

---

# 59. CODE GENERATION RULE

When generating code from this database contract:

```text
inspect
→ understand
→ implement
→ test
```

Do not:

```text
guess
→ generate
→ hope
```

---

# 60. FINAL RULE

The database is already a carefully designed production system.

Your responsibility is to build a **safe, clean, maintainable FastAPI application around it**.

Do not redesign the database while implementing APIs.

Do not weaken security constraints.

Do not bypass tenant isolation.

Do not expose service credentials.

Do not give the AI arbitrary database access.

Do not duplicate the source of truth.

Build the application layer around the existing database contract.
