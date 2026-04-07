export interface SpotifyRelease {
  title: string;
  year: string;
  cover: string;
  spotify: string;
}

export type SpotifyFetchStatus = "live" | "fallback" | "error";

export interface SpotifyReleasesResult {
  releases: SpotifyRelease[];
  status: SpotifyFetchStatus;
  message?: string;
}

interface SpotifyAlbumItem {
  name?: string;
  release_date?: string;
  images?: Array<{ url?: string }>;
  external_urls?: { spotify?: string };
}

interface SpotifyAlbumsResponse {
  items?: SpotifyAlbumItem[];
}

export async function fetchArtistReleases(params: {
  clientId?: string;
  clientSecret?: string;
  artistId?: string;
  fallbackCover?: string;
}): Promise<SpotifyReleasesResult> {
  const { clientId, clientSecret, artistId, fallbackCover = "/images/Mr.%20Bonzo%20half%20face.jpg" } = params;
  if (!clientId || !clientSecret || !artistId) {
    return { releases: [], status: "fallback", message: "Missing Spotify environment variables." };
  }

  try {
    const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
    const tokenResponse = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST",
      headers: {
        Authorization: `Basic ${credentials}`,
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: new URLSearchParams({ grant_type: "client_credentials" })
    });

    if (!tokenResponse.ok) {
      return { releases: [], status: "error", message: "Spotify token request failed." };
    }
    const tokenData = await tokenResponse.json();
    const accessToken = tokenData?.access_token as string | undefined;
    if (!accessToken) {
      return { releases: [], status: "error", message: "Spotify token was not returned." };
    }

    const releasesResponse = await fetch(
      `https://api.spotify.com/v1/artists/${artistId}/albums?include_groups=album,single&market=US&limit=50`,
      { headers: { Authorization: `Bearer ${accessToken}` } }
    );
    if (!releasesResponse.ok) {
      return { releases: [], status: "error", message: "Spotify releases request failed." };
    }

    const releasesData = (await releasesResponse.json()) as SpotifyAlbumsResponse;
    const seen = new Set<string>();

    const releases = (releasesData.items ?? [])
      .filter((item) => {
        const key = String(item.name || "").toLowerCase().trim();
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map((item) => ({
        title: item.name || "Untitled Release",
        year: String(item.release_date || "").slice(0, 4) || "----",
        cover: item.images?.[0]?.url || fallbackCover,
        spotify: item.external_urls?.spotify || "https://open.spotify.com/"
      }))
      .sort((a, b) => Number(b.year) - Number(a.year));

    if (!releases.length) {
      return { releases: [], status: "fallback", message: "Spotify returned no releases." };
    }

    return { releases, status: "live" };
  } catch {
    return { releases: [], status: "error", message: "Spotify request threw an exception." };
  }
}
