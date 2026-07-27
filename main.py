@app.route("/quota")
def check_quota():
    if not OPENROUTER_API_KEY:
        return "API key not set", 400
    try:
        resp = requests.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "limit": data.get("limit"),
                "remaining": data.get("limit_remaining"),
                "daily_usage": data.get("usage_daily")
            }
        else:
            return {"error": resp.text}, resp.status_code
    except Exception as e:
        return {"error": str(e)}, 500
