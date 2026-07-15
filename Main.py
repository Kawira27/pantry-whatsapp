import os
import re
import json
import random
import logging
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client
from dotenv import load_dotenv

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

CUISINES = [
    "Kenyan", "Indian", "Italian", "Chinese",
    "Mexican", "Mediterranean", "American", "International"
]

HELP_MSG = """🍳 *PantryChef* — What can I do?

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
    # English - explicit
    "i have", "i also have", "i've got", "i also got", "i got", "i bought",
    "i also bought", "just bought", "just got", "also picked up", "also got",
    "we have", "we also have", "at home", "in my fridge", "in the fridge",
    "in my kitchen", "i picked up", "picked up", "i found", "there's some",
    "got some", "went shopping", "from the shop", "from the market",
    "i picked", "purchased", "i also picked", "forgot to mention",
    "also have", "oh and i have", "oh i also",
    # English - quantity-based (one X, two Y, a packet of)
    "one ", "two ", "three ", "a packet", "a bag", "a bunch", "a loaf",
    "a tin", "a can", "a bottle", "a kilo", "half a", "some ",
    # Swahili
    "nimenunua", "niko na", "nimepata", "niko nazo", "kuna", "nimebuy",
    "pia niko na", "pia nimenunua", "niko na", "nina ", "tuna ",
    "niliambia", "nimechukua", "nimepata",
]


def parse_pantry_intent_local(message: str, known_ingredients: list[str]) -> dict:
    """
    Local ingredient extractor — no API key needed.
    Scans the message for known ingredient names and detects add/remove intent.
    """
    m = message.lower()

    # Detect intent
    is_remove = any(sig in m for sig in REMOVE_SIGNALS)
    is_add = any(sig in m for sig in ADD_SIGNALS)

    if not is_remove and not is_add:
        return {"intent": "none", "ingredients": []}

    intent = "remove" if is_remove else "add"

    # Find matching ingredients by scanning message for known names
    found = []
    for ing in known_ingredients:
        ing_lower = ing.lower()
        # Match whole word / phrase
        if re.search(r'\b' + re.escape(ing_lower) + r'\b', m):
            found.append(ing)

    log.info(f"🔍 Local NLU: intent={intent}, found={found}")
    return {"intent": intent, "ingredients": found}


def parse_pantry_intent(message: str, known_ingredients: list[str]) -> dict:
    """
    Use Claude AI if API key is set, otherwise fall back to local extraction.
    Returns: {intent: 'add'|'remove'|'none', ingredients: [...]}
    """
    # Always try local first — fast and free
    local = parse_pantry_intent_local(message, known_ingredients)

    # If local found something, use it
    if local["ingredients"]:
        return local

    # Try Claude AI for harder cases (e.g. "picked up a few things at Carrefour")
    if not ANTHROPIC_API_KEY:
        return local

    known_str = ", ".join(known_ingredients[:80])
    prompt = f"""You are a smart pantry assistant for a Kenyan cooking app. A user sent this WhatsApp message:
"{message}"

Known ingredients in our database: {known_str}

Decide if the user is:
1. ADDING ingredients - includes ANY of these patterns:
   - Explicit: "I have", "I bought", "just got", "nimenunua", "niko na"
   - Listing with quantities: "one egg, two tomatoes, a packet of milk"
   - Shopping list style: "one kitunguu, one egg, one packet of milk, rice, chicken"
   - Any message that lists food items they currently possess
2. REMOVING ingredients: "used up", "ran out", "finished", "imekwisha", "hakuna"
3. NEITHER: asking for recipes, general chat

Handle Swahili/Kenyan names and map to English:
- kitunguu = onions, mayai/yai = eggs, maziwa = milk, nyanya = tomatoes
- kuku = chicken, nyama = beef, wali/mchele = rice, sukuma = sukuma wiki
- nduma = arrowroots, muhogo = cassava, ndizi = bananas, karoti = carrots

Extract only ingredients matching the known list. Ignore quantities like "one", "two", "a packet of".

