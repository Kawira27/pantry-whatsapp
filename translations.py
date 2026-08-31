"""
PantryChef — Translation strings
All user-facing text in English and Kiswahili.
Usage: t("key", lang) or t("key", lang, name="Sally", count=3)
"""

STRINGS = {
    # ── Main menu ──────────────────────────────────────────────────────────────
    "main_menu": {
        "en": "Hey {name}! 👋 What can I do for you?\n\n"
              "1️⃣  🍳 *Cook* — Get recipe suggestions\n"
              "2️⃣  🧺 *Pantry* — View or update ingredients\n"
              "3️⃣  👤 *Profile* — View & edit preferences\n"
              "4️⃣  ❓ *Help* — See all commands\n"
              "5️⃣  👋 *Exit* — Close the menu\n\n"
              "_Or just tell me what you have: 'I bought tomatoes and eggs'_\n"
              "_Send a photo of your fridge or receipt 📸_",
        "sw": "Habari {name}! 👋 Naweza kukusaidia nini?\n\n"
              "1️⃣  🍳 *Pika* — Pata mapishi\n"
              "2️⃣  🧺 *Pantry* — Angalia viungo vyako\n"
              "3️⃣  👤 *Wasifu* — Mapendeleo yako\n"
              "4️⃣  ❓ *Msaada* — Maagizo yote\n"
              "5️⃣  👋 *Toka* — Funga menyu\n\n"
              "_Au niambie una nini: 'Nimenunua nyanya na mayai'_\n"
              "_Piga picha ya friji au risiti yako 📸_",
    },

    # ── Cook submenu ───────────────────────────────────────────────────────────
    "cook_menu": {
        "en": "What are we cooking today, {name}? 🍳\n\n"
              "1️⃣ 🌅 *Breakfast*\n"
              "2️⃣ ☀️ *Lunch*\n"
              "3️⃣ 🌙 *Dinner*\n"
              "4️⃣ 🍿 *Snack*\n"
              "5️⃣ 🎲 *Surprise me!*\n"
              "6️⃣ ⭐ *Saved recipes*\n"
              "7️⃣ 👋 *Back to menu*",
        "sw": "Tunapika nini leo, {name}? 🍳\n\n"
              "1️⃣ 🌅 *Kiamsha kinywa*\n"
              "2️⃣ ☀️ *Chakula cha mchana*\n"
              "3️⃣ 🌙 *Chakula cha jioni*\n"
              "4️⃣ 🍿 *Vitafunio*\n"
              "5️⃣ 🎲 *Nishangaze!*\n"
              "6️⃣ ⭐ *Mapishi yangu*\n"
              "7️⃣ 👋 *Rudi kwenye menyu*",
    },

    # ── Pantry submenu ─────────────────────────────────────────────────────────
    "pantry_menu": {
        "en": "🧺 *Your Pantry*, {name}\n\n"
              "1️⃣  👀 *View* — See what you have\n"
              "2️⃣  ➕ *Add* — Add ingredients\n"
              "3️⃣  ➖ *Remove* — Remove an ingredient\n"
              "4️⃣  🛒 *Shopping list* — What to buy\n"
              "5️⃣  📸 *Photo* — Scan fridge or receipt\n"
              "6️⃣  👋 *Back* — Back to main menu\n\n"
              "_Or just tell me naturally: 'I bought chicken and tomatoes'_",
        "sw": "🧺 *Pantry yako*, {name}\n\n"
              "1️⃣  👀 *Angalia* — Viungo vilivyopo\n"
              "2️⃣  ➕ *Ongeza* — Ongeza viungo\n"
              "3️⃣  ➖ *Ondoa* — Ondoa kiungo\n"
              "4️⃣  🛒 *Orodha ya manunuzi*\n"
              "5️⃣  📸 *Picha* — Piga picha ya friji/risiti\n"
              "6️⃣  👋 *Rudi* — Rudi kwenye menyu\n\n"
              "_Au niambie moja kwa moja: 'Nimenunua kuku na nyanya'_",
    },

    # ── Profile submenu ────────────────────────────────────────────────────────
    "profile_menu": {
        "en": "👤 *Your Profile*, {name}\n\n"
              "1️⃣  👀 *View profile* — Your preferences\n"
              "2️⃣  ✏️ *Edit* — Update preferences\n"
              "3️⃣  📊 *Nutrition* — Your nutrition summary\n"
              "4️⃣  ⭐ *Saved recipes* — Your favourites\n"
              "5️⃣  👋 *Back* — Back to main menu",
        "sw": "👤 *Wasifu wako*, {name}\n\n"
              "1️⃣  👀 *Angalia wasifu* — Mapendeleo yako\n"
              "2️⃣  ✏️ *Hariri* — Badilisha mapendeleo\n"
              "3️⃣  📊 *Lishe* — Muhtasari wa lishe\n"
              "4️⃣  ⭐ *Mapishi yangu* — Mapishi yaliyohifadhiwa\n"
              "5️⃣  👋 *Rudi* — Rudi kwenye menyu",
    },

    # ── Pantry empty ───────────────────────────────────────────────────────────
    "pantry_empty": {
        "en": "Hey {name}! What do you have at home right now? 🥕\n\n"
              "Just tell me naturally:\n"
              "_\"I have eggs, rice and some tomatoes\"_",
        "sw": "Habari {name}! Una nini nyumbani sasa hivi? 🥕\n\n"
              "Niambie kawaida:\n"
              "_\"Nina mayai, mchele na nyanya\"_",
    },

    # ── No recipe match ────────────────────────────────────────────────────────
    "no_recipe_match": {
        "en": "🤔 I couldn't find anything matching your pantry right now, {name}.\n\n"
              "Tell me what else you have at home and I'll find something!",
        "sw": "🤔 Sikupata mapishi yanayolingana na pantry yako sasa hivi, {name}.\n\n"
              "Niambie una nini kingine nyumbani nami nitapata kitu!",
    },

    # ── Near match intro ───────────────────────────────────────────────────────
    "near_match_intro": {
        "en": "🤔 No perfect {meal_type} match, but these are close:\n",
        "sw": "🤔 Hakuna {meal_type} inayolingana kabisa, lakini hizi ziko karibu:\n",
    },

    # ── Recipe options header ──────────────────────────────────────────────────
    "recipe_options_header": {
        "en": "🍳 *What can you make?*\n",
        "sw": "🍳 *Unaweza kupika nini?*\n",
    },

    "recipe_options_meal_header": {
        "en": "🍳 *{meal_type} Ideas*\n",
        "sw": "🍳 *Mapishi ya {meal_type}*\n",
    },

    "recipe_options_footer": {
        "en": "Reply *1*–*{num}* to see the full recipe!",
        "sw": "Jibu *1*–*{num}* kuona mapishi kamili!",
    },

    # ── Cooking confirmation ───────────────────────────────────────────────────
    "cooking_followup": {
        "en": "Did you end up cooking *{recipe_name}*? 👨‍🍳\n\n"
              "1️⃣  ✅ *Yes, I cooked it* — remove ingredients from pantry\n"
              "2️⃣  🥕 *Used some ingredients* — tell me which ones\n"
              "3️⃣  ❌ *Not yet* — keep pantry as is",
        "sw": "Je, ulipika *{recipe_name}*? 👨‍🍳\n\n"
              "1️⃣  ✅ *Ndiyo, nilipika* — ondoa viungo kwenye pantry\n"
              "2️⃣  🥕 *Nilitumia baadhi* — niambie vipi\n"
              "3️⃣  ❌ *Bado* — acha pantry kama ilivyo",
    },

    # ── Pantry updated ─────────────────────────────────────────────────────────
    "pantry_added_header": {
        "en": "✅ Added to your pantry ({count} items):",
        "sw": "✅ Imeongezwa kwenye pantry yako (vitu {count}):",
    },
    "pantry_already_header": {
        "en": "\n📌 Already in your pantry:",
        "sw": "\n📌 Tayari iko kwenye pantry yako:",
    },
    "pantry_removed_header": {
        "en": "🗑️ Removed from your pantry:",
        "sw": "🗑️ Imeondolewa kwenye pantry yako:",
    },
    "pantry_not_found": {
        "en": "\n❓ Didn't recognise:",
        "sw": "\n❓ Sikutambua:",
    },
    "pantry_not_found_hint": {
        "en": "  _(Try different spelling or add via pantry menu)_",
        "sw": "  _(Jaribu tahajia tofauti au ongeza kupitia menyu ya pantry)_",
    },
    "pantry_not_found_empty": {
        "en": "🤔 Hmm, I couldn't find those ingredients, {name}. Try being more specific!",
        "sw": "🤔 Samahani, sikupata viungo hivyo, {name}. Jaribu kuwa wazi zaidi!",
    },
    "pantry_ready_footer": {
        "en": "🍳 Reply *cook* when you're ready for a recipe!",
        "sw": "🍳 Andika *pika* unapokuwa tayari kwa mapishi!",
    },
    "pantry_ready_with_menu": {
        "en": "Your pantry is ready! Here's what you can do next, {name}:",
        "sw": "Pantry yako iko tayari! Hapa unachoweza kufanya sasa, {name}:",
    },

    # ── Shopping list ──────────────────────────────────────────────────────────
    "shopping_list_header": {
        "en": "🛒 *{name}*\n_{count} item(s)_\n",
        "sw": "🛒 *{name}*\n_Vitu {count}_\n",
    },
    "shopping_list_footer": {
        "en": "Reply *done shopping* when you're back — I'll add everything to your pantry!",
        "sw": "Andika *nimemaliza manunuzi* ukirudi — nitaongeza kila kitu kwenye pantry yako!",
    },
    "shopping_list_full": {
        "en": "🛒 Your pantry already covers most recipes! Reply *cook* to see what you can make.",
        "sw": "🛒 Pantry yako tayari inafunika mapishi mengi! Andika *pika* kuona unachoweza kupika.",
    },
    "done_shopping_header": {
        "en": "🎉 Welcome back! Added to your pantry:",
        "sw": "🎉 Karibu tena! Imeongezwa kwenye pantry yako:",
    },
    "done_shopping_footer": {
        "en": "Reply *cook* to see what you can make now! 🍳",
        "sw": "Andika *pika* kuona unachoweza kupika sasa! 🍳",
    },
    "no_active_shopping_list": {
        "en": "I don't have an active shopping list for you. Reply *shopping list* to create one!",
        "sw": "Sina orodha ya manunuzi inayoendelea kwako. Andika *orodha ya manunuzi* kuunda moja!",
    },

    # ── Saved recipes ──────────────────────────────────────────────────────────
    "saved_empty": {
        "en": "⭐ No saved recipes yet.\n\nAfter getting a recipe reply:\n_save [recipe name]_",
        "sw": "⭐ Bado huna mapishi yaliyohifadhiwa.\n\nBaada ya kupata mapishi andika:\n_hifadhi [jina la mapishi]_",
    },
    "saved_header": {
        "en": "⭐ *Your Saved Recipes:*\n",
        "sw": "⭐ *Mapishi Yako Yaliyohifadhiwa:*\n",
    },
    "saved_footer": {
        "en": "Reply *cook* for a new suggestion!",
        "sw": "Andika *pika* kwa mapendekezo mapya!",
    },
    "saved_success": {
        "en": "💾 *{recipe_name}* saved to your favourites, {name}!\n\n"
              "Find it anytime by typing *saved*.\n\n"
              "What would you like to do next?\n"
              "🍳 *cook* — get another recipe\n"
              "🛒 *shopping list* — top up your pantry\n"
              "📅 *meal prep* — plan your week",
        "sw": "💾 *{recipe_name}* imehifadhiwa kwenye mapishi yako, {name}!\n\n"
              "Ipate wakati wowote kwa kuandika *zilizohifadhiwa*.\n\n"
              "Unataka kufanya nini sasa?\n"
              "🍳 *pika* — pata mapishi mengine\n"
              "🛒 *orodha ya manunuzi* — jaza pantry yako\n"
              "📅 *maandalizi ya wiki* — panga wiki yako",
    },
    "saved_already": {
        "en": "⭐ *{recipe_name}* is already in your saved recipes!",
        "sw": "⭐ *{recipe_name}* tayari iko kwenye mapishi yako yaliyohifadhiwa!",
    },
    "recipe_not_found": {
        "en": "❌ Couldn't find *{recipe_name}*. Try the exact recipe name.",
        "sw": "❌ Sikupata *{recipe_name}*. Jaribu jina halisi la mapishi.",
    },

    # ── Rating ─────────────────────────────────────────────────────────────────
    "rating_prompt": {
        "en": "⭐ How was *{recipe_name}*? Rate it:\n"
              "1️⃣ ⭐ Didn't like it\n"
              "2️⃣ ⭐⭐ It was okay\n"
              "3️⃣ ⭐⭐⭐ Pretty good!\n"
              "4️⃣ ⭐⭐⭐⭐ Really enjoyed it\n"
              "5️⃣ ⭐⭐⭐⭐⭐ Absolutely loved it!\n\n"
              "_Or reply *skip* to skip the rating_",
        "sw": "⭐ *{recipe_name}* ilikuwaje? Ipe alama:\n"
              "1️⃣ ⭐ Sikupenda\n"
              "2️⃣ ⭐⭐ Ilikuwa sawa\n"
              "3️⃣ ⭐⭐⭐ Ilikuwa nzuri!\n"
              "4️⃣ ⭐⭐⭐⭐ Nilipenda sana\n"
              "5️⃣ ⭐⭐⭐⭐⭐ Nilipenda kabisa!\n\n"
              "_Au andika *ruka* kuruka alama_",
    },
    "rating_1": {
        "en": "Thanks for the feedback! We'll find you better recipes next time 🙏",
        "sw": "Asante kwa maoni! Tutakupata mapishi bora mara ijayo 🙏",
    },
    "rating_2": {
        "en": "Thanks! We'll keep improving the suggestions 👍",
        "sw": "Asante! Tutaendelea kuboresha mapendekezo 👍",
    },
    "rating_3": {
        "en": "Glad it was decent! ⭐⭐⭐",
        "sw": "Vizuri ilikuwa nzuri! ⭐⭐⭐",
    },
    "rating_4": {
        "en": "Great to hear you enjoyed it! ⭐⭐⭐⭐ 🎉",
        "sw": "Vizuri ulipenda! ⭐⭐⭐⭐ 🎉",
    },
    "rating_5": {
        "en": "Amazing! So glad you loved *{recipe_name}*! ⭐⭐⭐⭐⭐ 🎉🎉",
        "sw": "Vizuri sana! Furaha sana ulipenda *{recipe_name}*! ⭐⭐⭐⭐⭐ 🎉🎉",
    },
    "rating_skip": {
        "en": "No worries! ",
        "sw": "Sawa! ",
    },

    # ── Cooked confirmation ────────────────────────────────────────────────────
    "cooked_header": {
        "en": "✅ Great cook, {name}! Removed from your pantry:",
        "sw": "✅ Hongera kupika, {name}! Imeondolewa kwenye pantry yako:",
    },
    "not_yet": {
        "en": "👍 No problem! Your pantry stays as is.\n\n",
        "sw": "👍 Sawa! Pantry yako inabaki kama ilivyo.\n\n",
    },
    "used_some": {
        "en": "Which ingredients did you use? Just tell me naturally:\n_'I used the eggs and tomatoes'_",
        "sw": "Viungo vipi ulitumia? Niambie kawaida:\n_'Nilitumia mayai na nyanya'_",
    },

    # ── Goodbye ────────────────────────────────────────────────────────────────
    "goodbye": {
        "en": "👋 Goodbye {name}! Come back when you're hungry 😄\nReply *hi* anytime to get started again.",
        "sw": "👋 Kwa heri {name}! Rudi unapohisi njaa 😄\nAndika *habari* wakati wowote kuanza tena.",
    },

    # ── Default fallback ───────────────────────────────────────────────────────
    "default_fallback": {
        "en": "🤔 I didn't quite get that, {name}.\n\n"
              "You can tell me things like:\n"
              "💬 _\"I have eggs and tomatoes\"_ — to update your pantry\n"
              "💬 _\"I finished the rice\"_ — to remove items\n"
              "💬 _\"cook\"_ — to get a recipe\n\n"
              "Or type *help* to see everything I can do!",
        "sw": "🤔 Sikuelewa vizuri, {name}.\n\n"
              "Unaweza kuniambia mambo kama:\n"
              "💬 _\"Nina mayai na nyanya\"_ — kuboresha pantry yako\n"
              "💬 _\"Nimemaliza mchele\"_ — kuondoa vitu\n"
              "💬 _\"pika\"_ — kupata mapishi\n\n"
              "Au andika *msaada* kuona ninachoweza kufanya!",
    },

    # ── Photo ──────────────────────────────────────────────────────────────────
    "photo_no_api": {
        "en": "📸 I can see your photo, but I need an AI key to analyse it.\n\n"
              "For now, just tell me what you have:\n_I have tomatoes, eggs, milk_",
        "sw": "📸 Naona picha yako, lakini nahitaji ufunguo wa AI kuisoma.\n\n"
              "Kwa sasa, niambie tu una nini:\n_Nina nyanya, mayai, maziwa_",
    },
    "photo_download_fail": {
        "en": "😕 I couldn't download your photo. Please try again or tell me what you have in text!",
        "sw": "😕 Sikuweza kupakua picha yako. Tafadhali jaribu tena au niambie una nini kwa maandishi!",
    },
    "photo_not_food": {
        "en": "🤔 I couldn't spot any ingredients in that photo, {name}.\n\n"
              "Try sending:\n📸 A photo of your fridge/pantry\n🧾 A photo of your shopping receipt\n\n"
              "Or just type: _I have tomatoes, eggs, garlic_",
        "sw": "🤔 Sikuona viungo vyovyote kwenye picha hiyo, {name}.\n\n"
              "Jaribu kutuma:\n📸 Picha ya friji/pantry yako\n🧾 Picha ya risiti yako\n\n"
              "Au andika tu: _Nina nyanya, mayai, vitunguu saumu_",
    },
    "photo_found": {
        "en": "{emoji} *I analysed your {type_label}!*\nFound {count} ingredient(s):\n",
        "sw": "{emoji} *Nimechunguza {type_label} yako!*\nNimepata viungo {count}:\n",
    },
    "photo_confirm": {
        "en": "Shall I add all of these to your pantry?\n\n"
              "✅ Reply *yes* to add them all\n"
              "❌ Reply *no* to cancel\n"
              "✏️ Or say what to skip: _yes but skip the milk_",
        "sw": "Niziongeze zote kwenye pantry yako?\n\n"
              "✅ Andika *ndiyo* kuziongeza zote\n"
              "❌ Andika *hapana* kufuta\n"
              "✏️ Au sema unataka kuruka: _ndiyo lakini ruka maziwa_",
    },

    # ── Nutrition ──────────────────────────────────────────────────────────────
    "nutrition_empty": {
        "en": "📊 No nutrition data yet — cook some recipes first and I'll track your stats!",
        "sw": "📊 Bado hakuna data ya lishe — pika mapishi kwanza nami nitafuatilia takwimu zako!",
    },
    "nutrition_header": {
        "en": "📊 *Your Nutrition Summary*\n_Based on your last {count} meals_\n",
        "sw": "📊 *Muhtasari wa Lishe Yako*\n_Kulingana na milo yako {count} ya mwisho_\n",
    },

    # ── Privacy ────────────────────────────────────────────────────────────────
    "privacy_info": {
        "en": "🔒 *Your Data & Privacy*\n\n"
              "Here's what PantryChef stores about you:\n\n"
              "• Name: {name}\n"
              "• WhatsApp number (your identifier)\n"
              "• Dietary preferences & allergies\n"
              "• Pantry ingredients\n"
              "• Message history\n"
              "• Recipe ratings and saved recipes\n\n"
              "We never sell your data or share it with third parties.\n\n"
              "To delete all your data reply *confirm delete account*.\n"
              "This is permanent and cannot be undone.",
        "sw": "🔒 *Data Yako na Faragha*\n\n"
              "Hapa ndipo PantryChef inayohifadhi kuhusu wewe:\n\n"
              "• Jina: {name}\n"
              "• Nambari ya WhatsApp (kitambulisho chako)\n"
              "• Mapendeleo ya chakula na mizio\n"
              "• Viungo vya pantry\n"
              "• Historia ya ujumbe\n"
              "• Alama za mapishi na mapishi yaliyohifadhiwa\n\n"
              "Hatuuzi data yako wala kushiriki na watu wengine.\n\n"
              "Kufuta data yako yote andika *thibitisha kufuta akaunti*.\n"
              "Hii ni ya kudumu na haiwezi kutengwa.",
    },
    "delete_success": {
        "en": "✅ Your account and all associated data has been permanently deleted.\n\n"
              "We're sorry to see you go. If you ever want to come back, "
              "just send us a message and we'll start fresh. 👋",
        "sw": "✅ Akaunti yako na data zote zimefutwa kabisa.\n\n"
              "Tunasikitika kukuona ukienda. Ukitaka kurudi, "
              "tuma ujumbe tu na tutaanza upya. 👋",
    },
    "delete_fail": {
        "en": "❌ Something went wrong deleting your account. Please try again or contact support.",
        "sw": "❌ Kuna tatizo katika kufuta akaunti yako. Tafadhali jaribu tena au wasiliana na msaada.",
    },

    # ── Create AI recipe ───────────────────────────────────────────────────────
    "ai_recipe_intro": {
        "en": "✨ *I created a recipe just for you!*\n\n",
        "sw": "✨ *Nimeunda mapishi kwa ajili yako tu!*\n\n",
    },
    "ai_recipe_no_key": {
        "en": "✨ AI recipe creation isn't available right now. Reply *cook* for existing recipes!",
        "sw": "✨ Uundaji wa mapishi ya AI haupatikani sasa. Andika *pika* kwa mapishi yaliyopo!",
    },
    "ai_recipe_fail": {
        "en": "😕 Couldn't generate a recipe right now. Try again in a moment!",
        "sw": "😕 Sikuweza kuunda mapishi sasa hivi. Jaribu tena baadaye!",
    },
    "ai_recipe_no_pantry": {
        "en": "😅 Your pantry is empty! Add some ingredients first.",
        "sw": "😅 Pantry yako iko tupu! Ongeza viungo kwanza.",
    },

    # ── Meal prep ──────────────────────────────────────────────────────────────
    "meal_plan_header": {
        "en": "📅 *Your Weekly Meal Plan*\n",
        "sw": "📅 *Mpango Wako wa Chakula wa Wiki*\n",
    },
    "meal_plan_footer": {
        "en": "Add more ingredients to unlock more options!",
        "sw": "Ongeza viungo zaidi kufungua chaguzi zaidi!",
    },
    "meal_plan_no_match": {
        "en": "_(no {meal_type} match)_",
        "sw": "_(hakuna {meal_type} inayolingana)_",
    },
    "meal_plan_empty_pantry": {
        "en": "😅 Your pantry is empty! Add ingredients first:\n_add tomatoes, onions, rice_",
        "sw": "😅 Pantry yako iko tupu! Ongeza viungo kwanza:\n_ongeza nyanya, vitunguu, mchele_",
    },

    # ── Pantry view ────────────────────────────────────────────────────────────
    "pantry_view_header": {
        "en": "🧺 *Your Pantry* ({count} items)\n",
        "sw": "🧺 *Pantry Yako* (vitu {count})\n",
    },
    "pantry_view_footer": {
        "en": "➕ _add [ingredient]_ to add more\n➖ _remove [ingredient]_ to remove",
        "sw": "➕ _ongeza [kiungo]_ kuongeza zaidi\n➖ _ondoa [kiungo]_ kuondoa",
    },
    "pantry_view_empty": {
        "en": "🗑️ Your pantry is empty, {name}!\n\nJust tell me what you have:\n_\"I have eggs, tomatoes and rice\"_",
        "sw": "🗑️ Pantry yako iko tupu, {name}!\n\nNiambie tu una nini:\n_\"Nina mayai, nyanya na mchele\"_",
    },
    "pantry_add_prompt": {
        "en": "What did you get, {name}? Just tell me naturally:\n\n"
              "_\"I bought chicken and tomatoes\"_\n"
              "_\"Nimenunua mayai na unga\"_\n\n"
              "Or send a 📸 photo of your fridge or receipt!",
        "sw": "Ulinunua nini, {name}? Niambie kawaida:\n\n"
              "_\"Nimenunua kuku na nyanya\"_\n"
              "_\"Nina mayai na unga\"_\n\n"
              "Au tuma 📸 picha ya friji au risiti yako!",
    },
    "pantry_remove_prompt": {
        "en": "What would you like to remove from your pantry?\n\n_\"I used up the rice\"_\n_\"Nimemaliza sukuma wiki\"_",
        "sw": "Unataka kuondoa nini kwenye pantry yako?\n\n_\"Nimetumia mchele wote\"_\n_\"Nimemaliza sukuma wiki\"_",
    },
    "pantry_photo_prompt": {
        "en": "📸 Send me a photo of your fridge or shopping receipt and I'll add the ingredients automatically!",
        "sw": "📸 Nitumie picha ya friji yako au risiti ya ununuzi na nitaongeza viungo moja kwa moja!",
    },

    # ── Profile view ───────────────────────────────────────────────────────────
    "profile_view": {
        "en": "👤 *Your Profile*\n\n"
              "🙋 Name: {name}\n"
              "🚫 Allergies: {allergies}\n"
              "❤️ Loves: {liked}\n"
              "👎 Avoids: {disliked}\n"
              "💰 Budget: {budget}\n"
              "🌍 Cuisines: {cuisines}\n"
              "🍳 Cooking style: {style}\n\n"
              "Reply *2* to edit any of these.",
        "sw": "👤 *Wasifu Wako*\n\n"
              "🙋 Jina: {name}\n"
              "🚫 Mizio: {allergies}\n"
              "❤️ Unapenda: {liked}\n"
              "👎 Unaepuka: {disliked}\n"
              "💰 Bajeti: {budget}\n"
              "🌍 Vyakula vya nchi: {cuisines}\n"
              "🍳 Mtindo wa kupika: {style}\n\n"
              "Andika *2* kuhariri yoyote ya haya.",
    },

    # ── Near match ─────────────────────────────────────────────────────────────
    "near_match_card": {
        "en": "🟡 *{name}* _{cuisine} • {meal_type}_\n"
              "You have *{have}/{total}* ingredients\n"
              "Missing: {missing}\n"
              "Reply *shopping list* to add missing items, or *cook anyway* to see the full recipe.",
        "sw": "🟡 *{name}* _{cuisine} • {meal_type}_\n"
              "Una *{have}/{total}* ya viungo\n"
              "Vinakosekana: {missing}\n"
              "Andika *orodha ya manunuzi* au *pika hata hivyo* kuona mapishi kamili.",
    },

    # ── Missing ingredients warning ────────────────────────────────────────────
    "missing_warning": {
        "en": "⚠️ *Your pantry is missing:* {missing}\n_You can still try the recipe or grab these on your next shop!_\n",
        "sw": "⚠️ *Pantry yako inakosa:* {missing}\n_Bado unaweza jaribu mapishi au nunua hivi dukani!_\n",
    },

    # ── Create recipe command prompt ───────────────────────────────────────────
    "create_recipe_intro": {
        "en": "✨ *No existing recipe matched, so I created one just for you!*\n\n",
        "sw": "✨ *Hakuna mapishi yaliyolingana, kwa hivyo nimeunda moja kwa ajili yako!*\n\n",
    },

    # ── Near match create prompt ───────────────────────────────────────────────
    "near_match_create_prompt": {
        "en": "✨ Or reply *create recipe* and I'll make one just for you!",
        "sw": "✨ Au andika *unda mapishi* nami nitaunda moja kwa ajili yako!",
    },
}


def t(key: str, lang: str = "en", **kwargs) -> str:
    """
    Get translated string by key.
    Falls back to English if Swahili not available.
    Formats with kwargs.
    """
    lang = lang if lang in ("en", "sw") else "en"
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or f"[missing: {key}]"
    try:
        return text.format(**kwargs)
    except KeyError:
        return text