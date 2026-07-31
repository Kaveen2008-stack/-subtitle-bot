import requests
import config

def search_drama(query):
    url = f"https://api.themoviedb.org/3/search/tv?api_key={config.TMDB_API_KEY}&query={query}"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        results = res.json().get("results", [])[:5]
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "year": (r.get("first_air_date") or "N/A")[:4],
            }
            for r in results
        ]
    except Exception:
        return []