Respond ONLY with valid JSON:
{{"intent": "add" | "remove" | "none", "ingredients": ["ingredient1", "ingredient2"]}}"""

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
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=8,
        )
        text = resp.json()["content"][0]["text"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        log.info(f"🤖 Claude NLU: {result}")
        return result
    except Exception as e:
        log.warning(f"Claude NLU failed: {e}, using local result")
        return local


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


def format_pantry_update(intent: str, added_or_removed: list[str], not_found: list[str], user_name: str) -> str:
    action = "added to" if intent == "add" else "removed from"
    emoji = "✅" if intent == "add" else "🗑️"
    lines = []
    if added_or_removed:
        lines.append(f"{emoji} Got it, {user_name}! I've {action} your pantry:")
        lines += [f"  • {i}" for i in added_or_removed]
    if not_found:
        lines.append(f"\n❓ I didn't recognise these:")
        lines += [f"  • {i}" for i in not_found]
        lines.append("_Try a slightly different spelling._")
    lines += ["", "Reply *pantry* to see everything you have, or *cook* for a recipe! 🍳"]
    return "\n".join(lines)


def find_matching_recipes(pantry_names: list[str], user: dict, meal_type: str = None, max_missing: int = 0) -> list[dict]:
    """
    Find recipes matching pantry.
    max_missing=0: exact matches only
    max_missing=2: also return near-matches missing up to 2 ingredients
    Each recipe gets a 'missing' key listing what's needed.
    """
    query = supabase.table("recipes").select(
        "id, name, description, instructions, cuisine, meal_type, "
        "prep_time_minutes, cook_time_minutes, servings, difficulty, "
        "calories_per_serving, protein_g, carbs_g, fat_g, is_ai_generated, "
        "recipe_ingredients(ingredients(name))"
    )
    if meal_type:
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
        # Allergy check
        if any(a in required for a in allergies):
            continue
        # Dislike check
        if any(d in recipe["name"].lower() for d in disliked):
            continue
        # Cuisine check
        recipe_cuisine = (recipe.get("cuisine") or "").lower()
        if preferred_cuisines and not open_to_cuisines:
            if recipe_cuisine not in preferred_cuisines and recipe_cuisine != "kenyan":
                continue
        # Ingredient matching
        missing = [i for i in required if i not in pantry_names]
        if len(missing) <= max_missing:
            recipe["missing"] = missing
            recipe["match_score"] = len(required) - len(missing)
            matches.append(recipe)

    # Sort: perfect matches first, then by most ingredients matched
    matches.sort(key=lambda r: (len(r["missing"]), -r["match_score"]))
    return matches


def find_near_matches(pantry_names: list[str], user: dict, meal_type: str = None) -> list[dict]:
    """Return recipes missing 1-2 ingredients, excluding perfect matches."""
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


def save_recipe_by_name(user_id: str, recipe_name: str) -> str:
    res = supabase.table("recipes").select("id, name").ilike("name", f"%{recipe_name.strip()}%").execute()
    if not res.data:
        return f"❌ Couldn't find *{recipe_name}*. Try the exact recipe name."
    recipe = res.data[0]
    existing = supabase.table("saved_recipes").select("id").eq("user_id", user_id).eq("recipe_id", recipe["id"]).execute()
    if existing.data:
        return f"⭐ *{recipe['name']}* is already saved!"
    supabase.table("saved_recipes").insert({"user_id": user_id, "recipe_id": recipe["id"]}).execute()
    return f"✅ *{recipe['name']}* saved to your favourites!"


def log_message(user_id: str, direction: str, body: str):
    try:
        supabase.table("message_logs").insert({
            "user_id": user_id, "direction": direction, "message_text": body, "intent": "",
        }).execute()
    except Exception as e:
        log.warning(f"Could not log: {e}")


def format_recipe(recipe: dict, show_nutrition: bool = True) -> str:
    ingredients = [
        ri["ingredients"]["name"]
        for ri in recipe.get("recipe_ingredients", [])
        if ri.get("ingredients") and ri["ingredients"].get("name")
    ]
    cuisine = recipe.get("cuisine", "")
    meal_type = recipe.get("meal_type", "")
    is_ai = recipe.get("is_ai_generated", False)

    tag_parts = []
    if cuisine:
        tag_parts.append(cuisine)
    if meal_type:
        tag_parts.append(meal_type)
    if is_ai:
        tag_parts.append("✨ AI recipe")
    tag = f"_{' • '.join(tag_parts)}_" if tag_parts else ""

    lines = [f"🍽️ *{recipe['name']}*"]
    if tag:
        lines.append(tag)

    # Timing & servings
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
    if recipe.get("description"):
        lines += [recipe["description"], ""]

    if ingredients:
        lines.append("🛒 *Ingredients:*")
        lines += [f"  • {i}" for i in ingredients]
        lines.append("")

    steps = recipe.get("instructions") or ""
    if steps:
        lines.append("👨‍🍳 *Steps:*")
        step_list = steps if isinstance(steps, list) else str(steps).split("\n")
        for n, s in enumerate(step_list, 1):
            if str(s).strip():
                lines.append(f"  {n}. {str(s).strip()}")
        lines.append("")

    # Nutrition
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


def format_near_match(recipe: dict) -> str:
    """Format a near-match recipe showing what's missing."""
    missing = recipe.get("missing", [])
    total = recipe.get("match_score", 0) + len(missing)
    lines = [
        f"🟡 *{recipe['name']}* _{recipe.get('cuisine', '')} • {recipe.get('meal_type', '')}_",
        f"You have *{recipe['match_score']}/{total}* ingredients",
        f"Missing: {', '.join(missing)}",
        f"Reply *shopping list* to add missing items, or *cook anyway* to see the full recipe."
    ]
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



