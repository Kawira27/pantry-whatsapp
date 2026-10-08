import os
import re
import json
import random
import logging
import time
import hashlib
import hmac
import base64
import requests
from flask import Flask, request, abort
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
from supabase import create_client, Client
from dotenv import load_dotenv
from collections import defaultdict
from threading import Lock
from translations import t

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

supabase: Client = create_client(
    os.environ["SUPABASE_URL"].rstrip("/").replace("/rest/v1", ""),
    os.environ["SUPABASE_KEY"],
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

# ── Security ───────────────────────────────────────────────────────────────────

_rate_store: dict = defaultdict(list)
_rate_lock = Lock()
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60

def is_rate_limited(phone: str) -> bool:
    now = time.time()
    with _rate_lock:
        timestamps = _rate_store[phone]
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        _rate_store[phone] = timestamps
        if len(timestamps) >= RATE_LIMIT_MAX:
            return True
        timestamps.append(now)
        return False


def validate_twilio_signature(request) -> bool:
    strict = os.environ.get("TWILIO_STRICT_VALIDATION", "false").lower() == "true"
    if not TWILIO_AUTH_TOKEN:
        log.warning("⚠️ TWILIO_AUTH_TOKEN not set — skipping signature validation")
        return True
    try:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            log.warning("No Twilio signature header")
            return not strict
        params = request.form.to_dict()
        urls_to_try = [
            request.url.replace("http://", "https://"),
            request.url,
            f"https://pantry-whatsapp-production.up.railway.app/whatsapp",
        ]
        for url in urls_to_try:
            if validator.validate(url, params, signature):
                log.info(f"✅ Signature valid with URL: {url}")
                return True
        log.warning(f"🚨 Signature invalid. Strict={strict}. URL tried: {urls_to_try[0]}")
        return not strict
    except Exception as e:
        log.warning(f"Signature validation error: {e}")
        return True


def sanitise_input(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:1000].strip()

CUISINES = [
    "Kenyan", "Indian", "Italian", "Chinese",
    "Mexican", "Mediterranean", "American", "International"
]

# ── Tier limits ────────────────────────────────────────────────────────────────
TIER_LIMITS = {
    "free": {
        "recipe_suggestions": 5,
        "photo_scans": 2,
        "ai_chef_chats": 3,
        "pantry_items": 20,
        "saved_recipes": 5,
    },
    "premium": {
        "recipe_suggestions": 999,
        "photo_scans": 999,
        "ai_chef_chats": 999,
        "pantry_items": 999,
        "saved_recipes": 999,
    },
}

PREMIUM_PRICE = "Ksh 299/month"

# ── Abuse filter — basic keyword list (DB has more) ───────────────────────────
LOCAL_BLOCKED = [
    "fuck", "shit", "bitch", "bastard", "kill yourself",
    "i will hack", "motherfucker", "asshole", "cunt",
]

HELP_MSG = """🍳 *Tunapika* — What can I do?

*Getting recipes:*
🍽️ *cook* — Suggest a recipe
🌅 *breakfast* — Breakfast ideas
☀️ *lunch* — Lunch ideas
🌙 *dinner* — Dinner ideas
📅 *meal prep* — Weekly meal plan

*Your pantry:*
🧺 *pantry* — View ingredients
Just tell me naturally what you have or used up!
_"I just bought chicken and tomatoes"_
_"I finished the rice"_

*Recipes:*
⭐ *saved* — Your saved recipes
_"save Pilau"_ — Save a recipe

*Profile:*
👤 *profile* — View your profile
✏️ *edit profile* — Update preferences

Type *help* anytime to see this menu."""


# ── NLU: ingredient extraction ────────────────────────────────────────────────

REMOVE_SIGNALS = [
    "used up", "ran out", "no more", "finished", "don't have", "do not have",
    "out of", "used the last", "all out", "none left", "imekwisha", "nimemaliza",
    "hakuna", "nimetumia", "imeisha", "used all",
]

ADD_SIGNALS = [
    "i have", "i also have", "i've got", "i also got", "i got", "i bought",
    "i also bought", "just bought", "just got", "also picked up", "also got",
    "we have", "we also have", "at home", "in my fridge", "in the fridge",
    "in my kitchen", "i picked up", "picked up", "i found", "there's some",
    "got some", "went shopping", "from the shop", "from the market",
    "i picked", "purchased", "i also picked", "forgot to mention",
    "also have", "oh and i have", "oh i also",
    "one ", "two ", "three ", "a packet", "a bag", "a bunch", "a loaf",
    "a tin", "a can", "a bottle", "a kilo", "half a", "some ",
    "nimenunua", "niko na", "nimepata", "niko nazo", "kuna", "nimebuy",
    "pia niko na", "pia nimenunua", "niko na", "nina ", "tuna ",
    "niliambia", "nimechukua", "nimepata",
]


def parse_pantry_intent_local(message: str, known_ingredients: list[str]) -> dict:
    m = message.lower()
    is_remove = any(sig in m for sig in REMOVE_SIGNALS)
    is_add = any(sig in m for sig in ADD_SIGNALS)
    if not is_remove and not is_add:
        return {"intent": "none", "ingredients": []}
    intent = "remove" if is_remove else "add"
    found = []
    for ing in known_ingredients:
        ing_lower = ing.lower()
        if re.search(r'\b' + re.escape(ing_lower) + r'\b', m):
            found.append(ing)
    log.info(f"🔍 Local NLU: intent={intent}, found={found}")
    return {"intent": intent, "ingredients": found}


def parse_pantry_intent(message: str, known_ingredients: list[str]) -> dict:
    is_complex = len(message.split()) > 8 or "," in message
    if ANTHROPIC_API_KEY and is_complex:
        known_str = ", ".join(known_ingredients[:150])
        prompt = f"""You are a smart pantry assistant for a Kenyan cooking app. A user sent this WhatsApp message listing their ingredients:

"{message}"

Your job: extract ALL food ingredients from this message and match them to our database.

Known ingredients database: {known_str}

Rules:
1. This is clearly an ADD message (user is listing what they have)
2. Extract every food item mentioned, including spices, condiments, dairy, grains, proteins, vegetables
3. Match to the closest name in our database (handle variants, quantities, descriptions)
4. Only include items that exist in our database (exact or close match)
5. Ignore quantities (kg, packets, crates etc)

Respond ONLY with valid JSON:
{{"intent": "add", "ingredients": ["ingredient1", "ingredient2", ...]}}"""

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=10,
            )
            text = resp.json()["content"][0]["text"].strip()
            text = re.sub(r"```json|```", "", text).strip()
            result = json.loads(text)
            log.info(f"🤖 Claude NLU: found {len(result.get('ingredients', []))} ingredients")
            return result
        except Exception as e:
            log.warning(f"Claude NLU failed: {e}")

    return parse_pantry_intent_local(message, known_ingredients)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_or_create_user(whatsapp_number: str, display_name: str = "") -> dict | None:
    number = whatsapp_number.replace("whatsapp:", "").strip()
    res = supabase.table("users").select("*").eq("whatsapp_number", number).execute()
    if res.data:
        return res.data[0]
    insert = supabase.table("users").insert({
        "whatsapp_number": number,
        "full_name": display_name or "Friend",
        "timezone": "Africa/Nairobi",
        "onboarding_complete": False,
        "onboarding_step": 0,
    }).execute()
    return insert.data[0] if insert.data else None


def update_user(user_id: str, data: dict):
    supabase.table("users").update(data).eq("id", user_id).execute()


def get_all_ingredient_names() -> list[str]:
    res = supabase.table("ingredients").select("name").execute()
    return [r["name"] for r in res.data]


def get_user_pantry(user_id: str) -> list[dict]:
    res = (
        supabase.table("user_pantry_items")
        .select("id, ingredients(id, name)")
        .eq("user_id", user_id)
        .execute()
    )
    return [
        {"pantry_item_id": r["id"], "id": r["ingredients"]["id"], "name": r["ingredients"]["name"]}
        for r in res.data
        if r.get("ingredients") and r["ingredients"].get("name")
    ]


def get_pantry_names(user_id: str) -> list[str]:
    return [i["name"].lower() for i in get_user_pantry(user_id)]


def find_ingredient_by_name(name: str) -> dict | None:
    res = supabase.table("ingredients").select("id, name").ilike("name", name.strip()).execute()
    if res.data:
        return res.data[0]
    try:
        alias_res = supabase.table("ingredient_aliases").select("ingredient_id, ingredients(id, name)").ilike("alias", name.strip()).execute()
        if alias_res.data:
            return alias_res.data[0]["ingredients"]
    except Exception:
        pass
    return None


def add_ingredients(user_id: str, names: list[str]) -> tuple[list[str], list[str]]:
    added, not_found = [], []
    existing = get_pantry_names(user_id)
    for name in names:
        name = name.strip().lower()
        if not name:
            continue
        if name in existing:
            added.append(f"{name} (already in pantry)")
            continue
        ing = find_ingredient_by_name(name)
        if not ing:
            not_found.append(name)
            continue
        supabase.table("user_pantry_items").insert({
            "user_id": user_id,
            "ingredient_id": ing["id"],
        }).execute()
        added.append(ing["name"])
    return added, not_found


def remove_ingredients(user_id: str, names: list[str]) -> tuple[list[str], list[str]]:
    removed, not_found = [], []
    pantry = get_user_pantry(user_id)
    pantry_map = {i["name"].lower(): i["pantry_item_id"] for i in pantry}
    for name in names:
        name = name.strip().lower()
        if name not in pantry_map:
            not_found.append(name)
            continue
        supabase.table("user_pantry_items").delete().eq("id", pantry_map[name]).execute()
        removed.append(name)
    return removed, not_found


def format_pantry_update(action: str, items: list[str], not_found: list[str], name: str, show_menu: bool = False, lang: str = "en") -> str:
    lines = []
    if action == "add":
        real_adds = [i for i in items if "(already" not in i]
        already = [i for i in items if "(already" in i]
        if real_adds:
            lines.append(t("pantry_added_header", lang, count=len(real_adds)))
            lines += [f"  • {i}" for i in real_adds]
        if already:
            lines.append(t("pantry_already_header", lang))
            lines += [f"  • {i.replace(' (already in pantry)', '')}" for i in already]
    else:
        if items:
            lines.append(t("pantry_removed_header", lang))
            lines += [f"  • {i}" for i in items]

    if not_found:
        lines.append(t("pantry_not_found", lang))
        lines += [f"  • {i}" for i in not_found]
        lines.append(t("pantry_not_found_hint", lang))

    if not lines:
        return t("pantry_not_found_empty", lang, name=name)

    if show_menu:
        lines += ["", t("pantry_ready_with_menu", lang, name=name)]
        lines += ["", main_menu(name, lang)]
    else:
        lines += ["", t("pantry_ready_footer", lang)]
    return "\n".join(lines)


def find_matching_recipes(pantry_names: list[str], user: dict, meal_type: str = None, max_missing: int = 0) -> list[dict]:
    query = supabase.table("recipes").select(
        "id, name, description, instructions, cuisine, meal_type, "
        "prep_time_minutes, cook_time_minutes, servings, difficulty, "
        "calories_per_serving, protein_g, carbs_g, fat_g, is_ai_generated, "
        "avg_rating, rating_count, "
        "recipe_ingredients(ingredients(name))"
    )
    if meal_type:
        if meal_type in ("lunch", "dinner"):
            query = query.in_("meal_type", [meal_type, "quick meal"])
        else:
            query = query.eq("meal_type", meal_type)
    res = query.execute()

    allergies = [a.lower() for a in (user.get("allergies") or [])]
    disliked = [d.lower() for d in (user.get("disliked_meals") or [])]
    preferred_cuisines = [c.lower() for c in (user.get("preferred_cuisines") or [])]
    open_to_cuisines = user.get("open_to_cuisines", True)

    matches = []
    for recipe in res.data:
        required = []
        for ri in recipe.get("recipe_ingredients", []):
            ing = ri.get("ingredients")
            if ing and ing.get("name"):
                required.append(ing["name"].lower())
        if not required:
            continue
        if any(a in required for a in allergies):
            continue
        if any(d in recipe["name"].lower() for d in disliked):
            continue
        recipe_cuisine = (recipe.get("cuisine") or "").lower()
        if preferred_cuisines and not open_to_cuisines:
            if recipe_cuisine not in preferred_cuisines and recipe_cuisine != "kenyan":
                continue
        missing = [i for i in required if i not in pantry_names]
        if len(missing) <= max_missing:
            recipe["missing"] = missing
            recipe["match_score"] = len(required) - len(missing)
            matches.append(recipe)

    matches.sort(key=lambda r: (len(r["missing"]), -r["match_score"]))
    return matches


def find_near_matches(pantry_names: list[str], user: dict, meal_type: str = None) -> list[dict]:
    all_matches = find_matching_recipes(pantry_names, user, meal_type, max_missing=2)
    return [r for r in all_matches if len(r.get("missing", [])) > 0]


