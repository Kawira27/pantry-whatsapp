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


def find_matching_recipes(pantry_names: list[str], user: dict, meal_type: str = None) -> list[dict]:
    query = supabase.table("recipes").select(
        "id, name, description, instructions, cuisine, meal_type, recipe_ingredients(ingredients(name))"
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
        if not all(i in pantry_names for i in required):
            continue
        if any(a in required for a in allergies):
            continue
        if any(d in recipe["name"].lower() for d in disliked):
            continue
        recipe_cuisine = (recipe.get("cuisine") or "").lower()
        if preferred_cuisines and not open_to_cuisines:
            if recipe_cuisine not in preferred_cuisines and recipe_cuisine != "kenyan":
                continue
        matches.append(recipe)
    return matches


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


def format_recipe(recipe: dict) -> str:
    ingredients = [
        ri["ingredients"]["name"]
        for ri in recipe.get("recipe_ingredients", [])
        if ri.get("ingredients") and ri["ingredients"].get("name")
    ]
    cuisine = recipe.get("cuisine", "")
    meal_type = recipe.get("meal_type", "")
    tag = f"_{cuisine} • {meal_type}_" if cuisine and meal_type else ""
    lines = [f"🍽️ *{recipe['name']}*"]
    if tag:
        lines.append(tag)
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
    lines += ["", f"💾 _save {recipe['name']}_ to save this"]
    lines += ["🔄 Reply *cook* for another suggestion"]
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


# ── Onboarding ─────────────────────────────────────────────────────────────────

def handle_onboarding(user: dict, msg: str) -> tuple[str, bool]:
    step = user.get("onboarding_step", 0)
    user_id = user["id"]

    if step == 0:
        update_user(user_id, {"onboarding_step": 1})
        return (
            "👋 Welcome to *PantryChef*! 🍳\n"
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
            return ("Sawa! 🇰🇪 Tutaendelea kwa Kiswahili.\n\nJina lako ni nani?", False)
        return ("Great! 🇬🇧 We'll continue in English.\n\nWhat's your name?", False)

    if step == 2:
        name = msg.strip().title()
        lang = user.get("language", "en")
        update_user(user_id, {"full_name": name, "onboarding_step": 3})
        if lang == "sw":
            return (f"Karibu, *{name}*! 😊\n\nUna *mzio wowote wa chakula?*\n\ne.g. _karanga, maziwa, gluteni, nguruwe_\nAu andika *hapana*.", False)
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
        matches = find_matching_recipes(pantry, user, meal_type=meal_type)
        if not matches:
            label = f"{meal_type} recipe" if meal_type else "recipe"
            return f"🤔 No {label} matches your pantry right now.\n\nTell me what else you have at home and I'll find something!"
        recipe = random.choice(matches)
        try:
            supabase.table("user_recipe_suggestions").insert({
                "user_id": user_id, "recipe_id": recipe["id"]
            }).execute()
        except Exception:
            pass
        return format_recipe_with_followup(recipe, user_id)

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
