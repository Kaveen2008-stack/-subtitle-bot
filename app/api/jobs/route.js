import { createClient } from '@supabase/supabase-js';

// Dashboard eka admin-only nisa, service_role (secret) key eka use karanawa -
// eeka RLS bypass karanawa, jobs/job_events tables walata full access denawa.
// NEXT_PUBLIC_ prefix eka NAHA meke - browser ekata visible wenna epa.
const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

export async function GET(request) {
  try {
    // Simple auth: admin password header check (dan thiyena WEBHOOK_SECRET
    // pattern eka wagema, eth wenma secret ekak - dashboard ekata witharak)
    const providedPassword = request.headers.get('x-admin-password');
    if (providedPassword !== process.env.ADMIN_DASHBOARD_PASSWORD) {
      return Response.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { searchParams } = new URL(request.url);
    const jobId = searchParams.get('job_id');

    // If a specific job_id is requested, return that job + its full event
    // timeline. Otherwise return the list of recent jobs (summary only).
    if (jobId) {
      const { data: job, error: jobError } = await supabase
        .from('jobs')
        .select('*')
        .eq('id', jobId)
        .single();

      if (jobError) {
        return Response.json({ error: jobError.message }, { status: 404 });
      }

      const { data: events, error: eventsError } = await supabase
        .from('job_events')
        .select('*')
        .eq('job_id', jobId)
        .order('created_at', { ascending: true });

      if (eventsError) {
        return Response.json({ error: eventsError.message }, { status: 500 });
      }

      return Response.json({ job, events });
    }

    // List view: most recent 100 jobs, newest first
    const { data: jobs, error } = await supabase
      .from('jobs')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(100);

    if (error) {
      return Response.json({ error: error.message }, { status: 500 });
    }

    return Response.json({ jobs });
  } catch (err) {
    console.error('GET /api/jobs failed:', err);
    return Response.json({ error: 'Internal server error' }, { status: 500 });
  }
}
