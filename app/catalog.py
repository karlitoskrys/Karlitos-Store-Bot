"""Catálogos de produtos vendidos pelo bot."""



# ============================================================
# CATÁLOGO
# ============================================================

BLOX_GAMEPASSES = {
    "fruit_storage": {"name": "+1 Fruit Storage", "robux": 400, "emoji": "📦"},
    "fast_boats": {"name": "Fast Boats", "robux": 350, "emoji": "🚤"},
    "boss_drops": {"name": "2x Boss Drops", "robux": 350, "emoji": "🎁"},
    "double_money": {"name": "2x Money", "robux": 450, "emoji": "💰"},
    "double_mastery": {"name": "2x Mastery", "robux": 450, "emoji": "📚"},
    "dark_blade": {"name": "Dark Blade", "robux": 1200, "emoji": "🗡️"},
    "fruit_notifier": {"name": "Fruit Notifier", "robux": 2700, "emoji": "🔔"},
}

BLOX_FRUITS = {
    "rocket": {"name": "Rocket Permanente", "robux": 50, "emoji": "🚀"},
    "spin": {"name": "Spin Permanente", "robux": 75, "emoji": "🌀"},
    "blade": {"name": "Blade Permanente", "robux": 100, "emoji": "⚔️"},
    "spring": {"name": "Spring Permanente", "robux": 180, "emoji": "🌱"},
    "bomb": {"name": "Bomb Permanente", "robux": 220, "emoji": "💣"},
    "smoke": {"name": "Smoke Permanente", "robux": 250, "emoji": "💨"},
    "spike": {"name": "Spike Permanente", "robux": 380, "emoji": "📌"},
    "flame": {"name": "Flame Permanente", "robux": 550, "emoji": "🔥"},
    "eagle": {"name": "Eagle Permanente", "robux": 650, "emoji": "🦅"},
    "ice": {"name": "Ice Permanente", "robux": 750, "emoji": "🧊"},
    "sand": {"name": "Sand Permanente", "robux": 850, "emoji": "🏜️"},
    "dark": {"name": "Dark Permanente", "robux": 950, "emoji": "🌑"},
    "diamond": {"name": "Diamond Permanente", "robux": 1000, "emoji": "💎"},
    "light": {"name": "Light Permanente", "robux": 1100, "emoji": "✨"},
    "rubber": {"name": "Rubber Permanente", "robux": 1200, "emoji": "🟣"},
    "ghost": {"name": "Ghost Permanente", "robux": 1275, "emoji": "👻"},
    "magma": {"name": "Magma Permanente", "robux": 1300, "emoji": "🌋"},
    "quake": {"name": "Quake Permanente", "robux": 1500, "emoji": "🌊"},
    "buddha": {"name": "Buddha Permanente", "robux": 1650, "emoji": "🟡"},
    "love": {"name": "Love Permanente", "robux": 1700, "emoji": "💗"},
    "spider": {"name": "Spider Permanente", "robux": 1800, "emoji": "🕷️"},
    "sound": {"name": "Sound Permanente", "robux": 1900, "emoji": "🎵"},
    "portal": {"name": "Portal Permanente", "robux": 2000, "emoji": "🌀"},
    "phoenix": {"name": "Phoenix Permanente", "robux": 2100, "emoji": "🔥"},
    "rumble": {"name": "Rumble Permanente", "robux": 2250, "emoji": "⚡"},
    "pain": {"name": "Pain Permanente", "robux": 2300, "emoji": "🔴"},
    "mammoth": {"name": "Mammoth Permanente", "robux": 2350, "emoji": "🐘"},
    "trex": {"name": "T-Rex Permanente", "robux": 2350, "emoji": "🦖"},
    "blizzard": {"name": "Blizzard Permanente", "robux": 2400, "emoji": "❄️"},
    "dough": {"name": "Dough Permanente", "robux": 2400, "emoji": "🍩"},
    "shadow": {"name": "Shadow Permanente", "robux": 2425, "emoji": "🌘"},
    "venom": {"name": "Venom Permanente", "robux": 2450, "emoji": "🐍"},
    "gravity": {"name": "Gravity Permanente", "robux": 2500, "emoji": "🪐"},
    "control": {"name": "Control Permanente", "robux": 2500, "emoji": "🔮"},
    "spirit": {"name": "Spirit Permanente", "robux": 2550, "emoji": "👻"},
    "leopard": {"name": "Leopard Permanente", "robux": 3000, "emoji": "🐆"},
    "kitsune": {"name": "Kitsune Permanente", "robux": 4000, "emoji": "🦊"},
    "dragon": {"name": "Dragon Permanente", "robux": 5000, "emoji": "🐉"},
}

