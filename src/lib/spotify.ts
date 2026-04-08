export async function getArtistAlbums({ clientId, clientSecret, artistId, fallbackCover }) {
  try {
    if (!clientId || !clientSecret || !artistId) {
      throw new Error("Missing Spotify Credentials");
    }

    // 1. Λήψη Token
    const authResponse = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Authorization: `Basic ${Buffer.from(`${clientId}:${clientSecret}`).toString("base64")}`,
      },
      body: "grant_type=client_credentials",
    });

    if (!authResponse.ok) {
      const errorText = await authResponse.text();
      throw new Error(`Auth Failed: ${authResponse.status} - ${errorText}`);
    }

    const authData = await authResponse.json();
    const access_token = authData.access_token;

    // 2. Λήψη Albums/Singles
    const artistResponse = await fetch(
      `https://api.spotify.com/v1/artists/${artistId}/albums?include_groups=album,single&limit=20&market=GR`,
      {
        headers: { Authorization: `Bearer ${access_token}` },
      }
    );

    if (!artistResponse.ok) {
      const errorText = await artistResponse.text();
      throw new Error(`Artist Fetch Failed: ${artistResponse.status} - ${errorText}`);
    }

    const data = await artistResponse.json();

    return {
      status: "live",
      releases: data.items.map((item) => ({
        title: item.name,
        year: item.release_date.split("-"),
        cover: item.images && item.images.length > 0 ? item.images.url : fallbackCover,
        spotify: item.external_urls.spotify,
      })),
    };
  } catch (error) {
    console.error("--- SPOTIFY API CRITICAL ERROR ---");
    console.error(error.message);
    return {
      status: "error",
      releases: [],
    };
  }
}