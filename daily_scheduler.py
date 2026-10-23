import time
import schedule
import requests

# Meta Credentials
WHATSAPP_TOKEN = "EAGkUCKxbFXUBS9nhq7ZBQLsupgjDHZBFDRimY1XZCa6pQfrw3wL5JzzWwaErI3iF2C1OxtRaxWYcZB7R1h5qC13tbuZABCaTyCmeXOZBtJvxkNcHXXZCBdkKftqeZAKVsPIxAPXuC6eOPvfJLhCBMF4UYGgY1a6wwbRV8WZAp5S8W2PHaEmjnuAeYiZBbn6qk0We8ab3wdL838jbCQn5hzitQbPdAPbQxZBPWtfCk8kodgUZAIfSIo86ZBZCZCzugvXvVIj5k3F6HoptTt7p4YpM2YVjYSQeu0I"
PHONE_NUMBER_ID = "1311829505357152"
RECIPIENT_PHONE = "919949934574"  # Mee verified WhatsApp number

# Mee polam coordinates (Buchireddypalem area)
LAT = 14.5321
LON = 79.8745

def send_daily_soil_update():
    print(f"🛰️ Fetching daily satellite soil data for ({LAT}, {LON})...")
    url = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&hourly=soil_moisture_0_to_1cm,soil_temperature_0cm&timezone=auto"
    
    try:
        res = requests.get(url, timeout=10).json()
        moisture = res["hourly"]["soil_moisture_0_to_1cm"][-1]
        temp = res["hourly"]["soil_temperature_0cm"][-1]
        moisture_pct = round(moisture * 100, 1)

        if moisture_pct < 20:
            advice = "⚠️ Nelalo thema chala thakkuvaga undi. Thaksaname neeti thadulu andhinchandi."
        elif 20 <= moisture_pct <= 40:
            advice = "✅ Nelalo thema samruddhiga undi. Panta sthithi aashajanakamga undi."
        else:
            advice = "💧 Nelalo thema adhika ga undi. Neeru nilavakunda choosukondi."

        message = (
            f"🌾 *Mana Polam - Rojuvari Satellite Report* 🌾\n\n"
            f"📍 *Prantham:* Buchireddypalem ({LAT}, {LON})\n"
            f"💧 *Nela Thema (Soil Moisture):* {moisture_pct}%\n"
            f"🌡️ *Nela Ushnogratha:* {temp}°C\n"
            f"📋 *Vyavasaya Salaha:* {advice}\n\n"
            f"⚡ _Mana Polam Automated Daily Engine_"
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
        print("WhatsApp Daily Send Status:", response.status_code, response.json())
    except Exception as e:
        print("Error fetching/sending update:", e)

# Roju poddunna 07:00 AM ki alert vellela schedule:
schedule.every().day.at("07:00").do(send_daily_soil_update)

# Testing kosam: code run chesina ventane okasari send chesthundi
send_daily_soil_update()

print("⏰ Mana Polam Daily Scheduler active lo undi... Roju poddunna 7:00 AM ki WhatsApp alert velthundi.")

while True:
    schedule.run_pending()
    time.sleep(60)