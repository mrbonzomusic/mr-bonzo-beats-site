interface SpotifyAlbumsParams {
  clientId?: string;
  clientSecret?: string;
  artistId?: string;
  fallbackCover: string;
}

interface SpotifyRelease {
  title: string;
  year: string;
  cover: string;
  spotify: string;
}

interface SpotifyResult {
  status: "live" | "error";
  releases: SpotifyRelease[];
}

export async function getArtistAlbums({
  clientId,
  clientSecret,
  artistId,
  fallbackCover,
}: SpotifyAlbumsParams): Promise<SpotifyResult> {
  try {
    if (!clientId || !clientSecret || !artistId) {
      throw new Error("Missing Spotify Credentials");
    }

    // 1. Λήψη Token
    const authResponse = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Authorization: `Basic ${btoa(`${clientId}:${clientSecret}`)}`,
      },
      body: "grant_type=client_credentials",
    });

    if (!authResponse.ok) {
      const errorText = await authResponse.text();
      throw new Error(`Auth Failed: ${authResponse.status} - ${errorText}`);
    }

    const authData = await authResponse.json();
    const accessToken = authData.access_token;

    // 2. Λήψη Albums/Singles
    const artistResponse = await fetch(
      `https://api.spotify.com/v1/artists/${artistId}/albums?include_groups=album,single&limit=20&market=GR`,
      {
        headers: { Authorization: `Bearer ${accessToken}` },
      }
    );

    if (!artistResponse.ok) {
      const errorText = await artistResponse.text();
      throw new Error(`Artist Fetch Failed: ${artistResponse.status} - ${errorText}`);
    }

    const data = await artistResponse.json();
    const items = Array.isArray(data?.items) ? data.items : [];
    const seen = new Set();
    const releases: Array<SpotifyRelease & { releaseDate: string }> = [];

    for (const item of items) {
      const spotifyUrl = item?.external_urls?.spotify;
      if (!spotifyUrl || seen.has(spotifyUrl)) continue;
      seen.add(spotifyUrl);
      releases.push({
        title: item?.name || "Untitled",
        year: item?.release_date ? String(item.release_date).split("-")[0] : "",
        cover: item?.images?.[0]?.url || fallbackCover,
        spotify: spotifyUrl,
        releaseDate: item?.release_date || "",
      });
    }

    releases.sort((a, b) => String(b.releaseDate).localeCompare(String(a.releaseDate)));

    return {
      status: "live",
      releases: releases.slice(0, 6).map(({ releaseDate, ...release }) => release),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("--- SPOTIFY API CRITICAL ERROR ---");
    console.error(message);
    return {
      status: "error",
      releases: [],
    };
  }
}

export function extractSpotifyArtistId(spotifyUrl = "") {
  try {
    const match = String(spotifyUrl).match(/spotify\.com\/artist\/([a-zA-Z0-9]+)/);
    return match?.[1] || "";
  } catch {
    return "";
  }
}