def get_saved_recipes(user_id: str) -> list[str]:
    res = (
        supabase.table("saved_recipes")
        .select("recipes(name)")
        .eq("user_id", user_id)
        .execute()
    )
    return [r["recipes"]["name"] for r in res.data if r.get("recipes")]


def save_recipe_by_name(user_id: str, recipe_name: str, user_name: str = "Friend") -> str:
    res = supabase.table("recipes").select("id, name").ilike("name", f"%{recipe_name.strip()}%").execute()
    if not res.data:
        return f"❌ Couldn't find *{recipe_name}*. Try the exact recipe name."
    recipe = res.data[0]
    existing = supabase.table("saved_recipes").select("id").eq("user_id", user_id).eq("recipe_id", recipe["id"]).execute()
    if existing.data:
        return f"⭐ *{recipe['name']}* is already in your saved recipes!"
    supabase.table("saved_recipes").insert({"user_id": user_id, "recipe_id": recipe["id"]}).execute()
    return (
        f"💾 *{recipe['name']}* saved to your favourites, {user_name}!\n\n"
        "Find it anytime by typing *saved*.\n\n"
        "What would you like to do next?\n"
        "🍳 *cook* — get another recipe\n"
        "🛒 *shopping list* — top up your pantry\n"
        "📅 *meal prep* — plan your week"
    )


def log_message(user_id: str, direction: str, body: str):
    try:
        supabase.table("message_logs").insert({
            "user_id": user_id, "direction": direction, "message_text": body, "intent": "",
        }).execute()
    except Exception as e:
        log.warning(f"Could not log: {e}")


def format_recipe(recipe: dict, show_nutrition: bool = True, lang: str = "en") -> str:
    ingredients = [
        ri["ingredients"]["name"]
        for ri in recipe.get("recipe_ingredients", [])
        if ri.get("ingredients") and ri["ingredients"].get("name")
    ]
    cuisine = recipe.get("cuisine", "")
    meal_type = recipe.get("meal_type", "")
    is_ai = recipe.get("is_ai_generated", False)
    display_name = (recipe.get("name_sw") or recipe.get("name", "")) if lang == "sw" else recipe.get("name", "")
    display_desc = (recipe.get("description_sw") or recipe.get("description", "")) if lang == "sw" else recipe.get("description", "")

    tag_parts = []
    if cuisine:
        tag_parts.append(cuisine)
    if meal_type:
        tag_parts.append(meal_type)
    if is_ai:
        tag_parts.append("✨ AI recipe")
    tag = f"_{' • '.join(tag_parts)}_" if tag_parts else ""

    lines = [f"🍽️ *{display_name}*"]
    if tag:
        lines.append(tag)

    timing = []
    if recipe.get("prep_time_minutes"):
        timing.append(f"Prep: {recipe['prep_time_minutes']}min")
    if recipe.get("cook_time_minutes"):
        timing.append(f"Cook: {recipe['cook_time_minutes']}min")
    if recipe.get("servings"):
        timing.append(f"Serves: {recipe['servings']}")
    if recipe.get("difficulty"):
        timing.append(f"{recipe['difficulty'].title()}")
    if timing:
        lines.append(f"⏱ _{' | '.join(timing)}_")

    lines.append("")
    if display_desc:
        lines += [display_desc, ""]

    if ingredients:
        lines.append("🛒 *Ingredients:*")
        lines += [f"  • {i}" for i in ingredients]
        lines.append("")

    steps = recipe.get("instructions") or ""
    if steps:
        lines.append("👨‍🍳 *Steps:*")
        step_list = steps if isinstance(steps, list) else str(steps).split("\n")
        for n, s in enumerate(step_list, 1):
            s = str(s).strip()
            if not s:
                continue
            s = re.sub(r'^Step\s*\d+[\.\:]\s*', '', s, flags=re.IGNORECASE)
            s = re.sub(r'^\d+[\.\:]\s*', '', s)
            if s:
                lines.append(f"  {n}. {s}")
        lines.append("")

    if show_nutrition and recipe.get("calories_per_serving"):
        lines.append("📊 *Nutrition (per serving):*")
        nutrition = []
        if recipe.get("calories_per_serving"):
            nutrition.append(f"🔥 {recipe['calories_per_serving']} cal")
        if recipe.get("protein_g"):
            nutrition.append(f"💪 {recipe['protein_g']}g protein")
        if recipe.get("carbs_g"):
            nutrition.append(f"🌾 {recipe['carbs_g']}g carbs")
        if recipe.get("fat_g"):
            nutrition.append(f"🥑 {recipe['fat_g']}g fat")
        lines.append("  " + "  |  ".join(nutrition))
        lines.append("")

    lines += [f"💾 _save {recipe['name']}_ to save this"]
    lines += ["🔄 Reply *cook* for another suggestion"]
    return "\n".join(lines)


def format_near_match(recipe: dict, lang: str = "en") -> str:
    missing = recipe.get("missing", [])
    total = recipe.get("match_score", 0) + len(missing)
    return t("near_match_card", lang,
        name=recipe["name"],
        cuisine=recipe.get("cuisine", ""),
        meal_type=recipe.get("meal_type", ""),
        have=recipe["match_score"],
        total=total,
        missing=", ".join(missing)
    )


def format_recipe_with_followup(recipe: dict, user_id: str, missing: list = None, lang: str = "en") -> str:
    lines = []
    if missing:
        lines.append(f"⚠️ *Your pantry is missing:* {', '.join(missing)}")
        lines.append("_You can still try the recipe or grab these on your next shop!_")
        lines.append("")
    lines.append(format_recipe(recipe, lang=lang))
    update_user(user_id, {
        "last_suggested_recipe_id": str(recipe["id"]),
        "last_suggested_recipe_name": recipe["name"],
        "awaiting_cooking_confirmation": True,
    })
    lines.append("")
    lines.append(cooking_followup(recipe["name"], lang))
    return "\n".join(lines)


def generate_meal_plan(user: dict, pantry_names: list[str]) -> str:
    meal_types = ["breakfast", "lunch", "dinner"]
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    lines = ["📅 *Your Weekly Meal Plan*", ""]
    for day in days:
        lines.append(f"*{day}*")
        for mt in meal_types:
            matches = find_matching_recipes(pantry_names, user, meal_type=mt)
            if matches:
                recipe = random.choice(matches)
                emoji = "🌅" if mt == "breakfast" else "☀️" if mt == "lunch" else "🌙"
                lines.append(f"  {emoji} {recipe['name']}")
            else:
                lines.append(f"  _(no {mt} match)_")
        lines.append("")
    lines.append("💡 Add more ingredients to unlock more recipes!")
    return "\n".join(lines)


# ── AI Recipe Generation ───────────────────────────────────────────────────────