# ── Phase 2: AI Recipe Generation ─────────────────────────────────────────────

def format_recipe_with_followup(recipe: dict, user_id: str) -> str:
    """Format recipe and ask if they cooked it afterwards."""
    recipe_text = format_recipe(recipe)
    update_user(user_id, {
        "last_suggested_recipe_id": str(recipe["id"]),
        "last_suggested_recipe_name": recipe["name"],
        "awaiting_cooking_confirmation": True,
    })
    followup = cooking_followup(recipe["name"])
    return recipe_text + "\n\n" + followup


def generate_ai_recipe(pantry_names: list[str], user: dict, meal_type: str = None) -> dict | None:
    """Ask Claude to create a recipe from the user's pantry. Saves to DB."""
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
  "instructions": "Step 1.\nStep 2.\nStep 3.",
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

        # Save to DB
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

        # Link ingredients
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

        # Reload full recipe with ingredients
        full = supabase.table("recipes").select(
            "id, name, description, instructions, cuisine, meal_type, "
            "prep_time_minutes, cook_time_minutes, servings, difficulty, "
            "calories_per_serving, protein_g, carbs_g, fat_g, is_ai_generated, "
            "recipe_ingredients(ingredients(name))"
        ).eq("id", recipe["id"]).execute()

        return full.data[0] if full.data else None

    except Exception as e:
        log.warning(f"AI recipe generation failed: {e}")
        return None


# ── Phase 2: Shopping List ─────────────────────────────────────────────────────

def get_shopping_list(user_id: str) -> dict | None:
    """Get user's current active shopping list."""
    res = supabase.table("shopping_lists").select("*").eq("user_id", user_id).eq("is_complete", False).order("created_at", desc=True).limit(1).execute()
    return res.data[0] if res.data else None


def create_shopping_list(user_id: str, items: list[str], name: str = "Shopping List") -> dict:
    """Create a new shopping list."""
    res = supabase.table("shopping_lists").insert({
        "user_id": user_id,
        "name": name,
        "items": json.dumps(items),
    }).execute()
    return res.data[0] if res.data else {}


def format_shopping_list(items: list[str], name: str = "Shopping List") -> str:
    lines = [f"🛒 *{name}*", f"_{len(items)} item(s)_", ""]
    lines += [f"  ☐ {item}" for item in items]
    lines += ["", "Reply *done shopping* when you're back — I'll add everything to your pantry!"]
    return "\n".join(lines)


def shopping_list_for_recipe(recipe_name: str, user_id: str, pantry_names: list[str]) -> str:
    """Generate shopping list for a specific recipe."""
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


# ── Phase 2: Nutrition Summary ─────────────────────────────────────────────────

def get_nutrition_summary(user_id: str) -> str:
    """Get nutrition summary from recent suggestions."""
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
        return "📊 No nutrition data yet — cook some recipes first and I'll track your stats!"

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

# ── Onboarding ─────────────────────────────────────────────────────────────────

