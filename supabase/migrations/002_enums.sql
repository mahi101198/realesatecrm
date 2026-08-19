-- =============================================================================
-- MIGRATION 002: Enums
-- =============================================================================
-- Centralising all enum types here makes it straightforward to ALTER them
-- in future migrations without hunting across the schema.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ORGANIZATION / USERS
-- ---------------------------------------------------------------------------

create type public.user_status as enum (
  'active',
  'inactive',
  'suspended',
  'invited',
  'pending_verification'
);

create type public.agent_availability as enum (
  'online',
  'offline',
  'busy',
  'on_call'
);

-- ---------------------------------------------------------------------------
-- PROPERTY / PROJECT
-- ---------------------------------------------------------------------------

create type public.project_status as enum (
  'pre_launch',
  'launched',
  'under_construction',
  'ready_to_move',
  'completed',
  'on_hold',
  'cancelled',
  'archived'
);

create type public.property_status as enum (
  'draft',
  'available',
  'reserved',
  'hold',
  'sold',
  'blocked',
  'inactive'
);

create type public.property_facing as enum (
  'north',
  'south',
  'east',
  'west',
  'north_east',
  'north_west',
  'south_east',
  'south_west'
);

create type public.area_unit as enum (
  'sqft',
  'sqyd',
  'sqm',
  'acre',
  'hectare',
  'gaj',
  'biswa'
);

create type public.media_visibility as enum (
  'public',
  'tenant_only',
  'private'
);

create type public.price_type as enum (
  'base_price',
  'offer_price',
  'negotiated_price',
  'booking_amount',
  'token_amount'
);

-- ---------------------------------------------------------------------------
-- CRM / LEAD
-- ---------------------------------------------------------------------------

create type public.lead_status as enum (
  'new',
  'active',
  'on_hold',
  'converted',
  'lost',
  'do_not_contact'
);

create type public.sales_stage as enum (
  'new',
  'contacted',
  'qualified',
  'site_visit',
  'negotiation',
  'booking',
  'closed'
);

create type public.interest_level as enum (
  'very_high',
  'high',
  'medium',
  'low',
  'very_low'
);

create type public.purpose as enum (
  'investment',
  'end_use',
  'rental',
  'resale',
  'commercial_use',
  'other'
);

create type public.finance_requirement as enum (
  'self_funded',
  'bank_loan',
  'home_loan',
  'partial_loan',
  'undecided'
);

-- ---------------------------------------------------------------------------
-- CAMPAIGN
-- ---------------------------------------------------------------------------

create type public.campaign_type as enum (
  'ai_outbound',
  'ai_inbound',
  'manual_call',
  'whatsapp',
  'email',
  'mixed'
);

create type public.campaign_status as enum (
  'draft',
  'scheduled',
  'active',
  'paused',
  'completed',
  'cancelled',
  'archived'
);

create type public.campaign_lead_status as enum (
  'pending',
  'in_queue',
  'calling',
  'called',
  'qualified',
  'not_interested',
  'callback',
  'do_not_call',
  'failed',
  'skipped'
);

-- ---------------------------------------------------------------------------
-- VOICE CALLS
-- ---------------------------------------------------------------------------

create type public.call_direction as enum (
  'outbound',
  'inbound'
);

create type public.call_status as enum (
  'initiated',
  'ringing',
  'answered',
  'in_progress',
  'completed',
  'failed',
  'busy',
  'no_answer',
  'cancelled'
);

create type public.call_outcome as enum (
  'qualified',
  'not_interested',
  'callback_requested',
  'site_visit_requested',
  'site_visit_booked',
  'human_transfer',
  'wrong_number',
  'do_not_call',
  'follow_up_required',
  'voicemail',
  'other'
);

create type public.call_participant_type as enum (
  'customer',
  'ai_agent',
  'sales_agent',
  'system'
);

create type public.message_speaker as enum (
  'customer',
  'agent',
  'system'
);

create type public.message_type as enum (
  'speech',
  'tool_call',
  'tool_result',
  'system_event'
);

create type public.call_event_type as enum (
  'call_started',
  'call_answered',
  'agent_started_speaking',
  'customer_started_speaking',
  'customer_interrupted',
  'agent_interrupted',
  'tool_called',
  'tool_result_received',
  'tool_failed',
  'human_transfer_requested',
  'human_transfer_completed',
  'sentiment_changed',
  'silence_detected',
  'call_ended',
  'recording_started',
  'recording_stopped',
  'error'
);

create type public.agent_session_status as enum (
  'active',
  'completed',
  'failed',
  'abandoned'
);

-- ---------------------------------------------------------------------------
-- SALES / APPOINTMENTS
-- ---------------------------------------------------------------------------

create type public.assignment_type as enum (
  'manual',
  'automatic',
  'ai_transfer',
  'round_robin'
);

create type public.follow_up_type as enum (
  'ai_call',
  'human_call',
  'whatsapp',
  'email',
  'sms',
  'in_person'
);

create type public.follow_up_status as enum (
  'pending',
  'in_progress',
  'completed',
  'cancelled',
  'overdue',
  'skipped'
);

create type public.appointment_type as enum (
  'site_visit',
  'office_meeting',
  'virtual_meeting',
  'call_back'
);

create type public.appointment_status as enum (
  'pending',
  'confirmed',
  'completed',
  'cancelled',
  'rescheduled',
  'no_show'
);

create type public.appointment_source as enum (
  'ai_agent',
  'sales_agent',
  'admin',
  'website',
  'customer_portal'
);

-- ---------------------------------------------------------------------------
-- COMMUNICATION
-- ---------------------------------------------------------------------------

create type public.message_direction as enum (
  'inbound',
  'outbound'
);

create type public.whatsapp_message_status as enum (
  'queued',
  'sent',
  'delivered',
  'read',
  'failed'
);

create type public.whatsapp_template_status as enum (
  'pending',
  'approved',
  'rejected',
  'paused'
);

create type public.communication_channel as enum (
  'voice',
  'whatsapp',
  'sms',
  'email'
);

-- ---------------------------------------------------------------------------
-- SYSTEM
-- ---------------------------------------------------------------------------

create type public.notification_type as enum (
  'hot_lead',
  'human_transfer_requested',
  'site_visit_booked',
  'appointment_reminder',
  'follow_up_due',
  'call_failed',
  'lead_assigned',
  'campaign_started',
  'campaign_completed',
  'general'
);

create type public.webhook_status as enum (
  'received',
  'processing',
  'processed',
  'failed',
  'duplicate'
);

create type public.integration_status as enum (
  'active',
  'inactive',
  'error',
  'pending_setup'
);