def generate_ai_recipe(pantry_names: list[str], user: dict, meal_type: str = None) -> dict | None:
    if not ANTHROPIC_API_KEY:
        return None

    lang = user.get("language", "en")
    allergies = ", ".join(user.get("allergies") or []) or "none"
    disliked = ", ".join(user.get("disliked_meals") or []) or "none"
    budget = user.get("budget", "medium")
    meal_label = meal_type or "any meal"
    pantry_str = ", ".join(pantry_names)

    prompt = f"""You are a professional Kenyan chef and nutritionist.

Create a delicious {meal_label} recipe using ONLY these available ingredients: {pantry_str}

User preferences:
- Allergies/restrictions: {allergies}
- Dislikes: {disliked}
- Budget: {budget}
- Language: {"Kiswahili" if lang == "sw" else "English"}

Requirements:
- Use primarily Kenyan cooking styles and flavours
- Must be practical and realistic to cook at home
- Include accurate nutrition estimates
- Keep instructions clear and simple

Respond ONLY with valid JSON (no markdown):
{{
  "name": "Recipe Name",
  "description": "One sentence description",
  "instructions": "Step 1.\\nStep 2.\\nStep 3.",
  "prep_time_minutes": 10,
  "cook_time_minutes": 20,
  "servings": 4,
  "difficulty": "easy",
  "meal_type": "{meal_type or "dinner"}",
  "cuisine": "Kenyan",
  "calories_per_serving": 350,
  "protein_g": 25.0,
  "carbs_g": 40.0,
  "fat_g": 12.0,
  "ingredients_used": ["ingredient1", "ingredient2"]
}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        text = resp.json()["content"][0]["text"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        data = json.loads(text)
        log.info(f"🤖 AI generated recipe: {data.get('name')}")

        insert = supabase.table("recipes").insert({
            "name": data["name"],
            "description": data.get("description", ""),
            "instructions": data.get("instructions", ""),
            "prep_time_minutes": data.get("prep_time_minutes"),
            "cook_time_minutes": data.get("cook_time_minutes"),
            "servings": data.get("servings", 4),
            "difficulty": data.get("difficulty", "easy"),
            "meal_type": data.get("meal_type", meal_type or "dinner"),
            "cuisine": data.get("cuisine", "Kenyan"),
            "calories_per_serving": data.get("calories_per_serving"),
            "protein_g": data.get("protein_g"),
            "carbs_g": data.get("carbs_g"),
            "fat_g": data.get("fat_g"),
            "is_ai_generated": True,
            "is_approved": True,
        }).execute()

        if not insert.data:
            return None

        recipe = insert.data[0]

        for ing_name in data.get("ingredients_used", []):
            ing = find_ingredient_by_name(ing_name)
            if ing:
                try:
                    supabase.table("recipe_ingredients").insert({
                        "recipe_id": recipe["id"],
                        "ingredient_id": ing["id"],
                    }).execute()
                except Exception:
                    pass

        full = supabase.table("recipes").select(
            "id, name, description, instructions, cuisine, meal_type, "
            "prep_time_minutes, cook_time_minutes, servings, difficulty, "
            "calories_per_serving, protein_g, carbs_g, fat_g, is_ai_generated, "
            "recipe_ingredients(ingredients(name))"
        ).eq("id", recipe["id"]).execute()

        return full.data[0] if full.data else None

    except Exception as e:
        err = str(e).lower()
        if "credit" in err or "billing" in err or "balance" in err:
            log.warning("Anthropic credits exhausted")
        else:
            log.warning(f"AI recipe generation failed: {e}")
        return None


# ── Shopping List ──────────────────────────────────────────────────────────────

def get_shopping_list(user_id: str) -> dict | None:
    res = supabase.table("shopping_lists").select("*").eq("user_id", user_id).eq("is_complete", False).order("created_at", desc=True).limit(1).execute()
    return res.data[0] if res.data else None


def create_shopping_list(user_id: str, items: list[str], name: str = "Shopping List") -> dict:
    res = supabase.table("shopping_lists").insert({
        "user_id": user_id,
        "name": name,
        "items": json.dumps(items),
    }).execute()
    return res.data[0] if res.data else {}


def format_shopping_list(items: list[str], name: str = "Shopping List", lang: str = "en") -> str:
    lines = [t("shopping_list_header", lang, name=name, count=len(items))]
    lines += [f"  ☐ {item}" for item in items]
    lines += ["", t("shopping_list_footer", lang)]
    return "\n".join(lines)


def shopping_list_for_recipe(recipe_name: str, user_id: str, pantry_names: list[str]) -> str:
    res = supabase.table("recipes").select(
        "id, name, recipe_ingredients(ingredients(name))"
    ).ilike("name", f"%{recipe_name.strip()}%").execute()

    if not res.data:
        return f"❌ Couldn't find *{recipe_name}*. Try the exact recipe name."

    recipe = res.data[0]
    all_ingredients = [
        ri["ingredients"]["name"]
        for ri in recipe.get("recipe_ingredients", [])
        if ri.get("ingredients") and ri["ingredients"].get("name")
    ]
    need_to_buy = [i for i in all_ingredients if i.lower() not in pantry_names]

    if not need_to_buy:
        return f"🎉 You already have everything for *{recipe['name']}*!\n\nReply *cook* to get the recipe."

    create_shopping_list(user_id, need_to_buy, f"For {recipe['name']}")
    return format_shopping_list(need_to_buy, f"For {recipe['name']}")


# ── Nutrition Summary ──────────────────────────────────────────────────────────

def get_nutrition_summary(user_id: str, lang: str = "en") -> str:
    res = (
        supabase.table("user_recipe_suggestions")
        .select("recipes(name, calories_per_serving, protein_g, carbs_g, fat_g)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(7)
        .execute()
    )

    recipes = [r["recipes"] for r in res.data if r.get("recipes") and r["recipes"].get("calories_per_serving")]
    if not recipes:
        return t("nutrition_empty", lang)

    total_cal = sum(r["calories_per_serving"] for r in recipes if r.get("calories_per_serving"))
    total_protein = sum(float(r["protein_g"] or 0) for r in recipes)
    total_carbs = sum(float(r["carbs_g"] or 0) for r in recipes)
    total_fat = sum(float(r["fat_g"] or 0) for r in recipes)
    count = len(recipes)

    lines = [
        "📊 *Your Nutrition Summary*",
        f"_Based on your last {count} meals_", "",
        f"🔥 Avg calories: *{total_cal // count} cal/meal*",
        f"💪 Total protein: *{total_protein:.0f}g*",
        f"🌾 Total carbs: *{total_carbs:.0f}g*",
        f"🥑 Total fat: *{total_fat:.0f}g*", "",
        "Recent meals:",
    ]
    lines += [f"  • {r['name']}" for r in recipes[:5]]
    return "\n".join(lines)


# ── Abuse filter ──────────────────────────────────────────────────────────────

def is_abusive(message: str) -> bool:
    """Check message against local blocklist and DB blocked phrases."""
    m = message.lower()
    if any(word in m for word in LOCAL_BLOCKED):
        return True
    try:
        res = supabase.table("blocked_phrases").select("phrase").execute()
        db_phrases = [r["phrase"].lower() for r in res.data]
        if any(phrase in m for phrase in db_phrases):
            return True
    except Exception as e:
        log.warning(f"Blocked phrases DB check failed: {e}")
    return False


def block_user(user_id: str, reason: str = "abusive behaviour"):
    update_user(user_id, {"is_blocked": True, "block_reason": reason})
    log.warning(f"🚫 User {user_id} blocked: {reason}")


# ── Usage tracking ─────────────────────────────────────────────────────────────

def get_daily_usage(user_id: str) -> dict:
    from datetime import date
    today = date.today().isoformat()
    try:
        res = supabase.table("daily_usage").select("*").eq("user_id", user_id).eq("usage_date", today).execute()
        if res.data:
            return res.data[0]
        insert = supabase.table("daily_usage").insert({
            "user_id": user_id,
            "usage_date": today,
            "recipe_suggestions": 0,
            "photo_scans": 0,
            "ai_chef_chats": 0,
        }).execute()
        return insert.data[0] if insert.data else {}
    except Exception as e:
        log.warning(f"Daily usage fetch failed: {e}")
        return {}


def increment_usage(user_id: str, feature: str):
    from datetime import date
    today = date.today().isoformat()
    try:
        usage = get_daily_usage(user_id)
        current = usage.get(feature, 0)
        supabase.table("daily_usage").update({
            feature: current + 1,
        }).eq("user_id", user_id).eq("usage_date", today).execute()
    except Exception as e:
        log.warning(f"Usage increment failed: {e}")


def check_limit(user: dict, feature: str) -> tuple:
    """Returns (allowed: bool, message: str)"""
    from datetime import datetime
    user_id = user["id"]
    lang = user.get("language", "en")
    sw = lang == "sw"

    tier = user.get("tier", "free")
    premium_expires = user.get("premium_expires_at")
    if tier == "premium" and premium_expires:
        try:
            expires = datetime.fromisoformat(premium_expires.replace("Z", "+00:00"))
            if datetime.now(expires.tzinfo) > expires:
                update_user(user_id, {"tier": "free", "premium_expires_at": None})
                tier = "free"
        except Exception:
            pass

    limit = TIER_LIMITS.get(tier, TIER_LIMITS["free"]).get(feature, 999)
    usage = get_daily_usage(user_id)
    current = usage.get(feature, 0)

    if current >= limit:
        if sw:
            msg = (
                f"⚠️ Umefika kikomo chako cha leo cha *{limit}* kwa kipengele hiki.\n\n"
                f"⭐ *Pata Premium kwa {PREMIUM_PRICE}* na upate:\n"
                "• Mapendekezo ya mapishi yasio na kikomo\n"
                "• Uchanganuzi wa picha bila kikomo\n"
                "• Mazungumzo na mpishi bila kikomo\n"
                "• Mpango wa wiki + Muhtasari wa lishe\n\n"
                "Andika *premium* kujua zaidi! 🚀"
            )
        else:
            msg = (
                f"⚠️ You've reached your daily limit of *{limit}* for this feature.\n\n"
                f"⭐ *Upgrade to Premium for {PREMIUM_PRICE}* and get:\n"
                "• Unlimited recipe suggestions\n"
                "• Unlimited photo scanning\n"
                "• Unlimited Chat with Chef\n"
                "• Weekly meal plans + Nutrition summaries\n\n"
                "Reply *premium* to upgrade! 🚀"
            )
        return False, msg
    return True, ""


def is_premium(user: dict) -> bool:
    from datetime import datetime
    if user.get("tier") != "premium":
        return False
    expires = user.get("premium_expires_at")
    if not expires:
        return False
    try:
        exp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        return datetime.now(exp.tzinfo) <= exp
    except Exception:
        return False


def upgrade_info(lang: str = "en") -> str:
    if lang == "sw":
        return (
            "⭐ *Tunapika Premium*\n\n"
            f"*{PREMIUM_PRICE}* — Ghaghawa ya M-Pesa\n\n"
            "Unapata nini:\n"
            "✅ Mapendekezo ya mapishi yasio na kikomo\n"
            "✅ Uchanganuzi wa picha bila kikomo\n"
            "✅ Mazungumzo na mpishi bila kikomo\n"
            "✅ Mpango wa chakula wa wiki\n"
            "✅ Muhtasari wa lishe\n"
            "✅ Pantry ya familia (hivi karibuni)\n\n"
            "💳 Malipo ya M-Pesa yanaendelea — hivi karibuni!\n"
            "Kwa sasa wasiliana nasi moja kwa moja kukuwezesha."
        )
    return (
        "⭐ *Tunapika Premium*\n\n"
        f"*{PREMIUM_PRICE}* — paid via M-Pesa\n\n"
        "What you get:\n"
        "✅ Unlimited recipe suggestions\n"
        "✅ Unlimited photo scanning\n"
        "✅ Unlimited Chat with Chef\n"
        "✅ Weekly meal plans\n"
        "✅ Nutrition summaries\n"
        "✅ Family shared pantry (coming soon)\n\n"
        "💳 M-Pesa payments coming soon!\n"
        "For now contact us directly to get enabled."
    )


# ── Onboarding ─────────────────────────────────────────────────────────────────

def handle_onboarding(user: dict, msg: str) -> tuple[str, bool]:
    step = user.get("onboarding_step", 0)
    user_id = user["id"]
    lang = user.get("language", "en")
    sw = lang == "sw"

    if step == 0:
        update_user(user_id, {"onboarding_step": 1})
        return (
            "👋 Welcome to *Tunapika*! 🍳\n"
            "I help you cook great meals from what you already have.\n\n"
            "First, choose your preferred language:\n\n"
            "1️⃣  🇬🇧 *English*\n"
            "2️⃣  🇰🇪 *Kiswahili*\n\n"
            "Reply *1* or *2*", False
        )

    if step == 1:
        lang = "sw" if msg.strip() in ("2", "kiswahili", "swahili") else "en"
        update_user(user_id, {"language": lang, "onboarding_step": 2})
        if lang == "sw":
            return (
                "Sawa! 🇰🇪\n\n"
                "👋 Habari! Mimi ni *Tunapika* — msaidizi wako wa kupika! 🍳\n\n"
                "Nitakusaidia kuamua unapike nini kulingana na vitu vilivyo kwenye "
                "jokofu na pantry yako.\n\n"
                "Nikuite jina gani? 😊", False
            )
        return (
            "Great! 🇬🇧\n\n"
            "👋 Hi! *Tunapika* here — your personal AI kitchen assistant! 🍳\n\n"
            "I'll help you decide what to cook based on what's in your fridge and pantry.\n\n"
            "What shall I call you? 😊", False
        )

    if step == 2:
        name = msg.strip().title()
        lang = user.get("language", "en")
        sw = lang == "sw"
        update_user(user_id, {"full_name": name, "onboarding_step": 3})
        if sw:
            return (
                f"Karibu, *{name}*! 😊\n\n"
                "Kabla hatujaanza, tafadhali soma kanusho hili:\n\n"
                "⚠️ *Kanusho*\n"
                "_Tunapika ni msaidizi wa AI na si badala ya ushauri wa kitaalamu "
                "wa lishe au matibabu. Daima shauriana na daktari au mtaalamu wa "
                "lishe kwa mahitaji maalum ya kiafya._\n\n"
                "Je, unakubali Masharti na Vigezo vyetu?\n"
                "📄 _https://claude.ai/artifact/BQxxnyoAVXfEM28xbFL18o_\n\n"
                "1️⃣ Ndiyo, nakubali ✅\n"
                "2️⃣ Hapana, toka ❌", False
            )
        return (
            f"Nice to meet you, *{name}*! 😊\n\n"
            "Before we get started, please read our disclaimer:\n\n"
            "⚠️ *Disclaimer*\n"
            "_Tunapika is an AI assistant and is not a substitute for professional "
            "nutritional or medical advice. Always consult a qualified nutritionist "
            "or doctor for health-specific dietary needs._\n\n"
            "Do you accept our Terms & Conditions?\n"
            "📄 _https://claude.ai/artifact/BQxxnyoAVXfEM28xbFL18o_\n\n"
            "1️⃣ Yes, I accept ✅\n"
            "2️⃣ No, exit ❌", False
        )

    if step == 3:
        lang = user.get("language", "en")
        sw = lang == "sw"
        m_lower = msg.strip().lower()
        if m_lower in ("1", "yes", "i accept", "ndiyo", "nakubali", "yes i accept", "accept"):
            update_user(user_id, {"onboarding_step": 4})
            if sw:
                return ("✅ Asante! Sasa tuanze.\n\nUna *mzio wowote wa chakula?*\n\nMf. _karanga, maziwa, gluteni, nguruwe_\nAu andika *hapana*.", False)
            return ("✅ Great, let's get started!\n\nDo you have any *food allergies or dietary restrictions?*\n\ne.g. _nuts, dairy, gluten, pork_\nOr type *none*.", False)
        else:
            if sw:
                return ("Sawa, hakuna shida! Rudi wakati wowote uko tayari. Kwa heri! 👋", False)
            return ("No problem! Come back whenever you're ready. Goodbye! 👋", False)

    if step == 4:
        allergies = [] if msg.strip().lower() in ("none", "hapana") else [a.strip() for a in msg.replace(",", " ").split() if a.strip()]
        update_user(user_id, {"allergies": allergies, "onboarding_step": 5})
        ack = ("Mzio wako umeandikwa! ✅" if allergies else "Sawa, huna mzio! ✅") if sw else ("Noted your allergies! ✅" if allergies else "Great, no allergies! ✅")
        if sw:
            return (f"{ack}\n\nUnapenda *vyakula au milo gani?* 🥰\n\nMf. _pilau, kuku, pasta, ugali_\nAu andika *ruka*.", False)
        return (f"{ack}\n\nWhat are some *meals or foods you love?* 🥰\n\ne.g. _pilau, chicken, pasta, ugali_\nOr type *skip*.", False)

    if step == 5:
        liked = [] if msg.strip().lower() in ("skip", "ruka") else [a.strip() for a in msg.replace(",", " ").split() if a.strip()]
        update_user(user_id, {"liked_meals": liked, "onboarding_step": 6})
        if sw:
            return ("Vizuri! 😄\n\nKuna *vyakula unavyoepuka?*\n\nMf. _samaki, ini_\nAu andika *hapana*.", False)
        return ("Yum! Great taste 😄\n\nAny *foods or meals you dislike or avoid?*\n\ne.g. _fish, liver_\nOr type *none*.", False)

    if step == 6:
        disliked = [] if msg.strip().lower() in ("none", "hapana") else [a.strip() for a in msg.replace(",", " ").split() if a.strip()]
        update_user(user_id, {"disliked_meals": disliked, "onboarding_step": 7})
        if sw:
            return ("Sawa! 🙅\n\n*Bajeti yako ya chakula kwa wiki?*\n\n1️⃣ *chini* — Chini ya Ksh 1,000\n2️⃣ *kati* — Ksh 1,000–3,000\n3️⃣ *juu* — Ksh 3,000+\n\nJibu *chini*, *kati*, au *juu*.", False)
        return ("Noted! 🙅\n\n*What's your weekly food budget?*\n\n1️⃣ *low* — Under Ksh 1,000\n2️⃣ *medium* — Ksh 1,000–3,000\n3️⃣ *high* — Ksh 3,000+\n\nReply *low*, *medium*, or *high*.", False)

    if step == 7:
        budget = msg.strip().lower()
        if budget not in ("low", "medium", "high"):
            return ("Please reply with *low*, *medium*, or *high* 😊", False)
        update_user(user_id, {"budget": budget, "onboarding_step": 8})
        cuisine_list = "\n".join([f"{i+1}️⃣ {c}" for i, c in enumerate(CUISINES)])
        return (
            "Got it! 💰\n\n"
            "Would you like to *explore other cuisines* beyond Kenyan food? 🌍\n\n"
            f"{cuisine_list}\n\n"
            "Reply with the *numbers* of cuisines you'd like (e.g. _1, 3_)\n"
            "Or type *no* to stick to Kenyan food only.", False
        )

    if step == 8:
        m = msg.strip().lower()
        if m == "no":
            update_user(user_id, {"open_to_cuisines": False, "preferred_cuisines": ["Kenyan"], "onboarding_step": 9})
        else:
            selected = []
            for part in m.replace(",", " ").split():
                try:
                    idx = int(part.strip()) - 1
                    if 0 <= idx < len(CUISINES):
                        selected.append(CUISINES[idx])
                except ValueError:
                    for c in CUISINES:
                        if part in c.lower():
                            selected.append(c)
            if not selected:
                selected = ["Kenyan"]
            update_user(user_id, {"open_to_cuisines": True, "preferred_cuisines": selected, "onboarding_step": 9})
        return (
            "Awesome! 🌍\n\n"
            "How do you prefer to cook?\n\n"
            "1️⃣ *daily* — I cook fresh every day\n"
            "2️⃣ *meal prep* — I prep meals once a week\n\n"
            "Reply *daily* or *meal prep*.", False
        )

    if step == 9:
        m = msg.strip().lower()
        style = "meal_prep" if "meal" in m or "prep" in m or m == "2" else "daily"
        update_user(user_id, {"cooking_style": style, "onboarding_step": 10})
        if sw:
            return ("Vizuri! 🍳\n\nUna watu wangapi nyumbani wanaokula pamoja?\n\ne.g. _1, 2, 4_\nAu andika *ruka*.", False)
        return ("Got it! 🍳\n\nHow many people do you cook for at home?\n\ne.g. _1, 2, 4_\nOr type *skip*.", False)

    if step == 10:
        m = msg.strip().lower()
        household_size = None
        if m not in ("skip", "ruka"):
            try:
                household_size = int(m.split()[0])
            except Exception:
                pass
        update_user(user_id, {"household_size": household_size, "onboarding_step": 11})
        if sw:
            return ("Sawa! 👨‍👩‍👧\n\nUnaishi wapi? (Mji au kaunti)\n\nMf. _Nairobi, Mombasa, Kisumu_\nAu andika *ruka*.", False)
        return ("Got it! 👨‍👩‍👧\n\nWhich city or region are you in?\n\ne.g. _Nairobi, Mombasa, Kisumu, London_\nOr type *skip*.", False)

    if step == 11:
        m = msg.strip()
        region = None if m.lower() in ("skip", "ruka") else m.title()
        update_user(user_id, {"region": region, "onboarding_step": 12})
        if sw:
            return ("📍 Sawa!\n\nUna ujuzi gani wa kupika?\n\n1️⃣ *Mwanzo* — Ninajifunza\n2️⃣ *Kati* — Najua mambo ya msingi\n3️⃣ *Uzoefu* — Napika vizuri\n\nJibu *1*, *2* au *3*.", False)
        return ("📍 Got it!\n\nHow would you rate your cooking skills?\n\n1️⃣ *Beginner* — Still learning\n2️⃣ *Intermediate* — Know the basics\n3️⃣ *Advanced* — Confident cook\n\nReply *1*, *2* or *3*.", False)

    if step == 12:
        m = msg.strip().lower()
        skill_map = {"1": "beginner", "beginner": "beginner", "mwanzo": "beginner",
                     "2": "intermediate", "intermediate": "intermediate", "kati": "intermediate",
                     "3": "advanced", "advanced": "advanced", "uzoefu": "advanced"}
        skill = skill_map.get(m, "intermediate")
        update_user(user_id, {"cooking_skill": skill, "onboarding_step": 13})
        if sw:
            return ("👨‍🍳 Vizuri!\n\nUnapenda chakula chenye kiwango gani cha utiaji?\n\n1️⃣ *Kidogo* — Sipendi pilipili\n2️⃣ *Wastani* — Kidogo kidogo\n3️⃣ *Ukali* — Napenda moto\n4️⃣ *Ukali sana* — Kadri iwezekanavyo!\n\nJibu *1*–*4*.", False)
        return ("👨‍🍳 Great!\n\nHow much spice do you like in your food?\n\n1️⃣ *Mild* — No heat please\n2️⃣ *Medium* — A little warmth\n3️⃣ *Hot* — I like it spicy\n4️⃣ *Very hot* — The hotter the better!\n\nReply *1*–*4*.", False)

    if step == 13:
        m = msg.strip().lower()
        spice_map = {"1": "mild", "mild": "mild", "kidogo": "mild",
                     "2": "medium", "medium": "medium", "wastani": "medium",
                     "3": "hot", "hot": "hot", "ukali": "hot",
                     "4": "very hot", "very hot": "very hot", "ukali sana": "very hot"}
        spice = spice_map.get(m, "medium")
        update_user(user_id, {"spice_tolerance": spice, "onboarding_step": 14})
        if sw:
            return ("🌶️ Sawa!\n\nUna malengo gani ya kiafya? (Chagua moja au zaidi)\n\n1️⃣ Kupunguza uzito\n2️⃣ Kuongeza misuli\n3️⃣ Chakula bora na uwiano\n4️⃣ Udhibiti wa ugonjwa (kisukari, shinikizo la damu n.k)\n5️⃣ Hakuna — Napenda tu kula vizuri\n\nJibu kwa nambari e.g. _1, 3_ au *ruka*.", False)
        return ("🌶️ Perfect!\n\nDo you have any health goals? (Choose one or more)\n\n1️⃣ Weight loss\n2️⃣ Muscle gain\n3️⃣ Balanced / healthy eating\n4️⃣ Managing a condition (diabetes, hypertension etc.)\n5️⃣ None — I just want to eat well\n\nReply with numbers e.g. _1, 3_ or *skip*.", False)

    if step == 14:
        m = msg.strip().lower()
        goal_map = {
            "1": "weight_loss", "2": "muscle_gain", "3": "balanced",
            "4": "medical", "5": "none",
            "weight loss": "weight_loss", "muscle gain": "muscle_gain",
            "balanced": "balanced", "medical": "medical", "none": "none",
            "kupunguza uzito": "weight_loss", "kuongeza misuli": "muscle_gain",
            "chakula bora": "balanced", "ugonjwa": "medical", "hakuna": "none",
        }
        health_goals = []
        if m not in ("skip", "ruka", "5", "none", "hakuna"):
            for part in m.replace(",", " ").split():
                g = goal_map.get(part.strip())
                if g and g != "none":
                    health_goals.append(g)
        update_user(user_id, {"health_goals": health_goals or [], "onboarding_step": 15})
        if sw:
            return ("💪 Vizuri!\n\nSwali la mwisho kabisa — na ni la hiari:\n\nMshahara wako huja lini kwa kawaida? Hii inakusaidia kupata mapendekezo ya chakula cha bei nafuu mwishoni mwa mwezi.\n\ne.g. _25_ au _1_\nAu andika *ruka* — sawa kabisa!", False)
        return ("💪 Almost done!\n\nOne last question — completely optional:\n\nWhat day of the month does your salary usually arrive? This helps me suggest budget-friendly meals when funds are low.\n\ne.g. _25_ or _1_\nOr type *skip* — totally fine!", False)

    if step == 15:
        m = msg.strip().lower()
        payday = None
        if m not in ("skip", "ruka"):
            try:
                payday = int(m.split()[0])
                if not 1 <= payday <= 31:
                    payday = None
            except Exception:
                pass
        name = user.get("full_name", "Friend")
        style = user.get("cooking_style", "daily")
        update_user(user_id, {
            "payday": payday,
            "onboarding_complete": True,
            "onboarding_step": 16,
            "awaiting_meal_type": False,
            "awaiting_pantry_action": False,
            "awaiting_profile_action": False,
            "awaiting_cooking_confirmation": False,
            "pending_recipe_options": None,
        })
        style_msg = "I'll suggest weekly meal plans for you! 📅" if style == "meal_prep" else "I'll suggest fresh daily recipes! 🍳"
        if sw:
            return (
                f"🎉 Umeweka vizuri kabisa, *{name}*!\n\n"
                f"{style_msg}\n\n"
                "Hatua ya mwisho — niambie una nini nyumbani sasa hivi:\n\n"
                "💬 _\"Nina mayai, nyanya, mchele na kuku\"_\n"
                "💬 _\"Nimenunua unga, vitunguu na nyama\"_\n\n"
                "📸 Au piga picha ya friji au risiti yako!\n\n"
                "_Andika *ruka* kama unataka kufanya hivi baadaye_", True
            )
        return (
            f"🎉 You're all set, *{name}*!\n\n"
            f"{style_msg}\n\n"
            "One last step — tell me what you have at home right now:\n\n"
            "💬 _\"I have eggs, tomatoes, rice and chicken\"_\n"
            "💬 _\"I bought flour, onions and minced beef\"_\n\n"
            "📸 Or send a photo of your fridge or receipt!\n\n"
            "_Type *skip* to do this later_", True
        )

    return (HELP_MSG, True)


# ── Conversation helpers ──────────────────────────────────────────────────────

def conversation_closer(name: str, lang: str = "en", prefix: str = "") -> str:
    """Warm conversation ender — shown after rating or 'not yet'."""
    prefix_block = (prefix + "\n\n") if prefix else ""
    if lang == "sw":
        closer = (
            f"{prefix_block}"
            f"Hiyo inatosha kwa sasa, *{name}*! 🍳\n"
            "Rudi unapohisi njaa tena 😊\n\n"
            "_Andika *hi* wakati wowote_ 👋"
        )
    else:
        closer = (
            f"{prefix_block}"
            f"That's it from me for now, *{name}*! 🍳\n"
            "Come back when you're hungry again 😊\n\n"
            "_Reply *hi* anytime_ 👋"
        )
    return closer


def handle_reentry(user: dict, msg: str) -> str:
    """
    Smart re-entry handler — detects where the user left off and
    resumes or greets accordingly.
    """
    name = user.get("full_name", "Friend")
    lang = user.get("language", "en")
    sw = lang == "sw"

    # ── Mid-onboarding: dropped off during setup ──────────────────────────────
    if not user.get("onboarding_complete"):
        step = user.get("onboarding_step", 0)
        if step > 0:
            if sw:
                return (
                    f"Karibu tena, *{name}*! 👋\n\n"
                    f"Tulikuwa tukiendelea na usanidi wako (hatua {step}/15).\n"
                    "Tuendelee? Jibu chochote kuendelea."
                )
            return (
                f"Welcome back, *{name}*! 👋\n\n"
                f"We were in the middle of your setup (step {step}/15).\n"
                "Just reply anything to continue where you left off."
            )
        # Brand new — let onboarding handle it
        return None  # caller falls through to normal onboarding

    # ── Mid-cooking flow: was shown recipes but never picked one ─────────────
    pending_options = user.get("pending_recipe_options")
    if pending_options:
        try:
            import json as _json
            option_ids = _json.loads(pending_options)
            if option_ids:
                if sw:
                    return (
                        f"Karibu tena, *{name}*! 👋\n\n"
                        "Inaonekana uliondoka ukiwa na mapishi fulani uliyopewa — \n"
                        "bado unaweza kuchagua nambari, au andika *cook* kuanza upya."
                    )
                return (
                    f"Welcome back, *{name}*! 👋\n\n"
                    "Looks like you left with some recipe options on the table — \n"
                    "you can still pick a number, or type *cook* to start fresh."
                )
        except Exception:
            pass

    # ── Mid-confirmation: got a recipe, never confirmed if they cooked it ─────
    if user.get("awaiting_cooking_confirmation"):
        recipe_name = user.get("last_suggested_recipe_name", "your recipe")
        if sw:
            return (
                f"Karibu tena, *{name}*! 👋\n\n"
                f"Je, ulimaliza kupika *{recipe_name}*?\n\n"
                "1️⃣  ✅ *Ndiyo, nilipika*\n"
                "2️⃣  🥕 *Nilitumia baadhi ya viungo*\n"
                "3️⃣  ❌ *Bado sijakipika*"
            )
        return (
            f"Welcome back, *{name}*! 👋\n\n"
            f"Did you ever get around to cooking *{recipe_name}*?\n\n"
            "1️⃣  ✅ *Yes, I cooked it*\n"
            "2️⃣  🥕 *Used some ingredients*\n"
            "3️⃣  ❌ *Never got around to it*"
        )

    # ── Normal return: completed user, no pending state ───────────────────────
    last_recipe = user.get("last_suggested_recipe_name")

    # Time-aware greeting
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone("Africa/Nairobi")
        hour = datetime.now(tz).hour
    except Exception:
        hour = 12  # fallback if pytz not available

    if hour < 12:
        time_emoji = "🌅"
        time_greeting = "Habari za asubuhi" if sw else "Good morning"
    elif hour < 17:
        time_emoji = "☀️"
        time_greeting = "Habari za mchana" if sw else "Good afternoon"
    else:
        time_emoji = "🌙"
        time_greeting = "Habari za jioni" if sw else "Good evening"

    if last_recipe and sw:
        opener = (
            f"{time_emoji} {time_greeting}, *{name}*! 👋\n\n"
            f"Mara ya mwisho ulikuwa ukipika *{last_recipe}* — "
            f"ilikuwa ladha? 😄\n\n"
        )
    elif last_recipe:
        opener = (
            f"{time_emoji} {time_greeting}, *{name}*! 👋\n\n"
            f"Last time you were making *{last_recipe}* — "
            f"hope it turned out great! 😄\n\n"
        )
    else:
        if sw:
            opener = f"{time_emoji} {time_greeting}, *{name}*! 👋\n\nTunapika nini leo?\n\n"
        else:
            opener = f"{time_emoji} {time_greeting}, *{name}*! 👋\n\nWhat are we cooking today?\n\n"

    return opener + main_menu(name, lang)


# ── Intent router ──────────────────────────────────────────────────────────────

RECIPE_KEYWORDS = ["cook", "recipe", "hungry", "what are we", "breakfast", "lunch",
                   "dinner", "meal prep", "weekly plan", "supper",
                   "morning", "evening", "brunch", "snack"]
EXPLICIT_COMMANDS = ["help", "menu", "start", "hi", "hello", "hey", "pantry",
                     "ingredients", "saved", "favourites", "favorites", "profile",
                     "my profile", "settings", "edit profile", "update profile"]


def looks_like_pantry_message(msg: str) -> bool:
    m = msg.lower()
    pantry_signals = [
        "i have", "i've got", "i got", "i bought", "just bought", "just got",
        "we have", "at home", "in my fridge", "in the fridge", "in my kitchen",
        "i picked up", "picked up some", "i found", "there's some", "got some",
        "went shopping", "from the shop", "from the market", "nimenunua", "niko na",
        "nimepata", "niko nazo", "kuna", "nimebuy",
        "i used", "i finished", "ran out", "used up", "no more", "finished the",
        "i don't have", "i do not have", "out of", "imekwisha", "nimemaliza",
        "hakuna", "nimetumia", "imeisha",
    ]
    return any(signal in m for signal in pantry_signals)


def route(msg: str, user: dict) -> str:
    user_id = user["id"]
    m = msg.strip().lower()
    name = user.get("full_name", "Friend")
    lang = user.get("language", "en")
    meal_type = None

    COOK_CONFIRM_TRIGGERS = {"yes, i cooked it", "yes i cooked it", "yes", "1", "cooked", "i cooked it", "ndiyo", "nimepika"}
    COOK_DENY_TRIGGERS = {"no", "not yet", "3", "hapana", "bado"}
    COOK_SOME_TRIGGERS = {"used some", "used some ingredients", "2", "some", "baadhi"}

    # COOKING CONFIRMATION
    if user.get("awaiting_cooking_confirmation"):
        recipe_name = user.get("last_suggested_recipe_name", "that recipe")
        recipe_id = user.get("last_suggested_recipe_id")
        if m in COOK_CONFIRM_TRIGGERS:
            update_user(user_id, {"awaiting_cooking_confirmation": False})
            if recipe_id:
                res = supabase.table("recipe_ingredients").select("ingredients(name)").eq("recipe_id", recipe_id).execute()
                ing_names = [r["ingredients"]["name"] for r in res.data if r.get("ingredients")]
                removed, _ = remove_ingredients(user_id, ing_names)
                update_user(user_id, {
                    "awaiting_rating_recipe_id": str(recipe_id),
                    "awaiting_rating_recipe_name": recipe_name,
                })
                lines = [f"✅ Great cook, {name}! Removed from your pantry:"]
                lines += [f"  • {i}" for i in removed]
                lines += [
                    "",
                    f"⭐ How was *{recipe_name}*? Rate it:",
                    "1️⃣ ⭐ Didn't like it",
                    "2️⃣ ⭐⭐ It was okay",
                    "3️⃣ ⭐⭐⭐ Pretty good!",
                    "4️⃣ ⭐⭐⭐⭐ Really enjoyed it",
                    "5️⃣ ⭐⭐⭐⭐⭐ Absolutely loved it!",
                    "",
                    "_Or reply *skip* to skip the rating_"
                ]
                return "\n".join(lines)
        elif m in COOK_SOME_TRIGGERS:
            update_user(user_id, {"awaiting_cooking_confirmation": False})
            return t("used_some", lang)
        elif m in COOK_DENY_TRIGGERS:
            update_user(user_id, {"awaiting_cooking_confirmation": False})
            return conversation_closer(name, lang, prefix="👍 No problem! Your pantry is saved.")
        else:
            return (
                f"Did you end up cooking *{recipe_name}*? 👨‍🍳\n\n"
                "1️⃣  ✅ *Yes, I cooked it* — remove ingredients from pantry\n"
                "2️⃣  🥕 *Used some ingredients* — tell me which ones\n"
                "3️⃣  ❌ *Not yet* — keep pantry as is"
            )

    # RATING HANDLER
    if user.get("awaiting_rating_recipe_id") and (m in ("1","2","3","4","5") or m == "skip"):
        recipe_id = user.get("awaiting_rating_recipe_id")
        recipe_name = user.get("awaiting_rating_recipe_name", "that recipe")
        update_user(user_id, {"awaiting_rating_recipe_id": None, "awaiting_rating_recipe_name": None})
        if m != "skip" and m in ("1","2","3","4","5"):
            rating = int(m)
            stars = "⭐" * rating
            try:
                supabase.table("recipe_ratings").upsert({
                    "user_id": user_id,
                    "recipe_id": recipe_id,
                    "rating": rating,
                }).execute()
                avg_res = supabase.table("recipe_ratings").select("rating").eq("recipe_id", recipe_id).execute()
                if avg_res.data:
                    ratings = [r["rating"] for r in avg_res.data]
                    avg = round(sum(ratings) / len(ratings), 2)
                    supabase.table("recipes").update({
                        "avg_rating": avg,
                        "rating_count": len(ratings)
                    }).eq("id", recipe_id).execute()
            except Exception as e:
                log.warning(f"Rating save failed: {e}")
            messages = {
                1: f"Thanks for the feedback! We'll try to suggest better next time 🙏",
                2: f"Thanks! We'll keep improving the suggestions 👍",
                3: f"Glad it was decent! {stars}",
                4: f"Great to hear you enjoyed it! {stars} 🎉",
                5: f"Amazing! So glad you loved *{recipe_name}*! {stars} 🎉🎉",
            }
            return messages.get(rating, "Thanks for rating!") + "\n\n" + conversation_closer(name, lang)
        return "No worries! " + conversation_closer(name, lang)

    # RECIPE SELECTION
    pending_options = user.get("pending_recipe_options")
    if pending_options and m.strip() in ("1", "2", "3", "4", "5"):
        try:
            option_ids = json.loads(pending_options)
            idx = int(m.strip()) - 1
            if 0 <= idx < len(option_ids):
                recipe_id = option_ids[idx]
                res = supabase.table("recipes").select(
                    "id, name, description, instructions, cuisine, meal_type, "
                    "prep_time_minutes, cook_time_minutes, servings, difficulty, "
                    "calories_per_serving, protein_g, carbs_g, fat_g, is_ai_generated, "
                    "avg_rating, rating_count, "
                    "recipe_ingredients(ingredients(name))"
                ).eq("id", recipe_id).execute()
                if res.data:
                    recipe = res.data[0]
                    update_user(user_id, {"pending_recipe_options": None})
                    pantry = get_pantry_names(user_id)
                    all_ings = [
                        ri["ingredients"]["name"]
                        for ri in recipe.get("recipe_ingredients", [])
                        if ri.get("ingredients") and ri["ingredients"].get("name")
                    ]
                    missing = [i for i in all_ings if i.lower() not in pantry]
                    try:
                        supabase.table("user_recipe_suggestions").insert({
                            "user_id": user_id, "recipe_id": recipe["id"]
                        }).execute()
                    except Exception:
                        pass
                    return format_recipe_with_followup(recipe, user_id, missing=missing, lang=lang)
        except Exception as e:
            log.warning(f"Recipe selection error: {e}")
        update_user(user_id, {"pending_recipe_options": None})

    # CHEF CHAT HANDLER
    if user.get("awaiting_chef_chat") or any(p in m for p in [
        "vegan", "vegetarian", "meat", "chicken only", "beef only",
        "quick", "under 20", "under 30", "fast", "easy", "simple",
        "spicy", "mild", "high protein", "low carb", "healthy",
        "give me", "suggest", "recommend", "what can i make with",
        "ninaweza kupika", "ninapenda", "nataka chaguo"
    ]):
        if user.get("awaiting_chef_chat") or any(p in m for p in [
            "vegan", "vegetarian", "meat", "chicken only", "give me",
            "suggest", "recommend", "quick", "spicy", "healthy",
            "high protein", "low carb", "what can i make", "ninaweza kupika"
        ]):
            update_user(user_id, {"awaiting_chef_chat": False})

            allowed, limit_msg = check_limit(user, "ai_chef_chats")
            if not allowed:
                return limit_msg

            pantry = get_pantry_names(user_id)
            if not pantry:
                return t("pantry_empty", lang, name=name)

            if ANTHROPIC_API_KEY:
                all_recipes = supabase.table("recipes").select(
                    "id, name, description, cuisine, meal_type, "
                    "prep_time_minutes, cook_time_minutes, difficulty, "
                    "calories_per_serving, protein_g, avg_rating, "
                    "recipe_ingredients(ingredients(name))"
                ).execute().data or []

                candidate_recipes = []
                for r in all_recipes:
                    r_ings = [ri["ingredients"]["name"].lower()
                              for ri in r.get("recipe_ingredients", [])
                              if ri.get("ingredients")]
                    if not r_ings:
                        continue
                    matches = sum(1 for i in r_ings if i in pantry)
                    if matches / len(r_ings) >= 0.5:
                        r["match_pct"] = round(matches / len(r_ings) * 100)
                        candidate_recipes.append(r)

                recipe_list = "\n".join([
                    f"- {r['name']} ({r.get('cuisine','')}, {r.get('meal_type','')}, "
                    f"{(r.get('prep_time_minutes') or 0) + (r.get('cook_time_minutes') or 0)}min, "
                    f"protein: {r.get('protein_g','?')}g, match: {r.get('match_pct',0)}%)"
                    for r in candidate_recipes[:40]
                ])

                prompt = f"""A user of a Kenyan cooking app said: "{msg}"

