import os
import json
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from flask import Flask, request, jsonify
import requests

from send_whatsapp import ACCESS_TOKEN, PHONE_NUMBER_ID
from agri_intelligence import (
    get_live_weather,
    check_instant_weather_and_lightning,
    detect_crop_age_and_soil,
    analyze_directional_damage_and_map,
    diagnose_problem_and_dosage,
    generate_single_crisp_telugu_audio,
    get_nearby_shops_and_urea_stock,
    generate_product_pack_image,
    get_pre_harvest_market_and_dealers,
    get_post_harvest_soil_recovery,
    get_monthly_crop_recommendations,
)

app = Flask(__name__)

VERIFY_TOKEN = "mana_polam_secret_2026"
GRAPH_URL = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}"
HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
DB_FILE = "farmers_db.json"


def load_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_db(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_text(to: str, body: str):
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
    requests.post(f"{GRAPH_URL}/messages", headers=HEADERS, json=payload, timeout=15)


def send_buttons(to: str, body_text: str, buttons: list):
    btn_objs = [{"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}} for b in buttons[:3]]
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {"type": "button", "body": {"text": body_text}, "action": {"buttons": btn_objs}},
    }
    requests.post(f"{GRAPH_URL}/messages", headers=HEADERS, json=payload, timeout=15)


def upload_media(file_path: str, mime_type: str) -> str:
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime_type)}
        data = {"messaging_product": "whatsapp"}
        res = requests.post(f"{GRAPH_URL}/media", headers=HEADERS, files=files, data=data, timeout=60).json()
    return res.get("id", "")


def send_image(to: str, image_path: str, caption: str = ""):
    media_id = upload_media(image_path, "image/png")
    if media_id:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"id": media_id, "caption": caption}}
        requests.post(f"{GRAPH_URL}/messages", headers=HEADERS, json=payload, timeout=20)


def send_audio(to: str, audio_path: str):
    media_id = upload_media(audio_path, "audio/mpeg")
    if media_id:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "audio", "audio": {"id": media_id}}
        requests.post(f"{GRAPH_URL}/messages", headers=HEADERS, json=payload, timeout=25)


def parse_polygons_from_kml(kml_bytes: bytes) -> list:
    root = ET.fromstring(kml_bytes)
    polygons = []
    for elem in root.iter():
        if elem.tag.endswith("coordinates") and elem.text:
            pts = []
            for token in elem.text.strip().split():
                parts = token.split(",")
                if len(parts) >= 2:
                    pts.append([float(parts[0]), float(parts[1])])
            if len(pts) >= 3:
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                polygons.append(pts)
    return polygons


def pin_to_square_polygon(lat: float, lon: float, delta: float = 0.0012) -> list:
    return [
        [lon - delta, lat - delta],
        [lon + delta, lat - delta],
        [lon + delta, lat + delta],
        [lon - delta, lat + delta],
        [lon - delta, lat - delta],
    ]