def handle_onboarding(user: dict, msg: str) -> tuple[str, bool]:
    step = user.get("onboarding_step", 0)
    user_id = user["id"]

    if step == 0:
        update_user(user_id, {"onboarding_step": 1})
        return (
            "👋 Welcome to *PantryChef*! I help you cook great meals from what you already have.\n\n"
            "Let's set up your profile — it only takes a minute!\n\n"
            "*What's your name?*", False
        )

    if step == 1:
        name = msg.strip().title()
        update_user(user_id, {"full_name": name, "onboarding_step": 2})
        return (
            f"Nice to meet you, *{name}*! 😊\n\n"
            "Do you have any *food allergies or dietary restrictions?*\n\n"
            "e.g. _nuts, dairy, gluten, pork_\n"
            "Or type *none*.", False
        )

    if step == 3:
        allergies = [] if msg.strip().lower() == "none" else [a.strip() for a in msg.replace(",", " ").split() if a.strip()]
        update_user(user_id, {"allergies": allergies, "onboarding_step": 4})
        ack = "Noted your allergies! ✅" if allergies else "Great, no allergies! ✅"
        return (
            f"{ack}\n\n"
            "What are some *meals or foods you love?* 🥰\n\n"
            "e.g. _pilau, chicken, pasta, ugali_\n"
            "Or type *skip*.", False
        )

    if step == 4:
        liked = [] if msg.strip().lower() == "skip" else [a.strip() for a in msg.replace(",", " ").split() if a.strip()]
        update_user(user_id, {"liked_meals": liked, "onboarding_step": 5})
        return (
            "Yum! Great taste 😄\n\n"
            "Any *foods or meals you dislike or avoid?*\n\n"
            "e.g. _fish, liver_\n"
            "Or type *none*.", False
        )

    if step == 5:
        disliked = [] if msg.strip().lower() == "none" else [a.strip() for a in msg.replace(",", " ").split() if a.strip()]
        update_user(user_id, {"disliked_meals": disliked, "onboarding_step": 6})
        return (
            "Noted! I'll keep those off your plate 🙅\n\n"
            "*What's your weekly food budget?*\n\n"
            "1️⃣ *low* — Under Ksh 1,000\n"
            "2️⃣ *medium* — Ksh 1,000–3,000\n"
            "3️⃣ *high* — Ksh 3,000+\n\n"
            "Reply *low*, *medium*, or *high*.", False
        )

    if step == 6:
        budget = msg.strip().lower()
        if budget not in ("low", "medium", "high"):
            return ("Please reply with *low*, *medium*, or *high* 😊", False)
        update_user(user_id, {"budget": budget, "onboarding_step": 7})
        cuisine_list = "\n".join([f"{i+1}️⃣ {c}" for i, c in enumerate(CUISINES)])
        return (
            "Got it! 💰\n\n"
            "Would you like to *explore other cuisines* beyond Kenyan food? 🌍\n\n"
            f"{cuisine_list}\n\n"
            "Reply with the *numbers* of cuisines you'd like (e.g. _1, 3_)\n"
            "Or type *no* to stick to Kenyan food only.", False
        )

    if step == 7:
        m = msg.strip().lower()
        if m == "no":
            update_user(user_id, {"open_to_cuisines": False, "preferred_cuisines": ["Kenyan"], "onboarding_step": 8})
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
            update_user(user_id, {"open_to_cuisines": True, "preferred_cuisines": selected, "onboarding_step": 8})
        return (
            "Awesome! 🌍\n\n"
            "Last one — how do you prefer to cook?\n\n"
            "1️⃣ *daily* — I cook fresh every day\n"
            "2️⃣ *meal prep* — I prep meals once a week\n\n"
            "Reply *daily* or *meal prep*.", False
        )

    if step == 8:
        m = msg.strip().lower()
        style = "meal_prep" if "meal" in m or "prep" in m or m == "2" else "daily"
        name = user.get("full_name", "Friend")
        update_user(user_id, {"cooking_style": style, "onboarding_complete": True, "onboarding_step": 9})
        style_msg = "I'll suggest weekly meal plans for you! 📅" if style == "meal_prep" else "I'll suggest fresh daily recipes! 🍳"
        return (
            f"🎉 You're all set, *{name}*!\n\n"
            f"{style_msg}\n\n"
            "Now let's stock your pantry! Just tell me what you have at home — "
            "speak naturally, like you're texting a friend:\n\n"
            "💬 _\"I have eggs, tomatoes and some rice\"_\n"
            "💬 _\"Just bought chicken and garlic\"_\n\n"
            "Or type *cook* if your pantry is already set up!\n"
            "Type *help* anytime to see all commands.", True
        )

    return (HELP_MSG, True)


# ── Intent router ──────────────────────────────────────────────────────────────

# Keywords that are clearly NOT pantry-related (avoid false NLU calls)
RECIPE_KEYWORDS = ["cook", "recipe", "hungry", "what are we", "breakfast", "lunch",
                   "dinner", "meal prep", "weekly plan", "food", "eat", "supper",
                   "morning", "evening", "brunch", "snack"]