Their pantry contains: {", ".join(pantry)}

Available recipes they can make (at least 50% of ingredients):
{recipe_list}

Pick the 3-5 BEST recipes matching their request. Consider:
- Their specific request (vegan = no meat/fish, spicy = pilau/curry, quick = under 25min etc)
- Match percentage (higher is better)
- Variety

Return ONLY valid JSON:
{{"recipes": ["Recipe Name 1", "Recipe Name 2", "Recipe Name 3"], "message": "One friendly sentence explaining your picks"}}"""

                try:
                    resp = requests.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                              "messages": [{"role": "user", "content": prompt}]},
                        timeout=10,
                    )
                    text = resp.json()["content"][0]["text"].strip()
                    text = re.sub(r"```json|```", "", text).strip()
                    chef_result = json.loads(text)
                    suggested_names = chef_result.get("recipes", [])
                    chef_message = chef_result.get("message", "Here are my picks for you!")

                    shown = []
                    for rname in suggested_names:
                        for r in candidate_recipes:
                            if r["name"].lower() == rname.lower():
                                shown.append(r)
                                break

                    if shown:
                        increment_usage(user_id, "ai_chef_chats")
                        option_ids = [str(r["id"]) for r in shown]
                        update_user(user_id, {"pending_recipe_options": json.dumps(option_ids)})
                        lines = [f"👨‍🍳 {chef_message}\n"]
                        for i, r in enumerate(shown, 1):
                            cuisine = r.get("cuisine", "")
                            mtype = r.get("meal_type", "")
                            total_time = (r.get("prep_time_minutes") or 0) + (r.get("cook_time_minutes") or 0)
                            rating = r.get("avg_rating")
                            stars = f"⭐{rating:.1f}" if rating else ""
                            lines.append(f"{i}️⃣ 🟢 *{r['name']}* _{cuisine} • {mtype}_ ⏱{total_time}min {stars}")
                        lines.append(f"\nReply *1*–*{len(shown)}* to see the full recipe!")
                        return "\n".join(lines)
                except Exception as e:
                    log.warning(f"Chef chat AI failed: {e}")

            matches = find_matching_recipes(pantry, user)
            if matches:
                shown = matches[:5]
                option_ids = [str(r["id"]) for r in shown]
                update_user(user_id, {"pending_recipe_options": json.dumps(option_ids)})
                lines = [f"🍳 *Here are some options for you, {name}:*\n"]
                for i, r in enumerate(shown, 1):
                    total_time = (r.get("prep_time_minutes") or 0) + (r.get("cook_time_minutes") or 0)
                    lines.append(f"{i}️⃣ 🟢 *{r['name']}* _{r.get('cuisine','')} • {r.get('meal_type','')}_  ⏱{total_time}min")
                lines.append(f"\nReply *1*–*{len(shown)}* to see the full recipe!")
                return "\n".join(lines)

            return t("no_recipe_match", lang, name=name)

    # MEAL TYPE SELECTION
    awaiting_meal = user.get("awaiting_meal_type", False)
    if awaiting_meal:
        update_user(user_id, {"awaiting_meal_type": False})

        if m in ("8", "back", "back to menu", "rudi", "menu"):
            return main_menu(name, lang)

        if m in ("7", "chat with chef", "zungumza na mpishi", "chef"):
            update_user(user_id, {"awaiting_chef_chat": True})
            if lang == "sw":
                return (
                    f"👨‍🍳 Niambie unataka nini, {name}!\n\n"
                    "Unaweza kusema:\n"
                    "💬 _'Nataka chaguo za nyama'_\n"
                    "💬 _'Kitu cha vegan'_\n"
                    "💬 _'Chakula cha haraka chini ya dakika 20'_\n"
                    "💬 _'Kitu chenye protini nyingi'_\n"
                    "💬 _'Nina kuku na nyanya, napika nini?'_"
                )
            return (
                f"👨‍🍳 Tell me what you're in the mood for, {name}!\n\n"
                "You can say things like:\n"
                "💬 _'Give me meat options'_\n"
                "💬 _'Something vegan'_\n"
                "💬 _'Something quick under 20 minutes'_\n"
                "💬 _'High protein breakfast'_\n"
                "💬 _'I have chicken and tomatoes, what can I make?'_"
            )

        if m in ("6", "saved recipes", "saved", "mapishi yangu"):
            saved = get_saved_recipes(user_id)
            if not saved:
                return "⭐ No saved recipes yet.\n\nAfter getting a recipe reply:\n_save [recipe name]_"
            lines = ["⭐ *Your Saved Recipes:*", ""]
            lines += [f"  {i+1}. {r}" for i, r in enumerate(saved)]
            lines += ["", "Reply *cook* for a new suggestion!"]
            return "\n".join(lines)

        if m in ("5", "surprise me", "surprise", "chochote"):
            meal_type = None

        meal_type_map = {
            "1": "breakfast", "1️⃣": "breakfast",
            "breakfast": "breakfast", "kiamsha kinywa": "breakfast", "morning": "morning",
            "2": "lunch", "2️⃣": "lunch",
            "lunch": "lunch", "chakula cha mchana": "lunch", "midday": "lunch",
            "3": "dinner", "3️⃣": "dinner",
            "dinner": "dinner", "chakula cha jioni": "dinner", "supper": "dinner",
            "4": "snack", "4️⃣": "snack",
            "snack": "snack", "vitafunio": "snack",
        }
        if m.lower() in meal_type_map:
            meal_type = meal_type_map[m.lower()]

    # NUMBERED MENU SHORTCUTS
    if not pending_options and not awaiting_meal and not user.get("awaiting_pantry_action") and not user.get("awaiting_profile_action"):
        if m.strip() in ("1", "1️⃣"):
            m = "cook"
        elif m.strip() in ("2", "2️⃣"):
            update_user(user_id, {"awaiting_pantry_action": True})
            return pantry_menu(name, lang)
        elif m.strip() in ("3", "3️⃣"):
            update_user(user_id, {"awaiting_profile_action": True})
            return profile_menu(name, lang)
        elif m.strip() in ("4", "4️⃣", "help", "msaada"):
            return main_menu(name, lang)
        elif m.strip() in ("5", "5️⃣", "exit", "bye", "goodbye", "toka"):
            return t("goodbye", lang, name=name)

    # PANTRY SUBMENU HANDLER
    if user.get("awaiting_pantry_action"):
        update_user(user_id, {"awaiting_pantry_action": False})
        if m in ("1", "view", "angalia"):
            pantry = get_user_pantry(user_id)
            if not pantry:
                return f"🗑️ Your pantry is empty, {name}!\n\nJust tell me what you have:\n_\"I have eggs, tomatoes and rice\"_"
            names = sorted([i["name"] for i in pantry])
            lines = [f"🧺 *Your Pantry* ({len(names)} items)", ""]
            lines += [f"  • {n}" for n in names]
            lines += ["", "➕ _add [ingredient]_ to add more", "➖ _remove [ingredient]_ to remove"]
            return "\n".join(lines)
        elif m in ("2", "add", "ongeza"):
            return t("pantry_add_prompt", lang, name=name)
        elif m in ("3", "remove", "ondoa"):
            return t("pantry_remove_prompt", lang)
        elif m in ("4", "shopping list", "shopping", "orodha"):
            pantry = get_pantry_names(user_id)
            near = find_near_matches(pantry, user)
            if near:
                all_missing = []
                for r in near[:5]:
                    all_missing += r.get("missing", [])
                unique_missing = list(dict.fromkeys(all_missing))[:10]
                if unique_missing:
                    create_shopping_list(user_id, unique_missing, "Pantry Top-Up")
                    return format_shopping_list(unique_missing, "Pantry Top-Up")
            return t("shopping_list_full", lang)
        elif m in ("5", "photo", "picha"):
            return t("pantry_photo_prompt", lang)
        elif m in ("6", "back", "rudi"):
            return main_menu(name, lang)
        else:
            update_user(user_id, {"awaiting_pantry_action": True})
            return pantry_menu(name, lang)

    # PROFILE SUBMENU HANDLER
    if user.get("awaiting_profile_action"):
        update_user(user_id, {"awaiting_profile_action": False})
        if m in ("1", "view profile", "view", "angalia wasifu", "angalia"):
            allergies = ", ".join(user.get("allergies") or []) or "None"
            liked = ", ".join(user.get("liked_meals") or []) or "Not specified"
            disliked = ", ".join(user.get("disliked_meals") or []) or "None"
            budget = (user.get("budget") or "Not set").title()
            cuisines = ", ".join(user.get("preferred_cuisines") or []) or "Kenyan"
            style = (user.get("cooking_style") or "daily").replace("_", " ").title()
            return (
                f"👤 *Your Profile*\n\n"
                f"🙋 Name: {name}\n"
                f"🚫 Allergies: {allergies}\n"
                f"❤️ Loves: {liked}\n"
                f"👎 Avoids: {disliked}\n"
                f"💰 Budget: {budget}\n"
                f"🌍 Cuisines: {cuisines}\n"
                f"🍳 Cooking style: {style}\n\n"
                "Reply *2* to edit any of these."
            )
        elif m in ("2", "edit", "hariri", "edit profile", "update"):
            update_user(user_id, {"onboarding_complete": False, "onboarding_step": 0})
            reply, _ = handle_onboarding({**user, "onboarding_complete": False, "onboarding_step": 0}, m)
            return reply
        elif m in ("3", "nutrition", "lishe", "macros", "calories"):
            return get_nutrition_summary(user_id, lang)
        elif m in ("4", "saved recipes", "saved", "mapishi yangu", "favourites"):
            saved = get_saved_recipes(user_id)
            if not saved:
                return "⭐ No saved recipes yet.\n\nAfter getting a recipe reply:\n_save [recipe name]_"
            lines = ["⭐ *Your Saved Recipes:*", ""]
            lines += [f"  {i+1}. {r}" for i, r in enumerate(saved)]
            lines += ["", "Reply *cook* for a new suggestion!"]
            return "\n".join(lines)
        elif m in ("5", "back", "rudi"):
            return main_menu(name, lang)
        else:
            update_user(user_id, {"awaiting_profile_action": True})
            return profile_menu(name, lang)

    # PHOTO CONFIRMATION
    pending = user.get("pending_photo_ingredients")
    if pending and m in ("yes", "no", "cancel") or (pending and m.startswith("yes but")):
        if m == "no" or m == "cancel":
            update_user(user_id, {"pending_photo_ingredients": None})
            return f"👍 No problem, {name}! Nothing was added."

        try:
            found = json.loads(pending)
        except Exception:
            found = []

        skip = []
        if "skip" in m:
            skip_part = m.split("skip", 1)[1]
            skip = [s.strip() for s in re.split(r"and|,", skip_part) if s.strip()]
            found = [i for i in found if not any(sk in i.lower() for sk in skip)]

        update_user(user_id, {"pending_photo_ingredients": None})
        added, not_found = add_ingredients(user_id, found)
        return format_pantry_update("add", added, not_found, name)

    # HELP / HI — contextual re-entry
    if m in ("help", "menu", "start", "hi", "hello", "hey", "msaada", "habari"):
        return handle_reentry(user, m)

    # PROFILE VIEW
    if m in ("profile", "my profile", "settings"):
        allergies = ", ".join(user.get("allergies") or []) or "None"
        liked = ", ".join(user.get("liked_meals") or []) or "Not specified"
        disliked = ", ".join(user.get("disliked_meals") or []) or "None"
        budget = (user.get("budget") or "Not set").title()
        cuisines = ", ".join(user.get("preferred_cuisines") or []) or "Kenyan"
        style = (user.get("cooking_style") or "daily").replace("_", " ").title()
        return (
            f"👤 *Your Profile*\n\n"
            f"🙋 Name: {name}\n"
            f"🚫 Allergies: {allergies}\n"
            f"❤️ Loves: {liked}\n"
            f"👎 Avoids: {disliked}\n"
            f"💰 Budget: {budget}\n"
            f"🌍 Cuisines: {cuisines}\n"
            f"🍳 Cooking style: {style}\n\n"
            "Type *edit profile* to update any of these."
        )

    # EDIT PROFILE
    if m in ("edit profile", "update profile", "reset profile"):
        update_user(user_id, {"onboarding_complete": False, "onboarding_step": 0})
        reply, _ = handle_onboarding({**user, "onboarding_step": 0}, msg)
        return reply

    # PANTRY COMMAND
    if m in ("pantry", "ingredients", "my pantry", "my ingredients", "🧺"):
        update_user(user_id, {"awaiting_pantry_action": True})
        return pantry_menu(name, lang)

    # PROFILE COMMAND
    if m in ("profile", "my profile", "settings", "wasifu", "👤"):
        update_user(user_id, {"awaiting_profile_action": True})
        return profile_menu(name, lang)

    # PANTRY VIEW (direct)
    if m in ("view pantry", "angalia pantry"):
        pantry = get_user_pantry(user_id)
        if not pantry:
            return (
                f"🗑️ Your pantry is empty, {name}!\n\n"
                "Just tell me what you have at home:\n"
                "💬 _\"I have eggs, tomatoes and onions\"_\n"
                "💬 _\"Just bought some chicken and rice\"_"
            )
        names = sorted([i["name"] for i in pantry])
        lines = [f"🧺 *Your Pantry* ({len(names)} items)", ""]
        lines += [f"  • {n}" for n in names]
        lines += [
            "",
            "💬 Tell me what you bought to add items",
            "💬 Tell me what you used up to remove items",
            "🍳 Reply *cook* for a recipe!",
        ]
        return "\n".join(lines)

    # MEAL PREP PLAN (premium only)
    if "meal prep" in m or "weekly plan" in m or "week plan" in m:
        if not is_premium(user):
            if lang == "sw":
                return (
                    f"📅 Mpango wa wiki ni kipengele cha *Premium*!\n\n"
                    f"Pata Premium kwa {PREMIUM_PRICE} na upate mpango kamili wa wiki.\n\n"
                    "Andika *premium* kujua zaidi! ⭐"
                )
            return (
                f"📅 Weekly meal plans are a *Premium* feature!\n\n"
                f"Upgrade for {PREMIUM_PRICE} to get a full weekly plan tailored to your pantry.\n\n"
                "Reply *premium* to learn more! ⭐"
            )
        pantry = get_pantry_names(user_id)
        if not pantry:
            return f"😅 Your pantry is empty, {name}! Tell me what you have at home first."
        return generate_meal_plan(user, pantry)

    # SAVE RECIPE
    if m.startswith("save "):
        recipe_name = msg[5:].strip()
        return save_recipe_by_name(user_id, recipe_name, name) if recipe_name else "Tell me which recipe to save:\n_save Pilau_"

    # SAVED RECIPES
    if m in ("saved", "favourites", "favorites", "my recipes", "saved recipes"):
        saved = get_saved_recipes(user_id)
        if not saved:
            return "⭐ No saved recipes yet.\n\nAfter getting a recipe reply:\n_save [recipe name]_"
        lines = ["⭐ *Your Saved Recipes:*", ""]
        lines += [f"  {i+1}. {r}" for i, r in enumerate(saved)]
        lines += ["", "Reply *cook* for a new suggestion!"]
        return "\n".join(lines)

    # RECIPE / MEAL TYPE from direct text
    if meal_type is None:
        if any(w in m for w in ["breakfast", "morning", "brunch"]):
            meal_type = "breakfast"
        elif any(w in m for w in ["lunch", "midday", "afternoon"]):
            meal_type = "lunch"
        elif any(w in m for w in ["dinner", "supper", "evening"]):
            meal_type = "dinner"
        elif "snack" in m:
            meal_type = "snack"

    # CREATE RECIPE (before cook trigger)
    if any(p in m for p in ["create recipe", "generate recipe", "make me a recipe", "invent a recipe", "tengeneza recipe", "unda mapishi"]):
        pantry = get_pantry_names(user_id)
        if not pantry:
            return t("ai_recipe_no_pantry", lang)
        if not ANTHROPIC_API_KEY:
            return t("ai_recipe_no_key", lang)
        ai_recipe = generate_ai_recipe(pantry, user, meal_type)
        if ai_recipe:
            return t("ai_recipe_intro", lang) + format_recipe_with_followup(ai_recipe, user_id, lang=lang)
        return t("ai_recipe_fail", lang)

    if any(p in m.split() for p in ["cook", "hungry", "food", "eat"]) or \
       any(p in m for p in ["what are we", "what's cooking", "whats cooking", "nini tunachopika"]) and not meal_type:
        update_user(user_id, {"awaiting_meal_type": True})
        if lang == "sw":
            return (
                f"Tunapika nini leo, {name}? 🍳\n\n"
                "1️⃣ 🌅 *Kiamsha kinywa* — Breakfast\n"
                "2️⃣ ☀️ *Chakula cha mchana* — Lunch\n"
                "3️⃣ 🌙 *Chakula cha jioni* — Dinner\n"
                "4️⃣ 🍿 *Vitafunio* — Snack\n"
                "5️⃣ 🎲 *Chochote* — Surprise me!\n"
                "6️⃣ ⭐ *Mapishi yangu* — Saved recipes\n"
                "7️⃣ 👨‍🍳 *Zungumza na mpishi* — vegan, kali, ya haraka...\n"
                "8️⃣ 👋 *Rudi* — Back to menu"
            )
        return (
            f"What are we cooking today, {name}? 🍳\n\n"
            "1️⃣ 🌅 *Breakfast*\n"
            "2️⃣ ☀️ *Lunch*\n"
            "3️⃣ 🌙 *Dinner*\n"
            "4️⃣ 🍿 *Snack*\n"
            "5️⃣ 🎲 *Surprise me!*\n"
            "6️⃣ ⭐ *Saved recipes*\n"
            "7️⃣ 👨‍🍳 *Chat with chef* — vegan, spicy, quick...\n"
            "8️⃣ 👋 *Back to menu*"
        )

    if meal_type or m in ("5", "surprise me", "surprise", "chochote"):
        if m in ("5", "surprise me", "surprise", "chochote"):
            meal_type = None
        allowed, limit_msg = check_limit(user, "recipe_suggestions")
        if not allowed:
            return limit_msg
        pantry = get_pantry_names(user_id)
        if not pantry:
            return (
                f"Hey {name}! What do you have at home right now? 🥕\n\n"
                "Just tell me naturally:\n"
                "_\"I have eggs, rice and some tomatoes\"_"
            )

        matches = find_matching_recipes(pantry, user, meal_type=meal_type)
        matches.sort(key=lambda r: (-(r.get("avg_rating") or 0), -r.get("match_score", 0)))

        near = find_near_matches(pantry, user, meal_type=meal_type)
        near.sort(key=lambda r: (len(r.get("missing", [])), -(r.get("avg_rating") or 0)))

        all_options = []
        for r in matches:
            r["is_perfect"] = True
            all_options.append(r)
        for r in near:
            if len(all_options) >= 5:
                break
            r["is_perfect"] = False
            all_options.append(r)

        if all_options:
            shown = all_options[:5]
            option_ids = [str(r["id"]) for r in shown]
            update_user(user_id, {"pending_recipe_options": json.dumps(option_ids)})

            label = f"*{meal_type.title()} Ideas*" if meal_type else "*What can you make?*"
            lines = [f"🍳 {label}\n"]

            for i, r in enumerate(shown, 1):
                is_perfect = r.get("is_perfect", True)
                status = "🟢" if is_perfect else "🟡"
                name_str = f"*{r['name']}*"
                cuisine = r.get("cuisine", "")
                mtype = r.get("meal_type", "")
                tag = f"_{cuisine} • {mtype}_" if cuisine and mtype else ""
                prep = r.get("prep_time_minutes") or 0
                cook_t = r.get("cook_time_minutes") or 0
                total_time = prep + cook_t
                timing = f"⏱ {total_time}min" if total_time else ""
                rating = r.get("avg_rating")
                stars = f"⭐{rating:.1f}" if rating else ""
                missing = r.get("missing", [])
                missing_str = f"🟡 missing: {', '.join(missing)}" if missing else ""

                detail_parts = [p for p in [tag, timing, stars] if p]
                detail = "  ".join(detail_parts)

                lines.append(f"{i}️⃣  {status} {name_str}")
                if detail:
                    lines.append(f"    {detail}")
                if missing_str:
                    lines.append(f"    {missing_str}")
                lines.append("")

            num = len(shown)
            lines.append(f"Reply *1*–*{num}* to see the full recipe!")
            if len(matches) == 0:
                lines.append("✨ Or *create recipe* for a custom AI one!")
            increment_usage(user_id, "recipe_suggestions")
            return "\n".join(lines)

        if ANTHROPIC_API_KEY:
            ai_recipe = generate_ai_recipe(pantry, user, meal_type)
            if ai_recipe:
                try:
                    supabase.table("user_recipe_suggestions").insert({
                        "user_id": user_id, "recipe_id": ai_recipe["id"]
                    }).execute()
                    supabase.table("ai_recipe_log").insert({
                        "user_id": user_id,
                        "pantry_snapshot": json.dumps(pantry),
                        "recipe_id": ai_recipe["id"],
                    }).execute()
                except Exception:
                    pass
                return "✨ *I created a recipe just for you!*\n\n" + format_recipe_with_followup(ai_recipe, user_id, lang=lang)

        return (
            f"🤔 I couldn't find anything matching your pantry right now, {name}.\n\n"
            "What else do you have at home? Just tell me naturally!"
        )

    # NATURAL LANGUAGE PANTRY DETECTION
    is_pantry_msg = looks_like_pantry_message(m) or (
        "," in msg and len(msg.split(",")) > 3 and
        not any(k in m for k in RECIPE_KEYWORDS + EXPLICIT_COMMANDS)
    )

    if m in ("skip", "ruka", "later", "baadaye"):
        return main_menu(name, lang)

    if is_pantry_msg:
        if user.get("awaiting_meal_type") or user.get("awaiting_pantry_action"):
            update_user(user_id, {
                "awaiting_meal_type": False,
                "awaiting_pantry_action": False,
            })
        all_ingredients = get_all_ingredient_names()
        result = parse_pantry_intent(msg, all_ingredients)
        intent = result.get("intent", "none")
        ingredients = result.get("ingredients", [])

        if intent == "add" and ingredients:
            added, not_found = add_ingredients(user_id, ingredients)
            pantry_count = len(get_user_pantry(user_id))
            show_menu = pantry_count <= len(added)
            return format_pantry_update("add", added, not_found, name, show_menu=show_menu, lang=lang)

        if intent == "remove" and ingredients:
            removed, not_found = remove_ingredients(user_id, ingredients)
            return format_pantry_update("remove", removed, not_found, name, lang=lang)

    # PRIVACY & DATA COMMANDS
    if m in ("my data", "privacy", "delete my account", "delete account", "futa akaunti", "data yangu"):
        allergies = ", ".join(user.get("allergies") or []) or "None"
        return (
            f"🔒 *Your Data & Privacy*\n\n"
            f"Here's what Tunapika stores about you:\n\n"
            f"• Name: {name}\n"
            f"• WhatsApp number (your identifier)\n"
            f"• Dietary preferences & allergies: {allergies}\n"
            f"• Pantry ingredients\n"
            f"• Message history\n"
            f"• Recipe ratings and saved recipes\n\n"
            "We never sell your data or share it with third parties.\n\n"
            "To delete all your data reply *confirm delete account*.\n"
            "This is permanent and cannot be undone."
        )

    if m in ("confirm delete account", "thibitisha kufuta"):
        try:
            supabase.table("user_pantry_items").delete().eq("user_id", user_id).execute()
            supabase.table("saved_recipes").delete().eq("user_id", user_id).execute()
            supabase.table("recipe_ratings").delete().eq("user_id", user_id).execute()
            supabase.table("message_logs").delete().eq("user_id", user_id).execute()
            supabase.table("shopping_lists").delete().eq("user_id", user_id).execute()
            supabase.table("user_recipe_suggestions").delete().eq("user_id", user_id).execute()
            supabase.table("users").delete().eq("id", user_id).execute()
            log.info(f"🗑️ Account deleted for {user.get('whatsapp_number', user_id)}")
            return (
                "✅ Your account and all associated data has been permanently deleted.\n\n"
                "We're sorry to see you go. If you ever want to come back, "
                "just send us a message and we'll start fresh. 👋"
            )
        except Exception as e:
            log.error(f"Delete account failed: {e}")
            return "❌ Something went wrong deleting your account. Please try again or contact support."

    # SHOPPING LIST
    if "shopping list" in m or m == "shopping":
        pantry = get_pantry_names(user_id)
        if "for " in m:
            recipe_name = m.split("for ", 1)[1].strip()
            return shopping_list_for_recipe(recipe_name, user_id, pantry)
        near = find_near_matches(pantry, user)
        if near:
            all_missing = []
            for r in near[:5]:
                all_missing += r.get("missing", [])
            unique_missing = list(dict.fromkeys(all_missing))[:10]
            if unique_missing:
                create_shopping_list(user_id, unique_missing, "Pantry Top-Up")
                return format_shopping_list(unique_missing, "Pantry Top-Up")
        return t("shopping_list_full", lang)

    # DONE SHOPPING
    if "done shopping" in m or m in ("done", "back from shopping"):
        shopping = get_shopping_list(user_id)
        if not shopping:
            return t("no_active_shopping_list", lang)
        items = json.loads(shopping.get("items", "[]")) if isinstance(shopping.get("items"), str) else shopping.get("items", [])
        added, not_found = add_ingredients(user_id, items)
        supabase.table("shopping_lists").update({"is_complete": True}).eq("id", shopping["id"]).execute()
        lines = ["🎉 Welcome back! Added to your pantry:"]
        lines += [f"  • {i}" for i in added]
        if not_found:
            lines += ["\n❓ Couldn't find:", *[f"  • {i}" for i in not_found]]
        lines += ["\nReply *cook* to see what you can make now! 🍳"]
        return "\n".join(lines)

    # NUTRITION (premium only)
    if any(p in m for p in ["nutrition", "calories", "macros", "health stats", "lishe"]):
        if not is_premium(user):
            if lang == "sw":
                return (
                    f"📊 Muhtasari wa lishe ni kipengele cha *Premium*!\n\n"
                    f"Pata Premium kwa {PREMIUM_PRICE} ufuatilie lishe yako.\n\n"
                    "Andika *premium* kujua zaidi! ⭐"
                )
            return (
                f"📊 Nutrition summaries are a *Premium* feature!\n\n"
                f"Upgrade for {PREMIUM_PRICE} to track your daily nutrition.\n\n"
                "Reply *premium* to learn more! ⭐"
            )
        return get_nutrition_summary(user_id, lang)

    # PREMIUM INFO
    if m in ("premium", "upgrade", "subscribe", "bei", "bei ya premium"):
        return upgrade_info(lang)

    # MY PLAN / TIER STATUS
    if m in ("my plan", "my tier", "plan yangu", "subscription", "account"):
        tier = user.get("tier", "free")
        expires = user.get("premium_expires_at", "")
        if is_premium(user):
            if lang == "sw":
                return f"⭐ Wewe ni mtumiaji wa *Premium*! Asante, {name} 🙏\n\nMuda wa kumalizika: {expires[:10] if expires else 'haujawekwa'}\n\nFurahia vipengele vyote bila kikomo!"
            return f"⭐ You're on *Premium*, {name}! Thank you 🙏\n\nExpires: {expires[:10] if expires else 'not set'}\n\nEnjoy all features without limits!"
        else:
            usage = get_daily_usage(user["id"])
            if lang == "sw":
                return (
                    f"📋 *Mpango wako: Bure*\n\n"
                    f"Matumizi ya leo:\n"
                    f"• Mapendekezo ya mapishi: {usage.get('recipe_suggestions', 0)}/5\n"
                    f"• Uchanganuzi wa picha: {usage.get('photo_scans', 0)}/2\n"
                    f"• Mazungumzo na mpishi: {usage.get('ai_chef_chats', 0)}/3\n\n"
                    "Andika *premium* kupata vipengele zaidi! ⭐"
                )
            return (
                f"📋 *Your plan: Free*\n\n"
                f"Today's usage:\n"
                f"• Recipe suggestions: {usage.get('recipe_suggestions', 0)}/5\n"
                f"• Photo scans: {usage.get('photo_scans', 0)}/2\n"
                f"• Chat with Chef: {usage.get('ai_chef_chats', 0)}/3\n\n"
                "Reply *premium* to unlock everything! ⭐"
            )

    # DEFAULT FALLBACK
    return (
        f"🤔 I didn't quite get that, {name}.\n\n"
        "You can tell me things like:\n"
        "💬 _\"I have eggs and tomatoes\"_ — to update your pantry\n"
        "💬 _\"I finished the rice\"_ — to remove items\n"
        "💬 _\"cook\"_ — to get a recipe\n\n"
        "Or type *help* to see everything I can do!"
    )


# ── Photo analysis ─────────────────────────────────────────────────────────────

def fetch_image_as_base64(url: str, media_type: str) -> str | None:
    try:
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        auth = (twilio_sid, twilio_token) if twilio_sid and twilio_token else None
        log.info(f"📸 Fetching image from Twilio | SID set: {bool(twilio_sid)} | Token set: {bool(twilio_token)}")
        resp = requests.get(url, auth=auth, timeout=15)
        log.info(f"📸 Image fetch status: {resp.status_code}")
        resp.raise_for_status()
        return base64.standard_b64encode(resp.content).decode("utf-8")
    except Exception as e:
        log.warning(f"Image fetch failed: {e}")
        return None


def analyse_photo_with_claude(image_b64: str, media_type: str, known_ingredients: list[str]) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"ingredients_found": [], "image_type": "other"}

    known_str = ", ".join(known_ingredients[:150])
    prompt = f"""You are a smart pantry assistant for a Kenyan cooking app. The user sent an image showing their food/ingredients.

