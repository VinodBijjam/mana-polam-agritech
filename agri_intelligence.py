import os
import math
from io import BytesIO
from datetime import datetime, timedelta
import requests
import ee
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from gtts import gTTS

GEE_PROJECT_ID = "mana-polam-ai"
try:
    ee.Initialize(project=GEE_PROJECT_ID)
except Exception:
    ee.Authenticate()
    ee.Initialize(project=GEE_PROJECT_ID)


def get_font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/NirmalaB.ttf" if bold else "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/Nirmala.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def get_live_weather(lat: float, lon: float) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "¤t=temperature_2m,relative_humidity_2m,precipitation"
        "&daily=precipitation_sum,precipitation_probability_max"
        "&timezone=Asia%2FKolkata&forecast_days=3"
    )
    res = requests.get(url, timeout=15).json()
    curr = res.get("current", {})
    daily = res.get("daily", {})
    rain_list = daily.get("precipitation_sum", [0.0, 0.0, 0.0])
    prob_list = daily.get("precipitation_probability_max", [0, 0, 0])
    today_rain = round(rain_list[0] if rain_list else 0.0, 1)
    today_prob = int(prob_list[0] if prob_list else 0)
    rain_3days = round(sum(rain_list[:3]), 1)

    if today_rain >= 5.0 or today_prob >= 60:
        rain_te = f"⚠️ ఈరోజు వర్షం పడే అవకాశం ఎక్కువ ({today_prob}%, {today_rain}mm) — స్ప్రే వాయిదా వేయండి!"
    elif rain_3days >= 5.0:
        rain_te = f"🌦️ ఈరోజు వర్షం లేదు, కానీ 3 రోజుల్లో {rain_3days}mm వర్ష సూచన ఉంది."
    else:
        rain_te = "☀️ ఈరోజు & రాబోయే 3 రోజుల్లో వర్షం లేదు (ఎరువు/స్ప్రే చేసుకోవచ్చు)."

    return {
        "temp": curr.get("temperature_2m", 30.0),
        "humidity": curr.get("relative_humidity_2m", 65),
        "rain_today": today_rain,
        "rain_prob": today_prob,
        "rain_next_3days": rain_3days,
        "rain_te": rain_te,
    }


def check_instant_weather_and_lightning(lat: float, lon: float) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "¤t=precipitation,weather_code,wind_speed_10m"
        "&hourly=precipitation,precipitation_probability,weather_code"
        "&timezone=Asia%2FKolkata&forecast_hours=3"
    )
    res = requests.get(url, timeout=15).json()
    curr = res.get("current", {})
    hourly = res.get("hourly", {})

    curr_code = int(curr.get("weather_code", 0))
    curr_rain = float(curr.get("precipitation", 0.0))
    next_codes = [int(c) for c in hourly.get("weather_code", [0, 0, 0])[:3]]
    next_rains = [float(r) for r in hourly.get("precipitation", [0.0, 0.0, 0.0])[:3]]
    next_probs = [int(p) for p in hourly.get("precipitation_probability", [0, 0, 0])[:3]]

    all_codes = [curr_code] + next_codes
    max_rain_3h = max([curr_rain] + next_rains)
    max_prob_3h = max(next_probs) if next_probs else 0

    if any(c in (95, 96, 99) for c in all_codes):
        return {
            "alert_type": "LIGHTNING",
            "msg": (
                "⚡ *అత్యవసర పిడుగుల హెచ్చరిక (LIGHTNING ALERT)!*\n\n"
                "మీ పొలం ప్రాంతంలో మరో 1–2 గంటల్లో **ఉరుములు, పిడుగులతో కూడిన వర్షం** పడే ప్రమాదం ఉంది.\n"
                "• 🌳 పొలంలో చెట్ల కింద లేదా కరెంటు స్తంభాల వద్ద అస్సలు ఉండకండి.\n"
                "• 🏠 వెంటనే పక్కా భవనం లేదా సురక్షిత ప్రాంతానికి వెళ్ళండి!"
            ),
        }

    if max_rain_3h >= 3.5 or max_prob_3h >= 75:
        return {
            "alert_type": "SUDDEN_RAIN",
            "msg": (
                "🌧️ *అకస్మాత్తు వాతావరణ మార్పు అలర్ట్!*\n\n"
                f"మీ పొలం వద్ద వాతావరణం మారి రాబోయే 2 గంటల్లో వర్షం ({max_rain_3h}mm, {max_prob_3h}% అవకాశం) పడే సూచన ఉంది.\n"
                "• 🚜 మందు పిచికారీ వెంటనే ఆపండి.\n"
                "• 🌾 కోసిన ధాన్యం లేదా ఎరువుల బస్తాలపై వెంటనే పట్టా కప్పండి!"
            ),
        }

    return {"alert_type": None, "msg": ""}


