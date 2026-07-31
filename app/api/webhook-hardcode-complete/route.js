import { createClient } from '@supabase/supabase-js';

// Use service-role-like access here via anon key + permissive RLS,
// OR ideally a service role key stored server-side only.
const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
);

export async function POST(request) {
  try {
    const secret = request.headers.get('x-webhook-secret');
    if (secret !== process.env.HARDCODE_WEBHOOK_SECRET) {
      return Response.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json();
    const {
      tmdb_id,
      season_number,
      episode_number,
      pixeldrain_download_url,
      voe_embed_url,
      quality,
    } = body;

    if (!tmdb_id || !season_number || !episode_number) {
      return Response.json({ error: 'Missing required fields' }, { status: 400 });
    }

    // Upsert episode row
    const { data: episode, error: epError } = await supabase
      .from('episodes')
      .upsert(
        {
          tmdb_id: parseInt(tmdb_id),
          season_number: parseInt(season_number),
          episode_number: parseInt(episode_number),
          stream_url: voe_embed_url || '',
          download_url: pixeldrain_download_url || null,
          subtitle_url: null,
          quality: quality || '1080p',
        },
        { onConflict: 'tmdb_id,season_number,episode_number,quality' }
      )
      .select()
      .single();

    if (epError) {
      console.error('Episode upsert error:', epError);
      return Response.json({ error: epError.message }, { status: 500 });
    }

    // Add/update VOE server entry
    if (voe_embed_url) {
      await supabase.from('episode_servers').insert({
        episode_id: episode.id,
        server_name: 'VOE.sx',
        stream_url: voe_embed_url,
        server_type: 'embed',
        priority: 1,
      });
    }

    return Response.json({ success: true, episode_id: episode.id });
  } catch (err) {
    console.error('Webhook error:', err);
    return Response.json({ error: err.message }, { status: 500 });
  }
}