EXPLICIT_COMMANDS = ["help", "menu", "start", "hi", "hello", "hey", "pantry",
                     "ingredients", "saved", "favourites", "favorites", "profile",
                     "my profile", "settings", "edit profile", "update profile"]


def looks_like_pantry_message(msg: str) -> bool:
    """Heuristic: does this message sound like it's about having/getting/using ingredients?"""
    m = msg.lower()
    pantry_signals = [
        # Adding
        "i have", "i've got", "i got", "i bought", "just bought", "just got",
        "we have", "at home", "in my fridge", "in the fridge", "in my kitchen",
        "i picked up", "picked up some", "i found", "there's some", "got some",
        "went shopping", "from the shop", "from the market", "nimenunua", "niko na",
        "nimepata", "niko nazo", "kuna", "nimebuy",
        # Removing
        "i used", "i finished", "ran out", "used up", "no more", "finished the",
        "i don't have", "i do not have", "out of", "imekwisha", "nimemaliza",
        "hakuna", "nimetumia", "imeisha",
    ]
    return any(signal in m for signal in pantry_signals)


def format_pantry_update(action: str, items: list[str], not_found: list[str], name: str) -> str:
    """Format a friendly pantry update confirmation."""
    lines = []
    if action == "add":
        real_adds = [i for i in items if "(already" not in i]
        already = [i for i in items if "(already" in i]
        if real_adds:
            lines.append("✅ Added to your pantry:")
            lines += [f"  • {i}" for i in real_adds]
        if already:
            lines.append("\n📌 Already in your pantry:")
            lines += [f"  • {i.replace(' (already in pantry)', '')}" for i in already]
    else:
        if items:
            lines.append("🗑️ Removed from your pantry:")
            lines += [f"  • {i}" for i in items]

    if not_found:
        lines.append("\n❓ Didn't recognise:")
        lines += [f"  • {i}" for i in not_found]
        lines.append("  _(These might not be in our ingredient list yet)_")

    if not lines:
        return f"🤔 Hmm, I couldn't find those ingredients, {name}. Try being more specific!"

    lines += ["", "🍳 Reply *cook* when you're ready for a recipe!"]
    return "\n".join(lines)