GAKURAN_SPINS = {
    f"{amount}_spins": {
        "name": f"{amount} giros",
        "robux": amount * 3,
        "emoji": "🎡",
    }
    for amount in [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
}

ANIME_GAMEPASSES = {
    "vip": {"name": "VIP", "robux": 399, "emoji": "👑"},
    "display_all_units": {"name": "Display All Units", "robux": 399, "emoji": "👁️"},
    "shiny_hunter": {"name": "Shiny Hunter", "robux": 999, "emoji": "✨"},
}

# Preços originais, sem o desconto de 20%.
ANIME_BUNDLES = {
    "villain_1": {"name": "Villain Bundle #1", "robux": 1000, "emoji": "🦹"},
    "villain_2": {"name": "Villain Bundle #2", "robux": 4000, "emoji": "🦹"},
    "villain_3": {"name": "Villain Bundle #3", "robux": 7999, "emoji": "🦹"},
    "release_1": {"name": "Release Bundle #1", "robux": 1000, "emoji": "🚀"},
    "release_2": {"name": "Release Bundle #2", "robux": 4000, "emoji": "🚀"},
    "release_3": {"name": "Release Bundle #3", "robux": 7999, "emoji": "🚀"},
    "release_4": {"name": "Release Bundle #4", "robux": 10000, "emoji": "🚀"},
    "starter": {"name": "Starter Bundle", "robux": 250, "emoji": "⭐"},
}

ANIME_TRAIT_CRYSTALS = {
    "trait_1": {"name": "1 Trait Crystal", "robux": 115, "emoji": "💎"},
    "trait_10": {"name": "10 Trait Crystals", "robux": 1000, "emoji": "💎"},
    "trait_25": {"name": "25 Trait Crystals", "robux": 2400, "emoji": "💎"},
    "trait_50": {"name": "50 Trait Crystals", "robux": 4300, "emoji": "💎"},
    "trait_100": {"name": "100 Trait Crystals", "robux": 8200, "emoji": "💎"},
    "trait_250": {"name": "250 Trait Crystals", "robux": 16000, "emoji": "💎"},
}

ANIME_VILLAIN_COINS = {
    "coins_2500": {"name": "2.500 Villain Coins", "robux": 200, "emoji": "🔴"},
    "coins_7500": {"name": "7.500 Villain Coins", "robux": 450, "emoji": "🔴"},
    "coins_25000": {"name": "25.000 Villain Coins", "robux": 1500, "emoji": "🔴"},
    "coins_75000": {"name": "75.000 Villain Coins", "robux": 4000, "emoji": "🔴"},
}

ANIME_POTIONS = {
    "luck_potion": {"name": "Luck Potion", "robux": 150, "emoji": "🧪"},
    "super_luck_potion": {"name": "Super Luck Potion", "robux": 250, "emoji": "⚗️"},
}

ANIME_GEMS = {
    "gems_1000": {"name": "1.000 Gems", "robux": 200, "emoji": "💠"},
    "gems_2500": {"name": "2.500 Gems", "robux": 450, "emoji": "💠"},
    "gems_5000": {"name": "5.000 Gems", "robux": 800, "emoji": "💠"},
    "gems_10000": {"name": "10.000 Gems", "robux": 1500, "emoji": "💠"},
    "gems_20000": {"name": "20.000 Gems", "robux": 2800, "emoji": "💠"},
    "gems_30000": {"name": "30.000 Gems", "robux": 4000, "emoji": "💠"},
}

ANIME_STAT_PACKS = {
    "stat_reroll_1": {"name": "1 Stat Reroll", "robux": 130, "emoji": "🔷"},
    "stat_reroll_10": {"name": "10 Stat Rerolls", "robux": 250, "emoji": "🔷"},
    "stat_reroll_50": {"name": "50 Stat Rerolls", "robux": 1000, "emoji": "🔷"},
    "stat_lock_1": {"name": "1 Stat Lock", "robux": 130, "emoji": "🔒"},
    "stat_lock_10": {"name": "10 Stat Locks", "robux": 250, "emoji": "🔒"},
    "stat_lock_50": {"name": "50 Stat Locks", "robux": 1000, "emoji": "🔒"},
}

ANIME_EQUIPMENT_PACKS = {
    "equipment_reroll_1": {"name": "1 Equipment Reroll", "robux": 130, "emoji": "🟧"},
    "equipment_reroll_10": {"name": "10 Equipment Rerolls", "robux": 250, "emoji": "🟧"},
    "equipment_reroll_50": {"name": "50 Equipment Rerolls", "robux": 1000, "emoji": "🟧"},
    "equipment_lock_1": {"name": "1 Equipment Lock", "robux": 130, "emoji": "🔐"},
    "equipment_lock_10": {"name": "10 Equipment Locks", "robux": 250, "emoji": "🔐"},
    "equipment_lock_50": {"name": "50 Equipment Locks", "robux": 1000, "emoji": "🔐"},
}