Your job: extract ALL food ingredients visible in the image.

Image type:
- "receipt" = shopping receipt, till slip, or written shopping list
- "fridge" = fridge, freezer, pantry shelf, spice drawer, kitchen counter with food, ANY place where food/ingredients are stored or displayed
- "other" = clearly not food related

Known ingredients in our database: {known_str}

Extraction rules:
1. Extract EVERY food item visible — spices, condiments, proteins, vegetables, grains, oils, flours, sauces, dairy
2. Match to closest name in database
3. Ignore toiletries, cleaning products, non-food items
4. If item not exactly in database, include your best match anyway

Respond ONLY in valid JSON:
{{"image_type": "receipt" | "fridge" | "other", "ingredients_found": ["ingredient1", "ingredient2", ...]}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 500,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            },
            timeout=20,
        )
        api_response = resp.json()
        log.info(f"📸 Claude API response keys: {list(api_response.keys())}")
        if "content" not in api_response:
            error_msg = api_response.get("error", {}).get("message", "")
            log.warning(f"📸 Claude API error: {api_response}")
            if "credit" in error_msg.lower() or "balance" in error_msg.lower():
                return {"ingredients_found": [], "image_type": "no_credits"}
            return {"ingredients_found": [], "image_type": "other"}
        text = api_response["content"][0]["text"].strip()
        log.info(f"📸 Claude raw response: {text[:500]}")
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        log.info(f"📸 Photo analysis result: {result}")
        return result
    except Exception as e:
        err = str(e).lower()
        if "credit" in err or "billing" in err or "balance" in err:
            log.warning("Anthropic credits exhausted - photo analysis unavailable")
        else:
            log.warning(f"Photo analysis failed: {e}")
        return {"ingredients_found": [], "image_type": "other"}