def route(msg: str, user: dict) -> str:
    user_id = user["id"]
    m = msg.strip().lower()
    name = user.get("full_name", "Friend")

    # NUMBERED MENU SHORTCUTS
    if m.strip() in ("1", "1️⃣"):
        m = "cook"
    elif m.strip() in ("2", "2️⃣"):
        m = "pantry"
    elif m.strip() in ("3", "3️⃣"):
        m = "saved"
    elif m.strip() in ("4", "4️⃣"):
        m = "profile"
    elif m.strip() in ("5", "5️⃣", "exit", "bye", "goodbye"):
        return f"👋 Goodbye {name}! Come back when you're hungry 😄\nReply *hi* anytime to get started again."

    # COOKING CONFIRMATION
    if user.get("awaiting_cooking_confirmation"):
        recipe_name = user.get("last_suggested_recipe_name", "that recipe")
        recipe_id = user.get("last_suggested_recipe_id")
        if m in ("yes, i cooked it", "yes i cooked it", "yes", "1", "cooked", "i cooked it"):
            # Remove recipe ingredients from pantry
            update_user(user_id, {"awaiting_cooking_confirmation": False})
            if recipe_id:
                res = supabase.table("recipe_ingredients").select("ingredients(name)").eq("recipe_id", recipe_id).execute()
                ing_names = [r["ingredients"]["name"] for r in res.data if r.get("ingredients")]
                removed, _ = remove_ingredients(user_id, ing_names)
                lines = [f"✅ Great cook, {name}! Removed from your pantry:"]
                lines += [f"  • {i}" for i in removed]
                lines += ["", main_menu(name)]
                return "\n\n".join(lines)
        elif m in ("used some", "used some ingredients", "2", "some"):
            update_user(user_id, {"awaiting_cooking_confirmation": False})
            return "Which ingredients did you use? Just tell me naturally:\n_'I used the eggs and tomatoes'_"
        else:
            update_user(user_id, {"awaiting_cooking_confirmation": False})
            return f"👍 No problem! Your pantry stays as is.\n\n" + main_menu(name)

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

        # Parse skips: "yes but skip the milk and bread"
        skip = []
        if "skip" in m:
            skip_part = m.split("skip", 1)[1]
            skip = [s.strip() for s in re.split(r"and|,", skip_part) if s.strip()]
            found = [i for i in found if not any(sk in i.lower() for sk in skip)]

        update_user(user_id, {"pending_photo_ingredients": None})
        added, not_found = add_ingredients(user_id, found)
        return format_pantry_update("add", added, not_found, name)

    # HELP
    if m in ("help", "menu", "start", "hi", "hello", "hey"):
        return f"Hey {name}! 👋\n\n" + HELP_MSG

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

    # PANTRY VIEW
    if m in ("pantry", "ingredients", "my pantry", "my ingredients"):
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

    # MEAL PREP PLAN
    if "meal prep" in m or "weekly plan" in m or "week plan" in m:
        pantry = get_pantry_names(user_id)
        if not pantry:
            return f"😅 Your pantry is empty, {name}! Tell me what you have at home first."
        return generate_meal_plan(user, pantry)

    # SAVE RECIPE
    if m.startswith("save "):
        recipe_name = msg[5:].strip()
        return save_recipe_by_name(user_id, recipe_name) if recipe_name else "Tell me which recipe:\n_save Pilau_"

    # SAVED RECIPES
    if m in ("saved", "favourites", "favorites", "my recipes", "saved recipes"):
        saved = get_saved_recipes(user_id)
        if not saved:
            return "⭐ No saved recipes yet.\n\nAfter getting a recipe reply:\n_save [recipe name]_"
        lines = ["⭐ *Your Saved Recipes:*", ""]
        lines += [f"  {i+1}. {r}" for i, r in enumerate(saved)]
        lines += ["", "Reply *cook* for a new suggestion!"]
        return "\n".join(lines)

    # RECIPE / MEAL TYPE REQUESTS
    meal_type = None
    if any(w in m for w in ["breakfast", "morning", "brunch"]):
        meal_type = "breakfast"
    elif any(w in m for w in ["lunch", "midday", "afternoon"]):
        meal_type = "lunch"
    elif any(w in m for w in ["dinner", "supper", "evening"]):
        meal_type = "dinner"
    elif "snack" in m:
        meal_type = "snack"

    if meal_type or any(p in m for p in ["cook", "recipe", "hungry", "what are we", "what's cooking", "whats cooking", "food", "eat"]):
        pantry = get_pantry_names(user_id)
        if not pantry:
            return f"😅 Your pantry is empty! Tell me what you have at home first, {name} 😊"

        # Perfect matches first
        matches = find_matching_recipes(pantry, user, meal_type=meal_type)
        if matches:
            recipe = random.choice(matches)
            try:
                supabase.table("user_recipe_suggestions").insert({
                    "user_id": user_id, "recipe_id": recipe["id"]
                }).execute()
            except Exception:
                pass
            return format_recipe_with_followup(recipe, user_id)

        # Near-matches (missing 1-2 ingredients)
        near = find_near_matches(pantry, user, meal_type=meal_type)
        if near:
            label = meal_type or "recipe"
            lines = [f"🤔 No perfect {label} match, but these are close:\n"]
            for r in near[:3]:
                lines.append(format_near_match(r))
                lines.append("")
            lines.append("✨ Or reply *create recipe* and I'll make one just for you!")
            return "\n".join(lines)

        # AI recipe generation
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
                return "✨ *No existing recipe matched, so I created one just for you!*\n\n" + format_recipe_with_followup(ai_recipe, user_id)

        label = f"{meal_type} recipe" if meal_type else "recipe"
        return f"🤔 No {label} matches your pantry right now.\n\nTell me what else you have at home and I'll find something!"

    # ── NATURAL LANGUAGE PANTRY DETECTION ──────────────────────────────────────
    # Only call AI if the message has pantry-like signals
    if looks_like_pantry_message(m):
        all_ingredients = get_all_ingredient_names()
        result = parse_pantry_intent(msg, all_ingredients)
        intent = result.get("intent", "none")
        ingredients = result.get("ingredients", [])

        if intent == "add" and ingredients:
            added, not_found = add_ingredients(user_id, ingredients)
            return format_pantry_update("add", added, not_found, name)

        if intent == "remove" and ingredients:
            removed, not_found = remove_ingredients(user_id, ingredients)
            return format_pantry_update("remove", removed, not_found, name)

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
        return "🛒 Your pantry already covers most recipes! Reply *cook* to see what you can make."

    # DONE SHOPPING
    if "done shopping" in m or m in ("done", "back from shopping"):
        shopping = get_shopping_list(user_id)
        if not shopping:
            return "I don't have an active shopping list for you. Reply *shopping list* to create one!"
        items = json.loads(shopping.get("items", "[]")) if isinstance(shopping.get("items"), str) else shopping.get("items", [])
        added, not_found = add_ingredients(user_id, items)
        supabase.table("shopping_lists").update({"is_complete": True}).eq("id", shopping["id"]).execute()
        lines = ["🎉 Welcome back! Added to your pantry:"]
        lines += [f"  • {i}" for i in added]
        if not_found:
            lines += ["\n❓ Couldn't find:", *[f"  • {i}" for i in not_found]]
        lines += ["\nReply *cook* to see what you can make now! 🍳"]
        return "\n".join(lines)

    # CREATE AI RECIPE
    if any(p in m for p in ["create recipe", "make me a recipe", "generate recipe", "invent a recipe", "tengeneza recipe"]):
        pantry = get_pantry_names(user_id)
        if not pantry:
            return "😅 Your pantry is empty! Add some ingredients first."
        ai_recipe = generate_ai_recipe(pantry, user, meal_type)
        if ai_recipe:
            return "✨ *I created a recipe just for you!*\n\n" + format_recipe_with_followup(ai_recipe, user_id)
        return "😕 Couldn't generate a recipe right now. Try again in a moment!"

    # NUTRITION
    if any(p in m for p in ["nutrition", "calories", "macros", "health stats", "lishe"]):
        return get_nutrition_summary(user_id)

    # DEFAULT fallback
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
    """Download image from Twilio and encode as base64."""
    try:
        import base64
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        auth = (twilio_sid, twilio_token) if twilio_sid and twilio_token else None
        resp = requests.get(url, auth=auth, timeout=15)
        resp.raise_for_status()
        return base64.standard_b64encode(resp.content).decode("utf-8")
    except Exception as e:
        log.warning(f"Image fetch failed: {e}")
        return None