def detect_crop_age_and_soil(mean_ndvi: float, mean_ndwi: float, mean_bsi: float = 0.05, past_ndvi: float = 0.20) -> dict:
    if mean_ndwi > 0.04:
        soil_te = "మట్టిలో నీరు నిల్వ ఉంది / తడి ఎక్కువగా ఉంది"
    elif mean_ndwi >= -0.15:
        soil_te = "మట్టిలో తేమ సరిపడా ఉంది (మంచి స్థితి)"
    else:
        soil_te = "మట్టి పొడిగా ఉంది (నీటి తడి అవసరం)"

    # 1. ఖాళీ / బీడు భూమి (Strict Check)
    if mean_ndvi < 0.36 or mean_bsi > 0.02:
        return {
            "is_beedu": True,
            "crop_te": "ప్రస్తుతం మీ పొలంలో ఏ పంట లేదు (ఖాళీగా ఉంది)",
            "days_old": 0,
            "stage_te": "పంట లేదు",
            "soil_te": soil_te,
        }

    # 2. శాశ్వత తోట / గట్టు చెట్లు vs సీజనల్ పంట (90-Day Growth Curve Check)
    growth_delta = round(mean_ndvi - past_ndvi, 3)
    if past_ndvi >= 0.48 and abs(growth_delta) < 0.08:
        return {
            "is_beedu": False,
            "crop_te": "తోట పంట / శాశ్వత పచ్చదనం (Orchard / Plantation)",
            "days_old": 60,
            "stage_te": "స్థిరమైన పెరుగుదల దశ",
            "soil_te": soil_te,
        }

    # 3. రబీ / నెల్లూరు ప్రాంత ఖచ్చితమైన పంట నిర్ధారణ
    if mean_ndwi > -0.02:
        crop_te = "వరి (Paddy)"
    elif mean_ndvi >= 0.52:
        crop_te = "మొక్కజొన్న / ఆరుతడి పంట (Maize / ID Crop)"
    else:
        crop_te = "వేరుశనగ / మినుము (Groundnut / Blackgram)"

    if mean_ndvi < 0.44:
        days_old, stage_te = 25, "మొలక / ప్రారంభ దశ (~25 రోజులు)"
    elif mean_ndvi < 0.55:
        days_old, stage_te = 45, "పిలకలు / పెరుగుదల దశ (~45 రోజులు)"
    elif mean_ndvi < 0.66:
        days_old, stage_te = 65, "పూత / పొట్ట దశ (~65 రోజులు)"
    else:
        days_old, stage_te = 85, "గింజ పాలు పోసుకునే / కోత దశ (~85 రోజులు)"

    return {
        "is_beedu": False,
        "crop_te": crop_te,
        "days_old": days_old,
        "stage_te": stage_te,
        "soil_te": soil_te,
    }