def handle_photo(media_files: list[dict], user: dict) -> str:
    """
    Analyse one or more photos and merge all found ingredients.
    media_files: [{"url": "...", "type": "image/jpeg"}, ...]
    """
    name = user.get("full_name", "Friend")
    user_id = user["id"]
    lang = user.get("language", "en")
    sw = lang == "sw"

    if not ANTHROPIC_API_KEY:
        if sw:
            return "📸 Naona picha yako, lakini nahitaji ufunguo wa AI kuisoma.\n\nKwa sasa, niambie tu una nini:\n_Nina nyanya, mayai, maziwa_"
        return "📸 I can see your photo, but I need an AI key to analyse it.\n\nFor now, just tell me what you have:\n_I have tomatoes, eggs, milk_"

    all_ingredients = get_all_ingredient_names()
    all_found = []
    no_credits = False
    failed_count = 0
    image_types = []

    for idx, media in enumerate(media_files):
        log.info(f"📸 Processing photo {idx + 1}/{len(media_files)}")
        image_b64 = fetch_image_as_base64(media["url"], media["type"])
        if not image_b64:
            failed_count += 1
            continue

        result = analyse_photo_with_claude(image_b64, media["type"], all_ingredients)
        image_type = result.get("image_type", "other")
        found = result.get("ingredients_found", [])

        if image_type == "no_credits":
            no_credits = True
            break

        image_types.append(image_type)
        log.info(f"📸 Photo {idx + 1}: type={image_type} | found={found}")
        all_found.extend(found)

    if no_credits:
        if sw:
            return "📸 Uchanganuzi wa picha haufanyi kazi kwa sasa.\n\nBado unaweza kuongeza viungo kwa kuandika:\n_'Nina nyanya, mayai na kuku'_"
        return "📸 Photo scanning is temporarily unavailable.\n\nYou can still add ingredients by typing:\n_'I have tomatoes, eggs and chicken'_"

    if failed_count == len(media_files):
        if sw:
            return "😕 Sikuweza kupakua picha zako. Tafadhali jaribu tena!"
        return "😕 I couldn't download your photos. Please try again or tell me what you have in text!"

    # Deduplicate while preserving order
    seen = set()
    unique_found = []
    for item in all_found:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique_found.append(item)

    if not unique_found:
        if sw:
            return (
                f"🤔 Sikuona viungo vyovyote kwenye picha hizo, {name}.\n\n"
                "Jaribu kutuma:\n"
                "📸 Picha ya friji/pantry yako\n"
                "🧾 Picha ya risiti yako\n\n"
                "Au andika tu: _Nina nyanya, mayai, vitunguu saumu_"
            )
        return (
            f"🤔 I couldn't spot any ingredients in those photos, {name}.\n\n"
            "Try sending:\n"
            "📸 A clearer photo of your fridge or pantry\n"
            "🧾 A photo of your shopping receipt\n\n"
            "Or just type: _I have tomatoes, eggs, garlic_"
        )

    update_user(user_id, {"pending_photo_ingredients": json.dumps(unique_found)})

    # Build summary header
    photo_count = len(media_files)
    has_receipt = "receipt" in image_types
    has_fridge = any(t in ("fridge", "other") for t in image_types)

    if photo_count == 1:
        type_emoji = "🧾" if has_receipt else "🧊"
        type_label = ("risiti" if sw else "receipt") if has_receipt else ("friji/pantry" if sw else "fridge/pantry")
        if sw:
            header = f"{type_emoji} *Nimechunguza {type_label} yako!*"
        else:
            header = f"{type_emoji} *I analysed your {type_label}!*"
    else:
        if sw:
            header = f"📸 *Nimechunguza picha {photo_count} zako!*"
        else:
            header = f"📸 *I analysed all {photo_count} photos!*"

    if sw:
        count_line = f"Nimepata viungo {len(unique_found)}:"
        confirm_lines = [
            "",
            "Niziongeze zote kwenye pantry yako?", "",
            "✅ Andika *ndiyo* kuziongeza zote",
            "❌ Andika *hapana* kufuta",
            "✏️ Au sema unataka kuruka: _ndiyo lakini ruka maziwa_",
        ]
    else:
        count_line = f"Found {len(unique_found)} ingredient(s) across all photos:"
        confirm_lines = [
            "",
            "Shall I add all of these to your pantry?", "",
            "✅ Reply *yes* to add them all",
            "❌ Reply *no* to cancel",
            "✏️ Or say what to skip: _yes but skip the milk_",
        ]

    lines = [header, count_line, ""]
    lines += [f"  • {i}" for i in unique_found]
    lines += confirm_lines
    return "\n".join(lines)