def analyse_photo_with_claude(image_b64: str, media_type: str, known_ingredients: list[str]) -> dict:
    """Send image to Claude Vision. Returns {ingredients_found, image_type}"""
    if not ANTHROPIC_API_KEY:
        return {"ingredients_found": [], "image_type": "other"}

    known_str = ", ".join(known_ingredients[:100])
    prompt = f"""You are a smart pantry assistant. The user sent an image.

First identify the image type: receipt/shopping list/till slip, fridge/pantry/food storage, or other.

Extract ALL food ingredients, groceries, produce visible. Cross-reference with our known ingredients: {known_str}

Match aliases and brand names:
- "sukuma" -> "sukuma wiki"
- "dhania" -> "coriander"
- "free range eggs 6pk" -> "eggs"
- "Afia tomatoes 400g" -> "tomatoes"
- "uji flour" -> "unga"

Respond ONLY in valid JSON:
{{"image_type": "receipt" | "fridge" | "other", "ingredients_found": ["ingredient1", "ingredient2"]}}"""

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
        text = resp.json()["content"][0]["text"].strip()
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        log.info(f"📸 Photo analysis: {result}")
        return result
    except Exception as e:
        log.warning(f"Photo analysis failed: {e}")
        return {"ingredients_found": [], "image_type": "other"}


def handle_photo(media_url: str, media_type: str, user: dict) -> str:
    """Full photo -> pantry flow with confirmation step."""
    name = user.get("full_name", "Friend")
    user_id = user["id"]

    if not ANTHROPIC_API_KEY:
        return (
            "📸 I can see your photo, but I need an AI key to analyse it.\n\n"
            "For now, just tell me what you have:\n_I have tomatoes, eggs, milk_"
        )

    image_b64 = fetch_image_as_base64(media_url, media_type)
    if not image_b64:
        return "😕 I couldn't download your photo. Please try again or tell me what you have in text!"

    all_ingredients = get_all_ingredient_names()
    result = analyse_photo_with_claude(image_b64, media_type, all_ingredients)
    found = result.get("ingredients_found", [])
    image_type = result.get("image_type", "other")

    if image_type == "other" or not found:
        return (
            f"🤔 I couldn't spot any ingredients in that photo, {name}.\n\n"
            "Try sending a photo of your fridge or shopping receipt.\n\n"
            "Or just type: _I have tomatoes, eggs, garlic_"
        )

    # Store pending ingredients for confirmation
    update_user(user_id, {"pending_photo_ingredients": json.dumps(found)})

    type_emoji = "🧾" if image_type == "receipt" else "🧊"
    type_label = "receipt" if image_type == "receipt" else "fridge/pantry"
    lines = [
        f"{type_emoji} *I analysed your {type_label}!*",
        f"Found {len(found)} ingredient(s):", "",
    ]
    lines += [f"  • {i}" for i in found]
    lines += [
        "",
        "Shall I add all of these to your pantry?", "",
        "✅ Reply *yes* to add them all",
        "❌ Reply *no* to cancel",
        "✏️ Or say what to skip: _yes but skip the milk_",
    ]
    return "\n".join(lines)