def analyze_and_send_all_farms(phone: str, send_voice: bool = True, force_harvest_preview: bool = False):
    db = load_db()
    user = db.get(phone, {})
    farms = user.get("farms", [])

    if not farms:
        send_text(
            phone,
            "🌾 *Mana Polam కు స్వాగతం!*\n"
            "జీవితంలో ఒక్కసారి మాత్రమే మీ 1 నుండి 4 పొలాల Location Pin లేదా KML ఫైల్ పంపండి.",
        )
        return

    farms_summary = []
    first_weather = None

    for idx, farm in enumerate(farms, start=1):
        fname = farm.get("name", f"Polam_{idx}")
        coords = farm["coords"]
        prev_ndvi = farm.get("last_ndvi", 0.0)

        center_lon = sum(p[0] for p in coords[:-1]) / max(1, len(coords) - 1)
        center_lat = sum(p[1] for p in coords[:-1]) / max(1, len(coords) - 1)

        weather = get_live_weather(center_lat, center_lon)
        if first_weather is None:
            first_weather = weather

        map_info = analyze_directional_damage_and_map(coords, fname)
        crop_info = detect_crop_age_and_soil(
            map_info["mean_ndvi"], map_info["mean_ndwi"], map_info.get("mean_bsi", 0.05), map_info.get("past_ndvi", 0.20)
        )
        post_harvest = get_post_harvest_soil_recovery(map_info["mean_ndvi"], map_info["mean_ndwi"], prev_ndvi)
        diag = diagnose_problem_and_dosage(map_info["mean_ndvi"], map_info["damaged_acres"], weather, crop_info)

        farm["last_ndvi"] = map_info["mean_ndvi"]
        worst_te = map_info["worst_dir"].split("(")[0].strip()

        if post_harvest.get("is_post_harvest"):
            caption = (
                f"📍 *{fname} ({map_info['total_acres']} ఎకరాలు) — కోత పూర్తయింది 🌾*\n"
                f"🌱 *మట్టి స్థితి:* {post_harvest['soil_condition']}\n"
                f"🚜 *తదుపరి పంట బాగుండాలంటే:*\n{post_harvest['next_crop_action']}\n"
                f"🌧️ *వర్షం:* {weather['rain_te']}"
            )
            send_image(phone, map_info["map_path"], caption)

        elif crop_info["is_beedu"]:
            caption = (
                f"📍 *{fname} ({map_info['total_acres']} ఎకరాలు)*\n"
                f"🌾 *స్థితి:* ప్రస్తుతం మీ పొలంలో *ఏ పంట లేదు (ఖాళీగా ఉంది)*.\n"
                f"🌱 *మట్టి:* {crop_info['soil_te']}\n"
                f"🌧️ *వర్షం:* {weather['rain_te']}"
            )
            send_image(phone, map_info["map_path"], caption)
            send_text(phone, get_monthly_crop_recommendations(map_info["mean_ndwi"]))

        elif diag["is_healthy"]:
            caption = (
                f"📍 *{fname} ({map_info['total_acres']} ఎకరాలు)*\n"
                f"🌾 *{crop_info['crop_te']}* ({crop_info['stage_te']})\n"
                f"🌱 {crop_info['soil_te']} | ✅ పంట ఆరోగ్యంగా ఉంది 🌿\n"
                f"🌧️ *వర్షం:* {weather['rain_te']}"
            )
            send_image(phone, map_info["map_path"], caption)

        else:
            caption = (
                f"📍 *{fname} ({map_info['total_acres']} ఎకరాలు)*\n"
                f"🌾 *{crop_info['crop_te']}* ({crop_info['stage_te']})\n"
                f"⚠️ *{worst_te} ({map_info['damaged_acres']} ఎకరాలు):* {diag['problem_te']}\n"
                f"🌱 {crop_info['soil_te']}\n"
                f"🌧️ *వర్షం:* {weather['rain_te']}"
            )
            send_image(phone, map_info["map_path"], caption)

            pack_img = generate_product_pack_image(diag)
            pack_caption = (
                f"📦 *అసలు మందు ప్యాకెట్ ({fname})*\n"
                f"• *ఎందుకు:* {diag['use_case_te']}\n"
                f"• *ఎప్పుడు:* {diag['when_to_use_te']}\n"
                f"• 🏛️ *RBK ధర:* ₹{diag['rbk_price_inr']} (షాపు: ₹{diag['market_price_inr']})"
            )
            send_image(phone, pack_img, pack_caption)

        if (not crop_info["is_beedu"] and crop_info["days_old"] >= 80) or force_harvest_preview:
            crop_label = crop_info["crop_te"] if not crop_info["is_beedu"] else "వరి (Paddy)"
            market_msg = get_pre_harvest_market_and_dealers(crop_label, map_info["total_acres"])
            send_text(phone, market_msg)

        farms_summary.append({
            "name": fname,
            "total_acres": map_info["total_acres"],
            "damaged_acres": map_info["damaged_acres"],
            "is_beedu": crop_info["is_beedu"],
            "is_post_harvest": post_harvest.get("is_post_harvest", False),
            "is_healthy": diag["is_healthy"],
            "crop_te": crop_info["crop_te"],
            "days_old": crop_info["days_old"],
            "soil_te": post_harvest.get("soil_condition", crop_info["soil_te"]),
            "worst_dir": map_info["worst_dir"],
            "problem_te": diag["problem_te"],
            "use_case_te": diag.get("use_case_te", ""),
            "rbk_price": diag.get("rbk_price_inr", 0),
        })

    save_db(db)

    if send_voice:
        voice_path = generate_single_crisp_telugu_audio(farms_summary, first_weather)
        send_audio(phone, voice_path)

    send_buttons(
        phone,
        "👇 అవసరమైతే కింది బటన్లు నొక్కండి:",
        [
            {"id": "BTN_VOICE_REPLY", "title": "🎧 తెలుగు వాయిస్"},
            {"id": "BTN_MARKET_DEALERS", "title": "🌍 మార్కెట్ & డీలర్లు"},
            {"id": "BTN_UREA_SHOPS", "title": "🏪 యూరియా షాపులు"},
        ],
    )


