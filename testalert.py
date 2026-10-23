import os
import requests

# GitHub Actions Secret ఉంటే దాని నుండి తీసుకుంటుంది, లేదంటే కింద ఉన్న టోకెన్ వాడుతుంది
WHATSAPP_TOKEN = os.getenv(
    "WHATSAPP_TOKEN",
    "EAGkUCKxbFXUBS9nhq7ZBQLsupgjDHZBFDRimY1XZCa6pQfrw3wL5JzzWwaErI3iF2C1OxtRaxWYcZB7R1h5qC13tbuZABCaTyCmeXOZBtJvxkNcHXXZCBdkKftqeZAKVsPIxAPXuC6eOPvfJLhCBMF4UYGgY1a6wwbRV8WZAp5S8W2PHaEmjnuAeYiZBbn6qk0We8ab3wdL838jbCQn5hzitQbPdAPbQxZBPWtfCk8kodgUZAIfSIo86ZBZCZCzugvXvVIj5k3F6HoptTt7p4YpM2YVjYSQeu0I"
)
PHONE_NUMBER_ID = "1311829505357152"
RECIPIENT_PHONE = "919949934574"

def fetch_and_send_soil_report(lat=14.4426, lon=79.9865):
    """శాటిలైట్ API నుంచి లైవ్ నేల తేమ వివరాలు తీసుకుని వాట్సాప్‌కి పంపడం"""
    print(f"🛰️ Fetching live satellite soil data for ({lat}, {lon})...")
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=soil_moisture_0_to_1cm,soil_temperature_0cm&timezone=auto"
    res = requests.get(url, timeout=10).json()

    moisture = res["hourly"]["soil_moisture_0_to_1cm"][-1]
    temp = res["hourly"]["soil_temperature_0cm"][-1]
    moisture_pct = round(moisture * 100, 1)

    if moisture_pct < 20:
        advice = "⚠️ నేలలో తేమ చాలా తక్కువగా ఉంది. తక్షణమే నీటి తడులు అందించండి."
    elif 20 <= moisture_pct <= 40:
        advice = "✅ నేలలో తేమ సమృద్ధిగా ఉంది. పంట స్థితి ఆశాజనకంగా ఉంది."
    else:
        advice = "💧 నేలలో తేమ అధికంగా ఉంది. నీరు నిలవకుండా డ్రైనేజీ చూడండి."

    message = (
        f"🌾 *మన పొలం - లైవ్ శాటిలైట్ రిపోర్ట్* 🌾\n\n"
        f"📍 *పొలం కోఆర్డినేట్స్:* {lat}, {lon}\n"
        f"💧 *నేల తేమ (Soil Moisture):* {moisture_pct}%\n"
        f"🌡️ *నేల ఉష్ణోగ్రత:* {temp}°C\n"
        f"📋 *వ్యవసాయ సలహా:* {advice}\n\n"
        f"⚡ _Mana Polam Automated Agritech Engine_"
    )

    send_url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": RECIPIENT_PHONE,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(send_url, headers=headers, json=payload)
    print("WhatsApp Response:", response.status_code, response.json())

if __name__ == "__main__":
    fetch_and_send_soil_report()