# ── Twilio interactive messaging ──────────────────────────────────────────────

def send_buttons(to: str, body_text: str, buttons: list[dict]) -> bool:
    """
    Send a WhatsApp interactive button message via Twilio API.
    buttons = [{"id": "cook", "title": "🍳 Cook"}, ...]
    Max 3 buttons per message. For more, use send_list() instead.
    Returns True on success.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        log.warning("Twilio credentials not set — cannot send buttons")
        return False

    # Twilio Content API for interactive messages
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"

    # Build button payload
    action_buttons = [
        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
        for b in buttons[:3]
    ]

    payload = {
        "From": TWILIO_FROM,
        "To": to,
        "ContentSid": "",  # not using content templates
        "Body": body_text,
        # Interactive buttons via MessagingV2 requires content templates on Twilio
        # Fall back to plain text with numbered options
    }

    # NOTE: Twilio WhatsApp sandbox supports interactive messages only via
    # Content Templates. For sandbox testing we send rich plain text instead.
    # When moving to production WhatsApp Business, replace with Content API calls.
    log.info(f"Button send requested to {to}: {[b['title'] for b in buttons]}")
    return False  # signal to caller to use plain text fallback


def send_message(to: str, text: str):
    """Send a plain text WhatsApp message via Twilio REST API."""
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


def main_menu(name: str) -> str:
    """Return the main menu as rich text with numbered options."""
    return (
        f"Hey {name}! 👋 What would you like to do?\n\n"
        "1️⃣  🍳 *Cook* — Get a recipe suggestion\n"
        "2️⃣  🧺 *Pantry* — View or update ingredients\n"
        "3️⃣  ⭐ *Saved* — Your saved recipes\n"
        "4️⃣  👤 *Profile* — View & edit preferences\n"
        "5️⃣  👋 *Exit* — Close the menu\n\n"
        "_Or just tell me what you have: 'I bought tomatoes and eggs'_\n"
        "_Send a photo of your fridge or receipt 📸_"
    )


def cooking_followup(recipe_name: str) -> str:
    """Ask user if they cooked or used ingredients after a recipe suggestion."""
    return (
        f"Did you end up cooking *{recipe_name}*? 👨‍🍳\n\n"
        "1️⃣  ✅ *Yes, I cooked it* — remove ingredients from pantry\n"
        "2️⃣  🥕 *Used some ingredients* — tell me which ones\n"
        "3️⃣  ❌ *Not yet* — keep pantry as is"
    )


# ── Webhook ────────────────────────────────────────────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    body = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").strip()
    profile_name = request.values.get("ProfileName", "").strip()
    media_url = request.values.get("MediaUrl0", "").strip()
    media_type = request.values.get("MediaContentType0", "").strip()
    num_media = int(request.values.get("NumMedia", "0"))

    log.info(f"📩 From={from_number} | Body={body!r} | Media={num_media}")

    response = MessagingResponse()
    msg_obj = response.message()

    if not from_number:
        return str(response)

    user = get_or_create_user(from_number, profile_name)
    if not user:
        msg_obj.body("⚠️ Could not find or create your account. Please try again.")
        return str(response)

    user_id = user["id"]
    log_message(user_id, "inbound", body or "[photo]")

    # Photo received
    if num_media > 0 and media_url and media_type.startswith("image/"):
        if not user.get("onboarding_complete"):
            reply = "👋 Please finish setting up your profile first! Reply *hi* to continue."
        else:
            reply = handle_photo(media_url, media_type, user)
        msg_obj.body(reply)
        log_message(user_id, "outbound", reply)
        return str(response)

    # Text message
    if not body:
        return str(response)

    if not user.get("onboarding_complete"):
        reply, _ = handle_onboarding(user, body)
    else:
        reply = route(body, user)

    msg_obj.body(reply)
    log_message(user_id, "outbound", reply)
    log.info(f"✅ Replied to {from_number}")

    return str(response)


# ── Health & debug ─────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return {"status": "ok", "bot": "PantryChef"}


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