def daily_scheduler_loop():
    last_run_slot = ""
    last_weather_check = 0
    alert_cooldown = {}

    while True:
        now = datetime.now()
        hm = now.strftime("%H:%M")
        slot_key = f"{now.strftime('%Y-%m-%d')}_{hm}"

        if hm in ("07:00", "18:00") and slot_key != last_run_slot:
            last_run_slot = slot_key
            db = load_db()
            for phone in list(db.keys()):
                try:
                    analyze_and_send_all_farms(phone, send_voice=True)
                except Exception as e:
                    print(f"[Daily Scheduler Error]: {e}")

        if time.time() - last_weather_check >= 900:
            last_weather_check = time.time()
            db = load_db()
            for phone, udata in list(db.items()):
                farms = udata.get("farms", [])
                if not farms:
                    continue
                coords = farms[0]["coords"]
                lon = sum(p[0] for p in coords[:-1]) / max(1, len(coords) - 1)
                lat = sum(p[1] for p in coords[:-1]) / max(1, len(coords) - 1)
                try:
                    alert = check_instant_weather_and_lightning(lat, lon)
                    atype = alert.get("alert_type")
                    if atype:
                        cd_key = f"{phone}_{atype}"
                        if time.time() - alert_cooldown.get(cd_key, 0) >= 10800:
                            alert_cooldown[cd_key] = time.time()
                            send_text(phone, alert["msg"])
                except Exception as e:
                    print(f"[Lightning Watcher Error]: {e}")

        time.sleep(25)


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                val = change.get("value", {})
                for msg in val.get("messages", []):
                    phone = msg["from"]
                    mtype = msg["type"]
                    db = load_db()
                    user = db.setdefault(phone, {"farms": []})

                    if mtype == "audio":
                        analyze_and_send_all_farms(phone, send_voice=True)

                    elif mtype == "location":
                        loc = msg["location"]
                        lat, lon = float(loc["latitude"]), float(loc["longitude"])
                        if len(user["farms"]) >= 4:
                            user["farms"] = []
                        user["farms"].append({"name": f"Polam_{len(user['farms']) + 1}", "coords": pin_to_square_polygon(lat, lon)})
                        save_db(db)
                        analyze_and_send_all_farms(phone, send_voice=True)

                    elif mtype == "document":
                        doc = msg["document"]
                        fname = doc.get("filename", "").lower()
                        if fname.endswith(".kml"):
                            media_info = requests.get(f"https://graph.facebook.com/v25.0/{doc['id']}", headers=HEADERS, timeout=15).json()
                            file_bytes = requests.get(media_info["url"], headers=HEADERS, timeout=30).content
                            polys = parse_polygons_from_kml(file_bytes)
                            if polys:
                                for p in polys:
                                    if len(user["farms"]) >= 4:
                                        break
                                    user["farms"].append({"name": f"Polam_{len(user['farms']) + 1}", "coords": p})
                                save_db(db)
                                analyze_and_send_all_farms(phone, send_voice=True)

                    elif mtype == "interactive":
                        btn_id = msg["interactive"]["button_reply"]["id"]
                        if btn_id == "BTN_VOICE_REPLY":
                            analyze_and_send_all_farms(phone, send_voice=True)
                        elif btn_id == "BTN_MARKET_DEALERS":
                            send_text(phone, get_pre_harvest_market_and_dealers("వరి (Paddy)", 8.6))
                        elif btn_id == "BTN_UREA_SHOPS":
                            send_text(phone, get_nearby_shops_and_urea_stock(14.5395, 79.8792))

                    elif mtype == "text":
                        txt = msg["text"]["body"].strip().lower()
                        if txt in ("market", "price", "dealers", "ధరలు"):
                            send_text(phone, get_pre_harvest_market_and_dealers("వరి (Paddy)", 8.6))
                        elif txt in ("urea", "stock", "shops", "యూరియా"):
                            send_text(phone, get_nearby_shops_and_urea_stock(14.5395, 79.8792))
                        elif txt in ("voice", "audio", "వాయిస్"):
                            analyze_and_send_all_farms(phone, send_voice=True)
                        elif txt in ("clear", "reset", "మార్చు"):
                            user["farms"] = []
                            save_db(db)
                            send_text(phone, "🔄 పాత పొలాలు క్లియర్ అయ్యాయి. కొత్త Location Pin లేదా KML పంపండి.")
                        else:
                            analyze_and_send_all_farms(phone, send_voice=True)
    except Exception as e:
        print(f"[Webhook Error]: {e}")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    threading.Thread(target=daily_scheduler_loop, daemon=True).start()
    print("🚀 Mana Polam AI Server Live on Port 5000...")
    app.run(host="0.0.0.0", port=5000)
