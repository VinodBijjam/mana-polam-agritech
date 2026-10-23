import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# డిఫాల్ట్ పొలం లొకేషన్ వివరాలు
FARM_DATA = {
    "farmer_name": "వినోద్ రెడ్డి (Mana Polam)",
    "location_name": "బుచ్చిరెడ్డిపాలెం పొలం",
    "lat": 14.5321,
    "lon": 79.8745,
    "soil_moisture": 0,
    "soil_temp": 0,
    "status": "Healthy"
}

def fetch_satellite_data(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=soil_moisture_0_to_1cm,soil_temperature_0cm&timezone=auto"
    res = requests.get(url, timeout=10).json()
    moisture = round(res["hourly"]["soil_moisture_0_to_1cm"][-1] * 100, 1)
    temp = res["hourly"]["soil_temperature_0cm"][-1]
    return moisture, temp

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="te">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mana Polam - Live Agritech Dashboard</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f4f8; margin: 0; padding: 20px; }
        .container { max-width: 900px; margin: auto; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.08); }
        h1 { color: #2e7d32; display: flex; align-items: center; gap: 10px; margin-top: 0; }
        .badge { background: #e8f5e9; color: #2e7d32; padding: 4px 12px; border-radius: 20px; font-size: 14px; font-weight: bold; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin: 25px 0; }
        .card { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 10px; padding: 20px; text-align: center; }
        .card h3 { margin: 0; color: #666; font-size: 15px; }
        .card .value { font-size: 32px; font-weight: bold; color: #1b5e20; margin: 10px 0; }
        .form-box { background: #e8f5e9; border: 1px solid #c8e6c9; padding: 20px; border-radius: 10px; margin-top: 20px; }
        .form-box input { padding: 10px; border: 1px solid #ccc; border-radius: 6px; width: 140px; margin-right: 10px; }
        .btn { background: #2e7d32; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn:hover { background: #1b5e20; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌾 Mana Polam <span class="badge">Live Satellite Feed</span></h1>
        <p><strong>రైతు పేరు:</strong> {{ farm.farmer_name }} | <strong>ప్రాంతం:</strong> {{ farm.location_name }} ({{ farm.lat }}, {{ farm.lon }})</p>
        
        <div class="grid">
            <div class="card">
                <h3>💧 నేల తేమ (Soil Moisture)</h3>
                <div class="value">{{ farm.soil_moisture }}%</div>
                <small>ఆప్టిమల్ రేంజ్: 20% - 40%</small>
            </div>
            <div class="card">
                <h3>🌡️ నేల ఉష్ణోగ్రత</h3>
                <div class="value">{{ farm.soil_temp }}°C</div>
                <small>ఉపరితల ఉష్ణోగ్రత</small>
            </div>
            <div class="card">
                <h3>🛰️ శాటిలైట్ కవరేజ్</h3>
                <div class="value" style="color: #0288d1;">Active</div>
                <small>Open-Meteo Sentinel Models</small>
            </div>
        </div>

        <div class="form-box">
            <h3>📍 కోఆర్డినేట్స్ అప్‌డేట్ చేయండి (Update Farm GPS)</h3>
            <form method="POST" action="/update_coordinates">
                <input type="text" name="lat" value="{{ farm.lat }}" placeholder="Latitude" required>
                <input type="text" name="lon" value="{{ farm.lon }}" placeholder="Longitude" required>
                <button type="submit" class="btn">అప్‌డేట్ & శాటిలైట్ రీ-సింక్</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def dashboard():
    moisture, temp = fetch_satellite_data(FARM_DATA["lat"], FARM_DATA["lon"])
    FARM_DATA["soil_moisture"] = moisture
    FARM_DATA["soil_temp"] = temp
    return render_template_string(HTML_TEMPLATE, farm=FARM_DATA)

@app.route("/update_coordinates", methods=["POST"])
def update_coordinates():
    FARM_DATA["lat"] = float(request.form.get("lat"))
    FARM_DATA["lon"] = float(request.form.get("lon"))
    return dashboard()

if __name__ == "__main__":
    app.run(port=5000, debug=True)