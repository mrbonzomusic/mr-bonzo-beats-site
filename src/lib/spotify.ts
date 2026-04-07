const client_id = import.meta.env.SPOTIFY_CLIENT_ID;
const client_secret = import.meta.env.SPOTIFY_CLIENT_SECRET;
const artist_id = import.meta.env.SPOTIFY_ARTIST_ID;

async function getAccessToken() {
  const response = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: {
      'Authorization': 'Basic ' + btoa(client_id + ':' + client_secret),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: 'grant_type=client_credentials',
  });
  const data = await response.json();
  return data.access_token;
}

export async function getArtistAlbums() {
  try {
    const access_token = await getAccessToken();
    if (!access_token) return [];

    const response = await fetch(
      `https://api.spotify.com/v1/artists/${artist_id}/albums?include_groups=album,single&limit=20&market=GR`,
      {
        headers: { 'Authorization': `Bearer ${access_token}` },
      }
    );
    const data = await response.json();
    
    if (!data.items) return [];

    const seen = new Set();
    return data.items.filter((item: any) => {
      const isDuplicate = seen.has(item.name);
      seen.add(item.name);
      return !isDuplicate;
    });
  } catch (error) {
    console.error('Spotify Fetch Error:', error);
    return [];
  }
}