# ── Menu helpers ───────────────────────────────────────────────────────────────

def main_menu(name: str, lang: str = "en") -> str:
    return t("main_menu", lang, name=name)


def pantry_menu(name: str, lang: str = "en") -> str:
    return t("pantry_menu", lang, name=name)


def profile_menu(name: str, lang: str = "en") -> str:
    return t("profile_menu", lang, name=name)


def cooking_followup(recipe_name: str, lang: str = "en") -> str:
    return t("cooking_followup", lang, recipe_name=recipe_name)


def send_message(to: str, text: str):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        requests.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": TWILIO_FROM, "To": to, "Body": text},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"send_message failed: {e}")


# ── Webhook ────────────────────────────────────────────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    validate_twilio_signature(request)

    body = sanitise_input(request.values.get("Body", ""))
    from_number = request.values.get("From", "").strip()
    profile_name = sanitise_input(request.values.get("ProfileName", ""))
    num_media = int(request.values.get("NumMedia", "0"))

    # Collect all media files (Twilio supports up to 10 per message)
    media_files = []
    for i in range(min(num_media, 10)):
        url = request.values.get(f"MediaUrl{i}", "").strip()
        mtype = request.values.get(f"MediaContentType{i}", "").strip()
        if url and mtype.startswith("image/"):
            media_files.append({"url": url, "type": mtype})

    log.info(f"📩 From={from_number} | Body={body!r} | Media={num_media} | Images={len(media_files)}")

    response = MessagingResponse()
    msg_obj = response.message()

    if not from_number:
        return str(response)

    if is_rate_limited(from_number):
        log.warning(f"🚨 Rate limit exceeded for {from_number}")
        msg_obj.body("⏳ You're sending messages too fast. Please wait a moment and try again.")
        return str(response)

    user = get_or_create_user(from_number, profile_name)
    if not user:
        msg_obj.body("⚠️ Could not find or create your account. Please try again.")
        return str(response)

    user_id = user["id"]
    log_message(user_id, "inbound", body or f"[{len(media_files)} photo(s)]")

    # Block check
    if user.get("is_blocked"):
        msg_obj.body("⛔ Your account has been suspended for violating our terms of service.")
        return str(response)

    # Abuse filter on text messages
    if body and is_abusive(body):
        strikes = (user.get("abuse_strikes") or 0) + 1
        update_user(user_id, {"abuse_strikes": strikes})
        log.warning(f"⚠️ Abusive message from {from_number} (strike {strikes})")
        if strikes >= 3:
            block_user(user_id, "repeated abusive messages")
            msg_obj.body("⛔ Your account has been suspended due to repeated violations of our Terms & Conditions.")
        else:
            remaining = 3 - strikes
            warn = "1 more violation" if remaining == 1 else f"{remaining} more violations"
            msg_obj.body(
                f"⚠️ That message contained inappropriate content.\n\n"
                f"Please keep conversations respectful. "
                f"{warn} will result in a permanent ban."
            )
        return str(response)

    if media_files:
        if not user.get("onboarding_complete"):
            reply = "👋 Please finish setting up your profile first! Reply *hi* to continue."
        else:
            allowed, limit_msg = check_limit(user, "photo_scans")
            if not allowed:
                reply = limit_msg
            else:
                increment_usage(user_id, "photo_scans")
                reply = handle_photo(media_files, user)
        msg_obj.body(reply)
        log_message(user_id, "outbound", reply)
        return str(response)

    if not body:
        return str(response)

    if not user.get("onboarding_complete"):
        reply, _ = handle_onboarding(user, body)
    else:
        reply = route(body, user)
        # handle_reentry may signal to fall back to onboarding for mid-setup users
        if reply is None:
            reply, _ = handle_onboarding(user, body)

    msg_obj.body(reply)
    log_message(user_id, "outbound", reply)
    log.info(f"✅ Replied to {from_number}")

    return str(response)


# ── Health & debug ─────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return {"status": "ok", "bot": "Tunapika"}


@app.route("/debug/pantry/<whatsapp_number>")
def debug_pantry(whatsapp_number):
    user = get_or_create_user(whatsapp_number, "Debug")
    if not user:
        return {"error": "user not found"}
    pantry = get_pantry_names(user["id"])
    matches = find_matching_recipes(pantry, user)
    return {
        "user": {k: v for k, v in user.items() if k != "id"},
        "pantry_items": pantry,
        "matching_recipe_count": len(matches),
        "matching_recipes": [r["name"] for r in matches],
    }


if __name__ == "__main__":
    app.run(debug=True, port=8000)