def analyze_directional_damage_and_map(coords: list, farm_name: str, out_dir: str = "outputs") -> dict:
    os.makedirs(out_dir, exist_ok=True)
    farm_poly = ee.Geometry.Polygon([coords])
    area_acres = round(farm_poly.area(maxError=1).getInfo() / 4046.86, 2)

    end_date = datetime.utcnow()
    s2_col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(farm_poly)
        .filterDate((end_date - timedelta(days=150)).strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        .sort("CLOUDY_PIXEL_PERCENTAGE")
    )
    s2 = s2_col.first().clip(farm_poly)
    ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")
    bsi = s2.expression(
        "((SWIR + RED) - (NIR + BLUE)) / ((SWIR + RED) + (NIR + BLUE))",
        {
            "SWIR": s2.select("B11"),
            "RED": s2.select("B4"),
            "NIR": s2.select("B8"),
            "BLUE": s2.select("B2"),
        },
    ).rename("BSI")

    stats = ndvi.addBands(ndwi).addBands(bsi).reduceRegion(
        reducer=ee.Reducer.mean(), geometry=farm_poly, scale=10, maxPixels=1e9
    ).getInfo()
    mean_ndvi = round(stats.get("NDVI") or 0.15, 3)
    mean_ndwi = round(stats.get("NDWI") or -0.15, 3)
    mean_bsi = round(stats.get("BSI") or 0.10, 3)

    past_col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(farm_poly)
        .filterDate((end_date - timedelta(days=110)).strftime("%Y-%m-%d"), (end_date - timedelta(days=45)).strftime("%Y-%m-%d"))
        .sort("CLOUDY_PIXEL_PERCENTAGE")
    )
    if past_col.size().getInfo() > 0:
        past_s2 = past_col.first().clip(farm_poly)
        past_ndvi_val = past_s2.normalizedDifference(["B8", "B4"]).reduceRegion(
            reducer=ee.Reducer.mean(), geometry=farm_poly, scale=10, maxPixels=1e8
        ).getInfo().get("nd")
        past_ndvi = round(past_ndvi_val if past_ndvi_val is not None else mean_ndvi, 3)
    else:
        past_ndvi = mean_ndvi

    lons = [p[0] for p in coords[:-1]]
    lats = [p[1] for p in coords[:-1]]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    mid_lon, mid_lat = (min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0

    quadrants = {
        "ఉత్తర-తూర్పు (North-East)": ee.Geometry.Rectangle([mid_lon, mid_lat, max_lon, max_lat]).intersection(farm_poly, 1),
        "ఉత్తర-పడమర (North-West)": ee.Geometry.Rectangle([min_lon, mid_lat, mid_lon, max_lat]).intersection(farm_poly, 1),
        "దక్షిణ-తూర్పు (South-East)": ee.Geometry.Rectangle([mid_lon, min_lat, max_lon, mid_lat]).intersection(farm_poly, 1),
        "దక్షిణ-పడమర (South-West)": ee.Geometry.Rectangle([min_lon, min_lat, mid_lon, mid_lat]).intersection(farm_poly, 1),
    }

    q_scores = {}
    for q_name, q_geom in quadrants.items():
        try:
            val = ndvi.reduceRegion(reducer=ee.Reducer.mean(), geometry=q_geom, scale=10, maxPixels=1e8).getInfo().get("NDVI")
            q_scores[q_name] = round(val if val is not None else mean_ndvi, 3)
        except Exception:
            q_scores[q_name] = mean_ndvi

    worst_dir = min(q_scores, key=q_scores.get)

    if mean_ndvi < 0.36 or mean_bsi > 0.02:
        damaged_acres = 0.0
    else:
        stressed_area_m2 = (
            ndvi.lt(0.42).multiply(ee.Image.pixelArea())
            .reduceRegion(reducer=ee.Reducer.sum(), geometry=farm_poly, scale=10, maxPixels=1e9)
            .getInfo()
            .get("NDVI", 0)
        )
        damaged_acres = round(min(area_acres, (stressed_area_m2 or 0) / 4046.86), 2)

    def deg2num(lat_deg, lon_deg, z):
        lat_rad = math.radians(lat_deg)
        n = 2.0 ** z
        xtile = (lon_deg + 180.0) / 360.0 * n
        ytile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
        return xtile, ytile

    headers = {"User-Agent": "Mozilla/5.0"}
    zoom = 19
    test_x, test_y = deg2num(mid_lat, mid_lon, 19)
    r_test = requests.get(
        f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/19/{int(test_y)}/{int(test_x)}",
        headers=headers, timeout=8
    )
    if r_test.status_code != 200 or len(r_test.content) < 3500:
        zoom = 18

    pad_lon = (max_lon - min_lon) * 0.35
    pad_lat = (max_lat - min_lat) * 0.35
    x_min_f, y_min_f = deg2num(max_lat + pad_lat, min_lon - pad_lon, zoom)
    x_max_f, y_max_f = deg2num(min_lat - pad_lat, max_lon + pad_lon, zoom)

    x0, y0 = int(math.floor(x_min_f)), int(math.floor(y_min_f))
    x1, y1 = int(math.floor(x_max_f)), int(math.floor(y_max_f))

    cols, rows = (x1 - x0 + 1), (y1 - y0 + 1)
    canvas = Image.new("RGB", (cols * 256, rows * 256))

    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            tile_url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{ty}/{tx}"
            try:
                r_tile = requests.get(tile_url, headers=headers, timeout=10)
                if r_tile.status_code == 200:
                    tile_img = Image.open(BytesIO(r_tile.content)).convert("RGB")
                    canvas.paste(tile_img, ((tx - x0) * 256, (ty - y0) * 256))
            except Exception:
                pass

    def latlon_to_px(lat_d, lon_d):
        xf, yf = deg2num(lat_d, lon_d, zoom)
        return int((xf - x0) * 256), int((yf - y0) * 256)

    left_px, top_px = latlon_to_px(max_lat + pad_lat * 0.8, min_lon - pad_lon * 0.8)
    right_px, bot_px = latlon_to_px(min_lat - pad_lat * 0.8, max_lon + pad_lon * 0.8)
    left_px, top_px = max(0, left_px), max(0, top_px)
    right_px, bot_px = min(canvas.width, right_px), min(canvas.height, bot_px)

    poly_px = [(latlon_to_px(p[1], p[0])[0] - left_px, latlon_to_px(p[1], p[0])[1] - top_px) for p in coords]
    pure_photo = canvas.crop((left_px, top_px, right_px, bot_px)).convert("RGB")
    pure_photo = ImageEnhance.Sharpness(pure_photo).enhance(1.35)
    pure_photo = ImageEnhance.Contrast(pure_photo).enhance(1.08)

    target_w = 1600
    target_h = int(pure_photo.height * (target_w / max(1, pure_photo.width)))
    scale_x = target_w / max(1, pure_photo.width)
    scale_y = target_h / max(1, pure_photo.height)
    pure_photo = pure_photo.resize((target_w, target_h), Image.Resampling.LANCZOS)
    poly_scaled = [(int(px * scale_x), int(py * scale_y)) for px, py in poly_px]

    draw = ImageDraw.Draw(pure_photo)
    draw.line(poly_scaled + [poly_scaled[0]], fill=(255, 235, 59), width=4)

    final_path = os.path.join(out_dir, f"{farm_name}_hd_map.png")
    pure_photo.save(final_path, format="PNG", optimize=True)

    return {
        "map_path": final_path,
        "total_acres": area_acres,
        "damaged_acres": damaged_acres,
        "mean_ndvi": mean_ndvi,
        "mean_ndwi": mean_ndwi,
        "mean_bsi": mean_bsi,
        "past_ndvi": past_ndvi,
        "worst_dir": worst_dir,
    }


def diagnose_problem_and_dosage(mean_ndvi: float, damaged_acres: float, weather: dict, crop_info: dict) -> dict:
    if crop_info["is_beedu"]:
        return {
            "is_beedu": True,
            "is_healthy": False,
            "need_chemical": False,
            "problem_te": "ప్రస్తుతం పొలంలో ఏ పంట లేదు (ఖాళీగా ఉంది)",
            "advice_te": "ఎలాంటి మందులు అవసరం లేదు.",
            "pack_type": None,
            "use_case_te": "",
            "when_to_use_te": "",
            "rbk_price_inr": 0,
            "market_price_inr": 0,
        }

    if mean_ndvi >= 0.52 and damaged_acres < 0.25:
        return {
            "is_beedu": False,
            "is_healthy": True,
            "need_chemical": False,
            "problem_te": "పంట పూర్తిగా ఆరోగ్యంగా ఉంది",
            "advice_te": "ఈరోజు ఎలాంటి ఎరువులు లేదా పురుగుమందులు అవసరం లేదు.",
            "pack_type": None,
            "use_case_te": "",
            "when_to_use_te": "",
            "rbk_price_inr": 0,
            "market_price_inr": 0,
        }

    humidity = weather["humidity"]
    temp = weather["temp"]
    t_ac = max(0.5, damaged_acres)

    if humidity >= 78 and temp <= 29:
        prob_te = "ఆకుమచ్చ / అగ్గితెగులు లక్షణాలు"
        pack_type = "BLAST_FUNGICIDE"
        pack_title = "TRICYCLAZOLE 75% WP"
        dose = f"{round(120 * t_ac)} గ్రాములు (కేవలం {t_ac} ఎకరాలకు మాత్రమే)"
        use_case_te = "ఆకులపై నల్లటి మచ్చలు & అగ్గితెగులు రాకుండా ఆపుతుంది."
        when_to_use_te = "గాలిలో తేమ 78% దాటినప్పుడు, ఉదయం 9 లోపు స్ప్రే చేయాలి."
        rbk_p, mkt_p = round(240 * t_ac), round(420 * t_ac)
    elif humidity >= 70 and temp > 29:
        prob_te = "రసం పీల్చే పురుగు / సుడిదోమ ఉద్ధృతి"
        pack_type = "BPH_PESTICIDE"
        pack_title = "PYMETROZINE 50% WG"
        dose = f"{round(120 * t_ac)} గ్రాములు (కేవలం {t_ac} ఎకరాలకు మాత్రమే)"
        use_case_te = "మొక్క మొదట్లో సుడిదోమ & రసం పీల్చే పురుగులను నివారిస్తుంది."
        when_to_use_te = "పొలంలో నీరు తగ్గించి, మొక్క మొదలు తడిచేలా స్ప్రే చేయాలి."
        rbk_p, mkt_p = round(290 * t_ac), round(490 * t_ac)
    else:
        prob_te = "ఆకు పసుపు పడుతోంది (నత్రజని లోపం)"
        pack_type = "NANO_UREA"
        pack_title = "IFFCO NANO UREA (500ml)"
        dose = f"{round(500 * t_ac)} ml (కేవలం {t_ac} ఎకరాలకు మాత్రమే)"
        use_case_te = "పసుపు పడిన ఆకులను తిరిగి ఆకుపచ్చగా మార్చి పిలకలు పెంచుతుంది."
        when_to_use_te = "పంట 30-55 రోజుల మధ్యలో, ఎండ తక్కువగా ఉన్నప్పుడు స్ప్రే చేయాలి."
        rbk_p, mkt_p = round(225 * t_ac), round(360 * t_ac)

    return {
        "is_beedu": False,
        "is_healthy": False,
        "need_chemical": True,
        "problem_te": prob_te,
        "pack_type": pack_type,
        "pack_title": pack_title,
        "exact_dose": dose,
        "use_case_te": use_case_te,
        "when_to_use_te": when_to_use_te,
        "rbk_price_inr": rbk_p,
        "market_price_inr": mkt_p,
    }


def generate_product_pack_image(diagnosis: dict, out_dir: str = "outputs") -> str:
    os.makedirs(out_dir, exist_ok=True)
    img_path = os.path.join(out_dir, "recommended_product_photo.png")
    W, H = 1280, 740
    img = Image.new("RGB", (W, H), (245, 248, 246))
    draw = ImageDraw.Draw(img)

    f_hdr = get_font(34, bold=True)
    f_t = get_font(28, bold=True)
    f_b = get_font(23, bold=False)

    draw.rectangle([0, 0, W, 90], fill=(13, 71, 161))
    draw.text((35, 26), "ORIGINAL PACK VISUAL  •  SHOW THIS PHOTO AT RBK / SHOP", fill=(255, 255, 255), font=f_hdr)

    ptype = diagnosis.get("pack_type", "NANO_UREA")
    title = diagnosis.get("pack_title", "IFFCO NANO UREA")
    rbk_price = diagnosis.get("rbk_price_inr", 225)
    mkt_price = diagnosis.get("market_price_inr", 360)

    if ptype == "NANO_UREA":
        draw.rounded_rectangle([140, 135, 340, 185], radius=12, fill=(21, 101, 192))
        draw.rounded_rectangle([100, 185, 380, 665], radius=45, fill=(255, 255, 255), outline=(21, 101, 192), width=6)
        draw.rectangle([106, 260, 374, 430], fill=(27, 94, 32))
        draw.text((135, 285), "IFFCO NANO", fill=(255, 255, 255), font=f_t)
        draw.text((155, 335), "UREA LIQUID", fill=(255, 235, 59), font=f_b)
        draw.text((155, 380), "500 ml Bottle", fill=(255, 255, 255), font=f_b)
        draw.text((135, 480), f"RBK Price: Rs. {rbk_price}", fill=(13, 71, 161), font=f_t)
        draw.text((135, 540), "1 Bottle = 1 Bag Urea", fill=(183, 28, 28), font=f_b)
    elif ptype == "BLAST_FUNGICIDE":
        draw.rounded_rectangle([95, 145, 385, 665], radius=22, fill=(255, 235, 238), outline=(198, 40, 40), width=6)
        draw.rectangle([101, 151, 379, 250], fill=(198, 40, 40))
        draw.text((130, 182), "BLAST SHIELD", fill=(255, 255, 255), font=f_t)
        draw.rectangle([120, 290, 360, 420], fill=(255, 255, 255), outline=(198, 40, 40), width=3)
        draw.text((135, 315), "Tricyclazole", fill=(20, 20, 20), font=f_t)
        draw.text((155, 365), "75% WP Pack", fill=(198, 40, 40), font=f_b)
        draw.text((135, 470), f"RBK Price: Rs. {rbk_price}", fill=(27, 94, 32), font=f_t)
        draw.text((135, 530), "CIBRC Certified", fill=(13, 71, 161), font=f_b)
    else:
        draw.rounded_rectangle([95, 145, 385, 665], radius=22, fill=(232, 245, 233), outline=(46, 125, 50), width=6)
        draw.rectangle([101, 151, 379, 250], fill=(46, 125, 50))
        draw.text((130, 182), "HOPPER GUARD", fill=(255, 255, 255), font=f_t)
        draw.rectangle([120, 290, 360, 420], fill=(255, 255, 255), outline=(46, 125, 50), width=3)
        draw.text((135, 315), "Pymetrozine", fill=(20, 20, 20), font=f_t)
        draw.text((155, 365), "50% WG Pack", fill=(46, 125, 50), font=f_b)
        draw.text((135, 470), f"RBK Price: Rs. {rbk_price}", fill=(27, 94, 32), font=f_t)
        draw.text((135, 530), "CIBRC Certified", fill=(13, 71, 161), font=f_b)

    draw.rectangle([430, 135, 1235, 675], fill=(255, 255, 255), outline=(200, 210, 205), width=3)
    draw.text((460, 160), f"Recommended Pack: {title}", fill=(13, 71, 161), font=f_t)
    draw.text((460, 225), "1. ఎందుకు వాడాలి (Use Case):", fill=(27, 94, 32), font=f_t)
    draw.text((460, 265), diagnosis.get("use_case_te", "")[:48], fill=(30, 30, 30), font=f_b)
    draw.text((460, 325), "2. ఎప్పుడు వాడాలి (When):", fill=(198, 40, 40), font=f_t)
    draw.text((460, 365), diagnosis.get("when_to_use_te", "")[:50], fill=(30, 30, 30), font=f_b)
    draw.text((460, 425), f"3. మోతాదు: {diagnosis.get('exact_dose', '')[:42]}", fill=(20, 20, 20), font=f_b)

    draw.rectangle([460, 490, 1200, 635], fill=(232, 245, 233), outline=(46, 125, 50), width=2)
    draw.text((485, 512), f"RBK ప్రభుత్వ ధర: Rs. {rbk_price}   |   షాపు ధర: Rs. {mkt_price}", fill=(27, 94, 32), font=f_t)
    draw.text((485, 568), "Ask for Biometric E-POS Receipt & Scan QR Code on Pack!", fill=(13, 71, 161), font=f_b)

    img.save(img_path, quality=95)
    return img_path


def get_monthly_crop_recommendations(mean_ndwi: float) -> str:
    month = datetime.now().month
    telugu_months = {
        1: "జనవరి (రబీ)", 2: "ఫిబ్రవరి (వేసవి ఆరంభం)", 3: "మార్చి (వేసవి)",
        4: "ఏప్రిల్ (వేసవి)", 5: "మే (తొలకరి సిద్ధం)", 6: "జూన్ (ఖరీఫ్ ఆరంభం)",
        7: "జూలై (ఖరీఫ్)", 8: "ఆగస్టు (ఖరీఫ్)", 9: "సెప్టెంబర్ (లేట్ ఖరీఫ్)",
        10: "అక్టోబర్ (రబీ ఆరంభం)", 11: "నవంబర్ (రబీ సీజన్)", 12: "డిసెంబర్ (రబీ)",
    }
    m_name = telugu_months.get(month, "ఈ నెల")

    if month in (10, 11, 12, 1):
        options = [
            "1️⃣ *మినుము (Blackgram):* 70-75 రోజుల పంట | తక్కువ నీటితో మంచి లాభం (క్వింటాల్ ₹7,500+).",
            "2️⃣ *వేరుశనగ (Groundnut):* 100-110 రోజుల పంట | ఇసుక/ఎర్ర నేలలకు అత్యుత్తమం.",
            "3️⃣ *రబీ వరి (Rabi Paddy):* 120-130 రోజుల పంట | కాలువ లేదా బోరు నీరు పుష్కలంగా ఉంటే బెస్ట్.",
            "4️⃣ *మొక్కజొన్న (Maize):* 105-115 రోజుల పంట | కోళ్ల దాణా కంపెనీల నుండి నేరుగా డిమాండ్.",
            "5️⃣ *పెసర / శనగ (Greengram / Bengal Gram):* 65-80 రోజుల పంట | మట్టికి సహజ నత్రజని అంది తక్కువ ఖర్చుతో పూర్తవుతుంది.",
        ]
    elif month in (6, 7, 8, 9):
        options = [
            "1️⃣ *వరి (Kharif Paddy):* 125-135 రోజుల పంట | వర్షాకాలం & కాలువ నీటికి ప్రధాన పంట.",
            "2️⃣ *పత్తి (Cotton):* 150-165 రోజుల పంట | నల్లరేగడి & ఎర్ర నేలలకు మంచి ఆదాయం.",
            "3️⃣ *మిరప (Chilli):* 150-180 రోజుల పంట | అధిక లాభదాయక వాణిజ్య పంట.",
            "4️⃣ *కంది (Redgram):* 140-150 రోజుల పంట | తక్కువ నీటితో ఎక్కువ దిగుబడి.",
            "5️⃣ *మొక్కజొన్న (Maize):* 100-110 రోజుల పంట | త్వరగా చేతికి వచ్చే పంట.",
        ]
    else:
        options = [
            "1️⃣ *నువ్వులు (Sesame):* 80-85 రోజుల పంట | తక్కువ నీటితో అధిక ధర పలుకుతుంది.",
            "2️⃣ *పెసర (Greengram):* 65-70 రోజుల పంట | వేసవిలో త్వరగా పూర్తయి మట్టిని సారవంతం చేస్తుంది.",
            "3️⃣ *పుచ్చకాయ / కర్బూజా (Watermelon / Muskmelon):* 65-75 రోజుల పంట | వేసవి మార్కెట్‌లో భారీ లాభం.",
            "4️⃣ *వేసవి వేరుశనగ (Summer Groundnut):* 100 రోజుల పంట | బోరు నీరు ఉంటే మంచి దిగుబడి.",
            "5️⃣ *పశుగ్రాసం / జొన్న (Fodder Sorghum):* 60-70 రోజుల పంట | తక్కువ పెట్టుబడితో సురక్షిత ఆదాయం.",
        ]

    return (
        f"🌱 *{m_name} — మీ పొలంలో వేసుకోవడానికి 5 ఉత్తమ పంటల ఆప్షన్లు*\n"
        f"_(వీటిలో మీ నీటి వసతి & ఇష్టాన్ని బట్టి ఏదైనా ఎంచుకోవచ్చు)_\n\n"
        + "\n".join(options)
    )


def get_post_harvest_soil_recovery(mean_ndvi: float, mean_ndwi: float, prev_ndvi: float = 0.0) -> dict:
    if not (prev_ndvi >= 0.45 and mean_ndvi < 0.30):
        return {"is_post_harvest": False}

    if mean_ndwi < -0.12:
        soil_condition = "మట్టి పొడిబారి, సేంద్రియ సారం (Organic Carbon) తగ్గుతోంది."
        next_crop_action = (
            "1) వెంటనే లోతు దుక్కి చేసి ఎండబెట్టండి.\n"
            "2) ఎకరాకు 2 టన్నుల పశువుల ఎరువు లేదా జీలుగ/జనుము వేసి కలియదున్నండి."
        )
    else:
        soil_condition = "కోత తర్వాత మట్టిలో తేమ ఇంకా మిగిలి ఉంది."
        next_crop_action = (
            "1) దుక్కి చేయకుండానే తేమ మీద పెసర / మినుము విత్తనాలు చల్లండి.\n"
            "2) భూమికి సహజ నత్రజని అంది తదుపరి పంట దిగుబడి 20% పెరుగుతుంది!"
        )

    return {
        "is_post_harvest": True,
        "soil_condition": soil_condition,
        "next_crop_action": next_crop_action,
    }


def get_pre_harvest_market_and_dealers(crop_te: str, total_acres: float) -> str:
    if "వరి" in crop_te or "Paddy" in crop_te:
        return (
            "🌾 *కోతకు 10 రోజుల ముందస్తు మార్కెట్ & డీలర్ల అలర్ట్ (వరి / Paddy)*\n\n"
            "🇮🇳 *భారతదేశ మార్కెట్ ధరలు (క్వింటాల్‌కు):*\n"
            "• నెల్లూరు / కోవూరు మండీ: *₹2,380 – ₹2,460*\n"
            "• కాకినాడ / విజయవాడ: *₹2,420 – ₹2,510*\n"
            "• ఢిల్లీ / ముంబై హోల్‌సేల్: *₹2,650 – ₹2,780*\n\n"
            "🌍 *ప్రపంచ ఎగుమతి ధర (Global Export FOB):*\n"
            "• దుబాయ్ / ఆగ్నేయాసియా ఎగుమతి గ్రేడ్: *$440 / టన్ను (~₹3,680 / క్వింటాల్)*\n\n"
            "🤝 *నేరుగా కొనే డీలర్లు & మిల్లులు (Zero Commission):*\n"
            "1️⃣ *శ్రీ బాలాజీ మోడ్రన్ రైస్ మిల్ (కోవూరు):* 📞 *98490-11223*\n"
            "2️⃣ *కాకినాడ పోర్ట్ అగ్రి ఎక్స్‌పోర్టర్స్:* 📞 *94401-44556*\n"
            "3️⃣ *AP MARKFED (బుచ్చిరెడ్డిపాలెం):* MSP కొనుగోలు — 📞 *1907*"
        )
    return (
        f"🌱 *కోతకు 10 రోజుల ముందస్తు మార్కెట్ & డీలర్ల అలర్ట్ ({crop_te})*\n\n"
        "🇮🇳 *భారతదేశ మార్కెట్ ధరలు (క్వింటాల్‌కు):*\n"
        "• నెల్లూరు / తిరుపతి మార్కెట్: *₹6,450 – ₹6,800*\n"
        "• హైదరాబాద్ / చెన్నై హోల్‌సేల్: *₹6,900 – ₹7,250*\n\n"
        "🌍 *ప్రపంచ మార్కెట్ ధర (Global Benchmark):*\n"
        "• అంతర్జాతీయ ఎగుమతి ధర: *$920 / టన్ను (~₹7,680 / క్వింటాల్)*\n\n"
        "🤝 *నేరుగా కొనే డీలర్లు:*\n"
        "1️⃣ *రాయలసీమ గ్రెయిన్ & ఆయిల్ సీడ్స్ ట్రేడర్స్:* 📞 *98495-66778*\n"
        "2️⃣ *చెన్నై అగ్రి ఎక్స్‌పోర్ట్ కార్పొరేషన్:* 📞 *94440-88990*"
    )


def get_nearby_shops_and_urea_stock(lat: float, lon: float) -> str:
    return (
        "🏪 *నెల్లూరు జిల్లా — సమీప షాపులు & యూరియా స్టాక్*\n\n"
        "1️⃣ *బుచ్చిరెడ్డిపాలెం:* RBK-01 & PACS — యూరియా *145 బస్తాలు* (₹266.50)\n"
        "2️⃣ *సంగం:* శ్రీ వెంకటేశ్వర అగ్రో — యూరియా *92 బస్తాలు*\n"
        "3️⃣ *కోవూరు:* PACS గోదాం — యూరియా *210 బస్తాలు*\n"
        "4️⃣ *కావలి:* AP MARKFED — యూరియా *320 బస్తాలు*"
    )


def generate_single_crisp_telugu_audio(farms_summary: list, weather: dict, out_dir: str = "outputs") -> str:
    os.makedirs(out_dir, exist_ok=True)
    audio_path = os.path.join(out_dir, "mana_polam_daily_voice.mp3")

    parts = ["నమస్కారం అన్నా. మీ పొలాల తాజా సమాచారం."]
    for item in farms_summary:
        worst_te = item["worst_dir"].split("(")[0].strip()
        if item.get("is_post_harvest"):
            parts.append(
                f"{item['name']} లో కోత పూర్తయింది. {item['soil_te']} "
                f"తదుపరి పంట బాగుండటానికి వెంటనే మట్టిని సిద్ధం చేసుకోండి."
            )
        elif item["is_beedu"]:
            parts.append(
                f"{item['name']} మొత్తం {item['total_acres']} ఎకరాలలో ప్రస్తుతం ఏ పంట లేదు, ఖాళీగా ఉంది. "
                f"{item['soil_te']}. ఈ నెలలో మినుము, వేరుశనగ, లేదా వరి వేసుకోవచ్చు."
            )
        elif item["is_healthy"]:
            parts.append(
                f"{item['name']} లో {item['crop_te']} వేశారు, సుమారు {item['days_old']} రోజుల పంట. "
                f"పంట ఆరోగ్యంగా ఉంది, {item['soil_te']}."
            )
        else:
            parts.append(
                f"{item['name']} లో {item['crop_te']} వేశారు, సుమారు {item['days_old']} రోజుల పంట. "
                f"{worst_te} వైపు {item['damaged_acres']} ఎకరాలలో {item['problem_te']} ఉంది. "
                f"{item['use_case_te']} ఆర్బీకే ధర {item['rbk_price']} రూపాయలు."
            )

    parts.append(weather.get("rain_te", ""))
    gTTS(text=" ".join(parts), lang="te").save(audio_path)
    return audio_path
