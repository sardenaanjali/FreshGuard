from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import date, datetime, timedelta
import json, os, re, urllib.request
import smtplib, threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = 'freshguard_secret_2024'

# ── Always resolve paths relative to this file, not the cwd ──────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Separate folders: one JSON per user ───────────────────────────────────────
USERS_DIR         = os.path.join(BASE_DIR, 'data', 'users')
ITEMS_DIR         = os.path.join(BASE_DIR, 'data', 'items')
CUSTOM_ITEMS_FILE = os.path.join(BASE_DIR, 'data', 'custom_items.json')
BARCODE_DB_FILE   = os.path.join(BASE_DIR, 'data', 'barcode_db.json')

# Create data dirs on startup
os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(ITEMS_DIR, exist_ok=True)

# ── One-time migration from old single-file format ────────────────────────────
def _migrate_legacy():
    legacy_users = os.path.join(BASE_DIR, 'users.json')
    legacy_items = os.path.join(BASE_DIR, 'items.json')
    if os.path.exists(legacy_users):
        try:
            with open(legacy_users) as f:
                old = json.load(f)
            for email, udata in old.items():
                fp = os.path.join(USERS_DIR, _safe(email) + '.json')
                if not os.path.exists(fp):
                    _atomic(fp, udata)
            os.rename(legacy_users, legacy_users + '.migrated')
            print('[FreshGuard] Migrated users.json → data/users/')
        except Exception as e:
            print(f'[FreshGuard] users migration warning: {e}')
    if os.path.exists(legacy_items):
        try:
            with open(legacy_items) as f:
                old = json.load(f)
            for email, items in old.items():
                fp = os.path.join(ITEMS_DIR, _safe(email) + '.json')
                if not os.path.exists(fp):
                    _atomic(fp, items)
            os.rename(legacy_items, legacy_items + '.migrated')
            print('[FreshGuard] Migrated items.json → data/items/')
        except Exception as e:
            print(f'[FreshGuard] items migration warning: {e}')

def _safe(email):
    """Convert email to a safe filename string."""
    return email.replace('@', '_at_').replace('.', '_').replace('+', '_plus_')

def _atomic(path, data):
    """Write JSON atomically — crash-safe, data never corrupted."""
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

_migrate_legacy()

# ══════════════════════════════════════════════════════════════════
#  EMAIL SETUP — fill in these two values to enable expiry alerts
#  ──────────────────────────────────────────────────────────────
#  STEP 1: Use your Gmail address as SENDER_EMAIL
#  STEP 2: Create a Gmail App Password (NOT your real password):
#            → Go to: myaccount.google.com
#            → Security → 2-Step Verification → App passwords
#            → Create one for "Mail", copy the 16-char password
#  STEP 3: Paste it as SENDER_PASS below
#  STEP 4: Restart the app — users will get one email per day
#           when any of their items are expiring or expired
# ══════════════════════════════════════════════════════════════════
SMTP_HOST    = 'smtp.gmail.com'
SMTP_PORT    = 587
SENDER_EMAIL = 'sardenaanjali@gmail.com'
SENDER_PASS  = 'bsmarwzpjtqfixwn'
#
# ↑ Replace 'your_email@gmail.com' and 'your_app_password_here' with real values
#   OR set environment variables FRESHGUARD_EMAIL and FRESHGUARD_PASS
# ══════════════════════════════════════════════════════════════════

# ── Accurate shelf life in days (based on food safety guidelines) ─────────────
SHELF_LIFE = {
    # Dairy
    "Whole Milk": 5, "Skim Milk": 5, "Almond Milk": 7, "Oat Milk": 7, "Soy Milk": 7,
    "Butter": 60, "Salted Butter": 90, "Unsalted Butter": 60,
    "Cream Cheese": 14, "Cheddar Cheese": 21, "Mozzarella Cheese": 7,
    "Parmesan Cheese": 30, "Feta Cheese": 14, "Paneer": 5,
    "Yogurt": 7, "Curd": 7, "Greek Yogurt": 10, "Sour Cream": 14,
    "Heavy Cream": 7, "Whipping Cream": 7,
    "Condensed Milk": 365, "Evaporated Milk": 365, "Ghee": 270,
    # Vegetables
    "Tomato": 6, "Onion": 45, "Potato": 75, "Garlic": 60, "Ginger": 21,
    "Carrot": 14, "Cabbage": 14, "Cauliflower": 7, "Broccoli": 5,
    "Spinach": 4, "Coriander": 4, "Lettuce": 4, "Cucumber": 7,
    "Bell Pepper": 7, "Green Chilli": 7, "Capsicum": 7,
    "Peas": 5, "Corn": 3, "Beetroot": 14, "Radish": 7, "Mushroom": 5,
    "Zucchini": 7, "Eggplant": 7, "Sweet Potato": 21, "Pumpkin": 30,
    "Leek": 7, "Celery": 14, "Asparagus": 4,
    # Fruits
    "Apple": 21, "Banana": 5, "Mango": 5, "Orange": 14, "Grapes": 7,
    "Watermelon": 10, "Pineapple": 5, "Papaya": 5, "Strawberry": 3,
    "Blueberry": 5, "Raspberry": 3, "Lemon": 21, "Lime": 21, "Coconut": 30,
    "Pomegranate": 14, "Kiwi": 7, "Peach": 5, "Pear": 7, "Plum": 5,
    "Cherry": 5, "Avocado": 4, "Guava": 5, "Fig": 3, "Date": 180,
    # Meat & Seafood
    "Chicken Breast": 2, "Chicken Thigh": 2, "Whole Chicken": 2,
    "Mutton": 4, "Lamb": 4, "Beef": 4, "Pork": 3,
    "Bacon": 7, "Sausage": 5, "Salami": 14,
    "Salmon": 2, "Tuna": 2, "Shrimp": 2, "Prawns": 2, "Crab": 2,
    "Lobster": 2, "Tilapia": 2, "Catfish": 2, "Sardines": 2, "Fish": 2,
    "Eggs": 28, "Quail Eggs": 14,
    # Bakery
    "White Bread": 7, "Whole Wheat Bread": 5, "Multigrain Bread": 5,
    "Sourdough Bread": 5, "Baguette": 2, "Pita Bread": 5, "Naan": 3,
    "Croissant": 2, "Muffin": 3, "Bagel": 5, "Dinner Rolls": 3,
    "Cake": 5, "Cookies": 150, "Biscuits": 150, "Brownie": 5,
    "Donut": 2, "Pastry": 3, "Pizza Base": 5, "Tortilla": 7,
    # Snacks
    "Potato Chips": 90, "Popcorn": 30, "Crackers": 120, "Pretzels": 60,
    "Granola Bar": 90, "Protein Bar": 180, "Rice Cakes": 90,
    "Nuts Mix": 60, "Almonds": 90, "Cashews": 60, "Peanuts": 60,
    "Walnuts": 60, "Pistachios": 90, "Raisins": 180, "Dried Mango": 180,
    "Dark Chocolate": 365, "Milk Chocolate": 180, "Candy": 365, "Gummy Bears": 365,
    # Beverages
    "Orange Juice": 7, "Apple Juice": 7, "Mango Juice": 7, "Coconut Water": 5,
    "Sparkling Water": 365, "Green Tea": 730, "Black Tea": 730, "Coffee": 365,
    "Energy Drink": 365, "Sports Drink": 365, "Lemonade": 7, "Iced Tea": 7,
    "Smoothie": 3, "Protein Shake": 3, "Soda": 270, "Tonic Water": 365,
    "Instant Noodles": 210,
    # Frozen
    "Frozen Peas": 365, "Frozen Corn": 365, "Frozen Broccoli": 365, "Frozen Spinach": 365,
    "Frozen Pizza": 90, "Frozen Fries": 180, "Frozen Burger Patty": 90,
    "Frozen Fish Fillet": 90, "Frozen Shrimp": 90, "Frozen Waffles": 90,
    "Ice Cream": 60, "Frozen Yogurt": 60, "Frozen Dumplings": 90, "Frozen Paratha": 90,
    # Condiments & Oils
    "Tomato Ketchup": 180, "Mustard": 365, "Mayonnaise": 60, "Hot Sauce": 365,
    "Soy Sauce": 730, "Worcestershire Sauce": 730, "Barbecue Sauce": 180,
    "Salsa": 14, "Guacamole": 3, "Hummus": 7,
    "Peanut Butter": 210, "Jam": 180, "Honey": 730, "Maple Syrup": 365,
    "Olive Oil": 365, "Cooking Oil": 270, "Coconut Oil": 730,
    "Vinegar": 1460, "Lemon Juice": 7, "Pickle": 270,
    # Flours
    "All-Purpose Flour": 365, "Maida": 240, "Refined Flour": 240,
    "Whole Wheat Flour": 150, "Atta": 150, "Wheat Flour": 150,
    "Bread Flour": 365, "Cake Flour": 365, "Self-Rising Flour": 365,
    "Rice Flour": 365, "Almond Flour": 90, "Coconut Flour": 180,
    "Corn Flour": 365, "Oat Flour": 90, "Chickpea Flour": 180,
    "Rye Flour": 180, "Barley Flour": 180, "Tapioca Flour": 365,
    "Semolina Flour": 365, "Spelt Flour": 180, "Buckwheat Flour": 90,
    "Quinoa Flour": 90, "Sorghum Flour": 180, "Millet Flour": 90,
    # Grains & Pulses
    "White Rice": 548, "Brown Rice": 150, "Basmati Rice": 548,
    "Jasmine Rice": 548, "Oats": 365, "Quinoa": 730, "Barley": 365,
    "Millet": 365, "Corn Meal": 365,
    "Lentils (Red)": 270, "Lentils (Green)": 270, "Lentils": 270,
    "Dal": 270, "Moong Dal": 270, "Toor Dal": 270, "Urad Dal": 270,
    "Chana Dal": 270, "Masoor Dal": 270,
    "Chickpeas": 730, "Black Beans": 730, "Kidney Beans": 730, "Soybean": 365,
    "Pasta": 548, "Dry Pasta": 548,
    "Noodles": 210, "Vermicelli": 365,
    "Breakfast Cereal": 270, "Cornflakes": 270,
    "Canned Vegetables": 1095, "Canned Beans": 1095,
    "Canned Tomatoes": 1095, "Canned Corn": 1095,
    "Vegetable Broth": 365, "Chicken Broth": 365,
    # Spices & Herbs
    "Turmeric": 365, "Cumin": 365, "Coriander Powder": 365,
    "Red Chilli Powder": 365, "Black Pepper": 365, "Garam Masala": 270,
    "Cinnamon": 730, "Cardamom": 365, "Cloves": 730,
    "Bay Leaves": 365, "Oregano": 730, "Basil": 365,
    "Thyme": 730, "Rosemary": 730, "Paprika": 365, "Nutmeg": 730,
    "Saffron": 730, "Fennel Seeds": 730, "Mustard Seeds": 730, "Salt": 1825,
    # Other
    "Baking Powder": 365, "Baking Soda": 365, "Yeast": 120,
    "Sugar": 730, "Brown Sugar": 730, "Powdered Sugar": 730,
    "Vanilla Extract": 1460, "Cocoa Powder": 365, "Gelatin": 365,
    "Cornstarch": 730, "Breadcrumbs": 180, "Cooking Spray": 365,
}

# ── Smart keyword matcher for barcode-scanned products ────────────────────────
KEYWORD_SHELF_LIFE = [
    (["milk"],                                              5),
    (["butter"],                                            60),
    (["paneer"],                                            5),
    (["cheese"],                                            14),
    (["yogurt", "curd", "dahi"],                           7),
    (["cream"],                                             7),
    (["ghee"],                                              270),
    (["chicken"],                                           2),
    (["fish", "salmon", "tuna", "prawn", "shrimp"],        2),
    (["mutton", "beef", "lamb", "pork", "meat"],           4),
    (["egg"],                                               28),
    (["basmati", "jasmine"],                                548),
    (["brown rice"],                                        150),
    (["white rice", "rice"],                                548),
    (["atta", "wheat flour"],                               150),
    (["maida", "refined flour"],                            240),
    (["pasta", "spaghetti", "penne", "macaroni"],          548),
    (["maggi", "instant noodle"],                           210),
    (["noodle"],                                            210),
    (["oat", "cornflake", "cereal", "muesli"],             270),
    (["dal", "lentil", "moong", "toor", "urad", "chana", "masoor"], 270),
    (["rajma", "kidney bean", "chickpea", "black bean"],   730),
    (["bread", "roti"],                                     5),
    (["biscuit", "cookie", "cracker"],                      150),
    (["cake", "muffin", "brownie", "donut"],                5),
    (["juice"],                                             7),
    (["tea"],                                               730),
    (["coffee"],                                            365),
    (["water"],                                             365),
    (["cola", "pepsi", "sprite", "fanta", "soda"],         270),
    (["flour"],                                             365),
    (["ketchup", "sauce"],                                  180),
    (["pickle", "achaar"],                                  270),
    (["oil"],                                               270),
    (["honey"],                                             730),
    (["jam", "jelly"],                                      180),
    (["peanut butter"],                                     210),
    (["masala", "turmeric", "cumin", "spice", "chilli powder"], 365),
    (["chip", "crisps", "kurkure", "namkeen", "popcorn"],  90),
    (["chocolate"],                                         180),
    (["candy", "sweet", "gummy", "lollipop"],              365),
    (["canned", "tin"],                                     1095),
    (["frozen"],                                            180),
]

def smart_shelf_life(product_name):
    """Intelligently match a scanned product name to its shelf life in days"""
    name_lower = product_name.lower()
    # 1. Exact match
    for key, days in SHELF_LIFE.items():
        if key.lower() == name_lower:
            return days
    # 2. Partial exact key match
    for key, days in SHELF_LIFE.items():
        if key.lower() in name_lower:
            return days
    # 3. Keyword match
    for keywords, days in KEYWORD_SHELF_LIFE:
        if any(kw in name_lower for kw in keywords):
            return days
    # 4. Default
    return 180

GROCERY_ITEMS = {
    "Dairy": ["Whole Milk","Skim Milk","Almond Milk","Oat Milk","Soy Milk","Butter","Salted Butter","Unsalted Butter","Cream Cheese","Cheddar Cheese","Mozzarella Cheese","Parmesan Cheese","Feta Cheese","Paneer","Yogurt","Curd","Greek Yogurt","Sour Cream","Heavy Cream","Whipping Cream","Condensed Milk","Evaporated Milk","Ghee"],
    "Vegetables": ["Tomato","Onion","Potato","Garlic","Ginger","Carrot","Cabbage","Cauliflower","Broccoli","Spinach","Coriander","Lettuce","Cucumber","Bell Pepper","Green Chilli","Capsicum","Peas","Corn","Beetroot","Radish","Mushroom","Zucchini","Eggplant","Sweet Potato","Pumpkin","Leek","Celery","Asparagus"],
    "Fruits": ["Apple","Banana","Mango","Orange","Grapes","Watermelon","Pineapple","Papaya","Strawberry","Blueberry","Raspberry","Lemon","Lime","Coconut","Pomegranate","Kiwi","Peach","Pear","Plum","Cherry","Avocado","Guava","Fig","Date"],
    "Meat & Seafood": ["Chicken Breast","Chicken Thigh","Whole Chicken","Mutton","Lamb","Beef","Pork","Fish","Bacon","Sausage","Salami","Salmon","Tuna","Shrimp","Prawns","Crab","Lobster","Tilapia","Catfish","Sardines","Eggs","Quail Eggs"],
    "Bakery": ["White Bread","Whole Wheat Bread","Multigrain Bread","Sourdough Bread","Baguette","Pita Bread","Naan","Croissant","Muffin","Bagel","Dinner Rolls","Cake","Cookies","Biscuits","Brownie","Donut","Pastry","Pizza Base","Tortilla"],
    "Snacks": ["Potato Chips","Popcorn","Crackers","Pretzels","Granola Bar","Protein Bar","Rice Cakes","Nuts Mix","Almonds","Cashews","Peanuts","Walnuts","Pistachios","Raisins","Dried Mango","Dark Chocolate","Milk Chocolate","Candy","Gummy Bears"],
    "Beverages": ["Orange Juice","Apple Juice","Mango Juice","Coconut Water","Sparkling Water","Green Tea","Black Tea","Coffee","Energy Drink","Sports Drink","Lemonade","Iced Tea","Smoothie","Protein Shake","Soda","Tonic Water"],
    "Frozen": ["Frozen Peas","Frozen Corn","Frozen Broccoli","Frozen Spinach","Frozen Pizza","Frozen Fries","Frozen Burger Patty","Frozen Fish Fillet","Frozen Shrimp","Frozen Waffles","Ice Cream","Frozen Yogurt","Frozen Dumplings","Frozen Paratha"],
    "Condiments & Sauces": ["Tomato Ketchup","Mustard","Mayonnaise","Hot Sauce","Soy Sauce","Worcestershire Sauce","Barbecue Sauce","Salsa","Guacamole","Hummus","Peanut Butter","Jam","Honey","Maple Syrup","Olive Oil","Cooking Oil","Coconut Oil","Vinegar","Lemon Juice","Pickle"],
    "Flours": ["All-Purpose Flour","Maida","Whole Wheat Flour","Atta","Bread Flour","Cake Flour","Self-Rising Flour","Rice Flour","Almond Flour","Coconut Flour","Corn Flour","Oat Flour","Chickpea Flour","Rye Flour","Barley Flour","Tapioca Flour","Semolina Flour","Spelt Flour","Buckwheat Flour","Quinoa Flour","Sorghum Flour","Millet Flour"],
    "Grains & Pulses": ["White Rice","Brown Rice","Basmati Rice","Jasmine Rice","Oats","Quinoa","Barley","Millet","Corn Meal","Lentils (Red)","Lentils (Green)","Dal","Moong Dal","Toor Dal","Urad Dal","Chana Dal","Masoor Dal","Chickpeas","Black Beans","Kidney Beans","Soybean","Pasta","Dry Pasta","Instant Noodles","Noodles","Vermicelli","Breakfast Cereal","Cornflakes","Canned Vegetables","Canned Beans","Canned Tomatoes","Canned Corn"],
    "Spices & Herbs": ["Turmeric","Cumin","Coriander Powder","Red Chilli Powder","Black Pepper","Garam Masala","Cinnamon","Cardamom","Cloves","Bay Leaves","Oregano","Basil","Thyme","Rosemary","Paprika","Nutmeg","Saffron","Fennel Seeds","Mustard Seeds","Salt"],
    "Other": ["Baking Powder","Baking Soda","Yeast","Sugar","Brown Sugar","Powdered Sugar","Vanilla Extract","Cocoa Powder","Gelatin","Cornstarch","Breadcrumbs","Cooking Spray","Vegetable Broth","Chicken Broth"]
}

RECIPE_SUGGESTIONS = {
    # ── DAIRY ─────────────────────────────────────────────────────────────────
    "Whole Milk":             ["Creamy Pasta","Milk Rice Pudding","Homemade Paneer","Bechamel Sauce","Hot Chocolate"],
    "Amul Taaza Milk":        ["Masala Chai","Homemade Paneer","Kheer","Hot Chocolate","Creamy Oats"],
    "Amul Gold Milk":         ["Rabri","Basundi","Shrikhand","Kulfi","Rich Kheer"],
    "Mother Dairy Full Cream Milk": ["Doodh Peda","Malai Kofta","Paneer from Scratch","Creamy Dal","Milk Halwa"],
    "Paneer":                 ["Palak Paneer","Paneer Butter Masala","Paneer Tikka","Paneer Bhurji","Paneer Paratha"],
    "Amul Paneer":            ["Kadai Paneer","Shahi Paneer","Paneer Lababdar","Paneer Chilli","Paneer Roll"],
    "Mother Dairy Paneer":    ["Paneer Makhani","Paneer Pulao","Paneer Sandwich","Paneer Tikka Masala","Matar Paneer"],
    "Curd":                   ["Raita","Lassi","Curd Rice","Kadhi","Dahi Vada"],
    "Amul Dahi":              ["Boondi Raita","Sweet Lassi","Dahi Puri","Dahi Aloo","Shrikhand"],
    "Mother Dairy Dahi":      ["Vegetable Raita","Mango Lassi","Curd Rice","Kadhi Pakora","Dahi Chaat"],
    "Yogurt":                 ["Raita","Lassi","Tzatziki Dip","Yogurt Parfait","Chicken Marinade"],
    "Greek Yogurt":           ["Tzatziki","Yogurt Parfait","Smoothie Bowl","Yogurt Dip","Frozen Yogurt"],
    "Butter":                 ["Garlic Butter Toast","Butter Chicken","Shortbread Cookies","Butter Pasta","Hollandaise Sauce"],
    "Amul Butter":            ["Butter Naan","Makhan Toast","Butter Chicken","Garlic Bread","Dal Makhani"],
    "Amul Salted Butter":     ["Grilled Cheese Sandwich","Butter Popcorn","Sautéed Vegetables","Garlic Bread","Shortbread"],
    "Ghee":                   ["Dal Tadka","Halwa","Biryani","Chapati","Gajar Halwa"],
    "Amul Ghee":              ["Panchmel Dal","Moong Dal Halwa","Biryani Rice","Poori","Gajar Ka Halwa"],
    "Amul Processed Cheese":  ["Cheese Toast","Mac and Cheese","Cheesy Pasta","Pizza","Grilled Sandwich"],
    "Amul Cheese Slices":     ["Club Sandwich","Cheese Burger","Grilled Cheese","Cheese Omelette","Cheese Paratha"],
    "Amul Cheese Spread":     ["Bread Spread","Cheese Dip","Pasta Sauce","Cheese Sandwich","Stuffed Toast"],
    "Amul Fresh Cream":       ["Pasta in White Sauce","Cream Soup","Panacotta","Whipped Cream Dessert","Creamy Curry"],
    "Amul Lassi":             ["Serve Chilled","Lassi Float","Mango Lassi Blend","Paired with Paratha","Summer Cooler"],
    "Eggs":                   ["Scrambled Eggs","Omelette","Egg Curry","Frittata","Egg Fried Rice"],
    # ── NOODLES & PASTA ───────────────────────────────────────────────────────
    "Maggi 2-Minute Masala Noodles": ["Maggi with Veggies","Egg Maggi","Masala Maggi","Maggi Soup","Cheesy Maggi"],
    "Maggi Masala Noodles Family Pack": ["Noodle Stir Fry","Maggi Pulao","Veggie Maggi","Maggi Frittata","Masala Noodle Bowl"],
    "Maggi Chicken Noodles":  ["Chicken Noodle Soup","Stir Fry Noodles","Egg Noodles","Spicy Noodle Bowl","Noodle Omelette"],
    "Maggi Atta Noodles":     ["Healthy Veggie Noodles","Atta Noodle Soup","Stir Fry","Noodle Upma","Egg Atta Noodles"],
    "Yippee Noodles Magic Masala": ["Spicy Yippee","Veggie Noodles","Egg Yippee","Noodle Soup","Masala Noodle Stir Fry"],
    "Ching's Secret Hakka Noodles": ["Veg Hakka Noodles","Chicken Hakka Noodles","Schezwan Noodles","Noodle Stir Fry","Egg Hakka"],
    "Ching's Secret Schezwan Noodles": ["Schezwan Noodles","Spicy Stir Fry","Hakka Noodles","Schezwan Fried Rice","Dragon Noodles"],
    "Bambino Vermicelli":     ["Semiya Payasam","Upma","Vermicelli Pulao","Sweet Vermicelli","Semiya Kheer"],
    "Bambino Instant Vermicelli": ["Quick Semiya Upma","Vermicelli Kheer","Instant Pulao","Semiya Biryani","Veggie Vermicelli"],
    "Weikfield Pasta Penne":  ["Penne Arrabbiata","Pasta Bake","Penne Pasta Salad","Pasta in White Sauce","Masala Penne"],
    "Weikfield Pasta Spaghetti": ["Spaghetti Bolognese","Aglio e Olio","Tomato Spaghetti","Pasta Carbonara","Masala Spaghetti"],
    "Del Monte Penne Pasta":  ["Penne in Red Sauce","Pasta Primavera","Baked Pasta","Cheesy Penne","Pesto Pasta"],
    # ── BREAD & BAKERY ────────────────────────────────────────────────────────
    "White Bread":            ["French Toast","Bread Upma","Bread Pakoda","Croutons","Bread Pudding"],
    "Britannia Whole Wheat Bread": ["Avocado Toast","Whole Wheat Sandwich","French Toast","Bread Upma","Grilled Sandwich"],
    "Britannia White Sandwich Bread": ["Club Sandwich","Bread Rolls","French Toast","Bread Pakoda","Croutons"],
    "Bonn Multigrain Bread":  ["Healthy Sandwich","Multigrain Toast","Avocado Toast","Grilled Veggie Sandwich","Bread Salad"],
    "English Oven Sandwich Bread": ["Grilled Sandwich","Club Sandwich","Bread Omelette","Bread Upma","Sandwich Toast"],
    # ── BISCUITS ──────────────────────────────────────────────────────────────
    "Parle-G Glucose Biscuits": ["Biscuit Cake","Parle-G Milkshake","No-Bake Biscuit Pudding","Tea Biscuits","Biscuit Crumble"],
    "Britannia Good Day Butter Biscuits": ["Biscuit Ice Cream Sandwich","Biscuit Crumble","Tea Snack","Biscuit Cake","Crushed Biscuit Base"],
    "Britannia Marie Gold Biscuits": ["Marie Biscuit Cake","Cheesecake Base","Biscuit Pudding","Tea Dip","No-Bake Bars"],
    "Britannia Bourbon Biscuits": ["Chocolate Biscuit Cake","Bourbon Milkshake","Biscuit Truffle","Ice Cream Sandwich","Biscuit Pudding"],
    "Cadbury Oreo Biscuits":  ["Oreo Milkshake","Oreo Ice Cream","Oreo Cheesecake","Oreo Cake","Oreo Truffles"],
    # ── RICE & GRAINS ─────────────────────────────────────────────────────────
    "Fortune Basmati Rice":   ["Biryani","Pulao","Fried Rice","Lemon Rice","Curd Rice"],
    "India Gate Basmati Rice Classic": ["Hyderabadi Biryani","Jeera Rice","Peas Pulao","Tomato Rice","Fried Rice"],
    "Daawat Traditional Basmati Rice": ["Chicken Biryani","Veg Pulao","Coconut Rice","Sambar Rice","Steam Rice with Dal"],
    "Kohinoor Basmati Rice":  ["Mutton Biryani","Matar Pulao","Saffron Rice","Lemon Rice","Egg Fried Rice"],
    "Fortune Atta":           ["Chapati","Paratha","Poori","Thepla","Wheat Halwa"],
    "Aashirvaad Atta":        ["Soft Chapati","Aloo Paratha","Missi Roti","Puran Poli","Bati"],
    "Pillsbury Chakki Fresh Atta": ["Chapati","Phulka","Wheat Paratha","Thepla","Wheat Pizza Base"],
    "Aashirvaad Besan":       ["Besan Chilla","Kadhi","Pakoda","Mysore Pak","Besan Halwa"],
    "Fortune Suji / Semolina": ["Upma","Rava Idli","Sooji Halwa","Rava Dosa","Semolina Cake"],
    # ── DALS & PULSES ─────────────────────────────────────────────────────────
    "Dal":                    ["Dal Tadka","Dal Makhani","Dal Soup","Sambar","Khichdi"],
    "Moong Dal":              ["Moong Dal Khichdi","Moong Dal Soup","Pesarattu","Moong Dal Halwa","Dal Tadka"],
    "Tata Sampann Toor Dal":  ["Dal Tadka","Sambar","Gujarati Dal","Toor Dal Khichdi","Dal Fry"],
    "Tata Sampann Chana Dal": ["Chana Dal Tadka","Dal Baati","Chana Dal Halwa","Dal Khichdi","Chana Dal Soup"],
    "Tata Sampann Moong Dal": ["Moong Dal Soup","Khichdi","Moong Dal Halwa","Pesarattu","Green Moong Sprouts Salad"],
    "Tata Sampann Urad Dal":  ["Dal Makhani","Medu Vada","Idli Batter","Urad Dal Khichdi","Urad Dal Soup"],
    "Laxmi Toor Dal":         ["Sambar","Dal Tadka","Gujarati Dal","Toor Dal Khichdi","Dal Fry"],
    "Laxmi Chana Dal":        ["Chana Dal Curry","Dal Halwa","Dal Baati","Chana Dal Soup","Khichdi"],
    # ── READY-TO-EAT MIXES ────────────────────────────────────────────────────
    "MTR Upma Mix":           ["Classic Upma","Upma with Vegetables","Upma with Coconut Chutney","Upma Cutlets","Loaded Upma"],
    "MTR Rava Idli Mix":      ["Rava Idli with Sambar","Rava Idli with Chutney","Mini Rava Idli","Masala Rava Idli","Rava Idli Fry"],
    "MTR Masala Oats":        ["Masala Oats Bowl","Oats Upma","Oats with Veggies","Oats Porridge","Oats Khichdi"],
    "MTR Dosa Mix":           ["Plain Dosa","Masala Dosa","Onion Dosa","Set Dosa","Crispy Dosa"],
    "MTR Idli Mix":           ["Soft Idli","Masala Idli Fry","Mini Idlis","Idli Sambar","Idli Upma"],
    "MTR Dal Makhani":        ["Dal Makhani with Naan","Dal Makhani Rice","Dal Makhani Paratha","Creamy Dal Bowl","Loaded Rice Bowl"],
    "MTR Palak Paneer":       ["Palak Paneer with Roti","Palak Rice","Palak Paneer Paratha","Palak Pasta","Saag Paneer"],
    "MTR Shahi Paneer":       ["Shahi Paneer Biryani","Shahi Paneer with Naan","Paneer Pulao","Paneer Wrap","Rich Paneer Curry"],
    "Gits Dosa Mix":          ["Crispy Dosa","Masala Dosa","Onion Dosa","Ghee Dosa","Set Dosa"],
    "Gits Idli Mix":          ["Soft Idli","Idli Sambar","Mini Idli","Masala Idli","Idli Upma"],
    "Gits Dhokla Mix":        ["Steamed Dhokla","Dhokla Sandwich","Fried Dhokla","Dhokla Chaat","Masala Dhokla"],
    "Gits Gulab Jamun Mix":   ["Classic Gulab Jamun","Gulab Jamun Ice Cream","Gulab Jamun Cheesecake","Gulab Jamun Shake","Stuffed Gulab Jamun"],
    "Ashoka Ready Dal Makhani": ["Dal Makhani with Rice","Dal Makhani Wrap","Dal Makhani Naan","Loaded Dal Bowl","Quick Dal Dinner"],
    "Ashoka Ready Chana Masala": ["Chana Puri","Chana Masala Rice","Chana Wrap","Chana Bhatura","Chana Salad"],
    "Ashoka Ready Palak Paneer": ["Palak Paneer Roti","Palak Pulao","Palak Paratha","Palak Sandwich","Palak Bowl"],
    "Kitchens of India Butter Chicken Paste": ["Butter Chicken","Butter Chicken Naan Pizza","Butter Chicken Wrap","Butter Chicken Rice","Butter Chicken Pasta"],
    # ── SPICES & CONDIMENTS ───────────────────────────────────────────────────
    "Maggi Tomato Ketchup":   ["Dipping Sauce","Pizza Sauce","Pasta Sauce","Sandwich Spread","French Fries Dip"],
    "Kissan Tomato Ketchup":  ["Burger Sauce","Pasta Base","Dipping Sauce","Sandwich Spread","Scrambled Egg Topping"],
    "Kissan Mixed Fruit Jam": ["Jam Toast","Jam Sandwich","Jam Rolls","Jam Cake Filling","Bread Jam"],
    "Dabur Honey":            ["Honey Lemon Tea","Honey Yogurt","Honey Cake","Baklava","Salad Dressing"],
    "Nutella Hazelnut Spread": ["Nutella Toast","Nutella Pancakes","Nutella Crepes","Nutella Milkshake","Nutella Cookies"],
    "Saffola Peanut Butter Crunchy": ["Peanut Butter Toast","Peanut Butter Smoothie","Peanut Butter Cookies","Peanut Sauce","Energy Balls"],
    "Veeba Burger Sauce":     ["Burger","Sandwich Spread","Wrap Sauce","Fries Dip","Club Sandwich"],
    "Ching's Secret Schezwan Sauce": ["Schezwan Fried Rice","Schezwan Noodles","Schezwan Chicken","Schezwan Paneer","Schezwan Sandwich"],
    "Mapro Strawberry Jam":   ["Jam Toast","Jam Sandwich","Strawberry Cake","Jam Crepes","Jam Tarts"],
    "Mapro Rose Syrup":       ["Rose Milk","Rose Lemonade","Rose Lassi","Rose Sharbat","Rose Ice Cream"],
    "Fortune Sunflower Oil":  ["Stir Fry","Deep Fry","Salad Dressing","Sautéed Veggies","Tadka"],
    "Borges Extra Virgin Olive Oil": ["Pasta","Salad Dressing","Bruschetta","Hummus","Grilled Veggies"],
    # ── SNACKS ────────────────────────────────────────────────────────────────
    "Lays Classic Salted Chips": ["Chips Sandwich","Crushed Topping","Nachos Dip","Quick Snack","Chips Chaat"],
    "Lays Magic Masala Chips": ["Masala Chips Bowl","Spicy Nachos","Chips Bhel","Party Snack","Chips Sandwich"],
    "Kurkure Masala Munch":   ["Kurkure Chaat","Kurkure Mix","Spicy Snack Bowl","Kurkure Sandwich","Quick Munch"],
    "Haldiram Aloo Bhujia":   ["Bhujia Chaat","Bhujia Sandwich","Snack Mix","Bhujia Poha","Bhujia Salad Topping"],
    "Haldiram Moong Dal":     ["Moong Dal Snack Mix","Dal Chaat","Snack Bowl","Party Mix","Dal Bhel"],
    "Act II Butter Popcorn":  ["Popcorn Mix","Spicy Popcorn","Caramel Popcorn Balls","Movie Snack","Popcorn Trail Mix"],
    "Cadbury Dairy Milk":     ["Chocolate Cake","Hot Chocolate","Chocolate Mousse","Chocolate Bark","Chocolate Milkshake"],
    "Cadbury 5 Star":         ["Chocolate Milkshake","Dessert Topping","Chocolate Fondue","5 Star Smoothie","Crushed Topping"],
    "KitKat Chocolate":       ["KitKat Milkshake","KitKat Cheesecake","Chocolate Bark","KitKat Ice Cream","Crushed Topping"],
    "Ferrero Rocher":         ["Chocolate Truffles","Festive Gift Box","Chocolate Mousse","Chocolate Cake Topping","Dessert Platter"],
    "Lijjat Urad Papad":      ["Roasted Papad","Fried Papad","Papad Roll","Papad Chaat","Papad Sabzi"],
    "Lijjat Masala Papad":    ["Masala Papad","Papad Chaat","Roasted Papad","Papad with Dal","Masala Snack"],
    # ── BEVERAGES ─────────────────────────────────────────────────────────────
    "Tropicana Orange Juice": ["Orange Smoothie","Juice Mocktail","Orange Cake","Marinade","Juice Popsicle"],
    "Dabur Real Orange Juice": ["Juice Smoothie","Orange Mocktail","Orange Popsicle","Juice Marinade","Orange Punch"],
    "Dabur Real Mango Juice": ["Mango Smoothie","Mango Lassi","Mango Popsicle","Aamras","Mango Punch"],
    "Frooti Mango Drink":     ["Mango Float","Frooti Punch","Mango Ice Cream Float","Mango Mocktail","Frooti Popsicle"],
    "Horlicks Classic Malt":  ["Horlicks Cake","Malt Milkshake","Horlicks Cookies","Malt Smoothie","Horlicks Ladoo"],
    "Bournvita Chocolate Health Drink": ["Chocolate Milkshake","Bournvita Cake","Malt Smoothie","Chocolate Cookies","Bournvita Ladoo"],
    "Nescafe Classic Coffee": ["Dalgona Coffee","Cold Coffee","Coffee Cake","Tiramisu","Coffee Smoothie"],
    "Nescafe Gold Coffee":    ["Filter Coffee Style","Cold Coffee","Coffee Panna Cotta","Affogato","Coffee Cake"],
    "Bru Instant Coffee":     ["South Indian Coffee","Cold Coffee","Coffee Milkshake","Coffee Pudding","Café au Lait"],
    "Brooke Bond Red Label Tea": ["Masala Chai","Ginger Tea","Kadak Chai","Iced Tea","Tea Cake"],
    "Tata Tea Premium":       ["Masala Chai","Cardamom Tea","Iced Tea","Mint Tea","Ginger Lemon Tea"],
    "Rasna Orange Concentrate": ["Orange Drink","Juice Mocktail","Orange Popsicle","Rasna Punch","Orange Slush"],
    # ── BREAKFAST CEREALS ─────────────────────────────────────────────────────
    "Kellogg's Corn Flakes":  ["Cereal with Milk","Cornflake Chivda","Cornflake Ladoo","Cereal Parfait","Cornflake Mixture"],
    "Kellogg's Chocos":       ["Chocolate Cereal Bowl","Chocos Milkshake","Chocos Bark","Cereal Parfait","Chocos Ladoo"],
    "Kellogg's Muesli Fruit & Nut": ["Muesli Parfait","Muesli Smoothie Bowl","Muesli with Yogurt","Muesli Energy Bars","Muesli Porridge"],
    "Saffola Oats Plain":     ["Oats Porridge","Masala Oats","Oats Idli","Oats Khichdi","Oats Smoothie"],
    "Saffola Masala Oats":    ["Masala Oats Bowl","Spicy Oats Upma","Oats with Veggies","Quick Oats Snack","Loaded Oats Bowl"],
    "Quaker Oats":            ["Overnight Oats","Oatmeal Cookies","Granola","Oat Upma","Oats Smoothie"],
    # ── MEAT & SEAFOOD ────────────────────────────────────────────────────────
    "Chicken Breast":         ["Grilled Chicken","Chicken Stir Fry","Chicken Soup","Chicken Tikka","Chicken Salad"],
    "Chicken Thigh":          ["Chicken Curry","Baked Chicken Thighs","Chicken Biryani","BBQ Chicken","Butter Chicken"],
    "Mutton":                 ["Mutton Curry","Mutton Biryani","Keema","Mutton Rogan Josh","Mutton Stew"],
    "Fish":                   ["Fish Curry","Grilled Fish","Fish Tacos","Fish Fry","Fish Soup"],
    "Salmon":                 ["Grilled Salmon","Salmon Pasta","Teriyaki Salmon","Salmon Soup","Salmon Tacos"],
    "Prawns":                 ["Prawn Masala","Garlic Butter Prawns","Prawn Fried Rice","Prawn Curry","Prawn Pasta"],
    # ── VEGETABLES ────────────────────────────────────────────────────────────
    "Spinach":                ["Palak Paneer","Spinach Dal","Spinach Soup","Spinach Pasta","Green Smoothie"],
    "Coriander":              ["Coriander Chutney","Coriander Rice","Garnish for Dal","Coriander Soup","Herb Sauce"],
    "Tomato":                 ["Tomato Soup","Tomato Chutney","Tomato Rice","Shakshuka","Tomato Gravy"],
    "Onion":                  ["Onion Pakoda","French Onion Soup","Onion Raita","Caramelized Onions","Onion Chutney"],
    "Potato":                 ["Aloo Paratha","Mashed Potatoes","Potato Soup","Aloo Gobi","Potato Wedges"],
    "Carrot":                 ["Carrot Halwa","Carrot Soup","Carrot Cake","Glazed Carrots","Carrot Stir Fry"],
    "Mushroom":               ["Mushroom Soup","Mushroom Pasta","Mushroom Stir Fry","Stuffed Mushrooms","Mushroom Curry"],
    "Broccoli":               ["Broccoli Soup","Broccoli Stir Fry","Roasted Broccoli","Broccoli Pasta","Broccoli Salad"],
    "Corn":                   ["Corn Soup","Corn Salad","Corn Chaat","Grilled Corn","Corn Fritters"],
    "Peas":                   ["Matar Paneer","Pea Soup","Peas Pulao","Aloo Matar","Pea Stir Fry"],
    "Capsicum":               ["Capsicum Stir Fry","Stuffed Capsicum","Capsicum Rice","Capsicum Sabzi","Capsicum Chutney"],
    "Cabbage":                ["Cabbage Sabzi","Coleslaw","Cabbage Paratha","Cabbage Stir Fry","Stuffed Cabbage"],
    "Cauliflower":            ["Aloo Gobi","Cauliflower Soup","Gobi Paratha","Cauliflower Rice","Gobi Manchurian"],
    "Ginger":                 ["Ginger Tea","Ginger Chutney","Adrak Chai","Ginger Cookies","Ginger Shots"],
    "Garlic":                 ["Garlic Bread","Garlic Chutney","Garlic Butter","Garlic Tadka","Roasted Garlic"],
    # ── FRUITS ────────────────────────────────────────────────────────────────
    "Banana":                 ["Banana Smoothie","Banana Bread","Banana Pancakes","Banana Chips","Banana Milkshake"],
    "Mango":                  ["Mango Smoothie","Mango Lassi","Mango Salsa","Mango Ice Cream","Aam Panna"],
    "Apple":                  ["Apple Pie","Apple Crumble","Apple Smoothie","Baked Apples","Apple Chutney"],
    "Avocado":                ["Guacamole","Avocado Toast","Avocado Smoothie","Avocado Salad","Avocado Pasta"],
    "Strawberry":             ["Strawberry Smoothie","Strawberry Jam","Strawberry Shortcake","Strawberry Salad","Strawberry Ice Cream"],
    "Lemon":                  ["Lemonade","Lemon Tart","Lemon Chicken","Lemon Curd","Lemon Pasta"],
    "Pomegranate":            ["Pomegranate Raita","Pomegranate Juice","Anardana Chutney","Pomegranate Salad","Pomegranate Mocktail"],
    "Papaya":                 ["Papaya Smoothie","Raw Papaya Salad","Papaya Halwa","Papaya Raita","Papaya Lassi"],
    "Coconut":                ["Coconut Chutney","Coconut Rice","Coconut Curry","Coconut Ladoo","Coconut Milk"],
    "Orange":                 ["Orange Juice","Orange Cake","Candied Orange Peel","Orange Salad","Orange Smoothie"],
    "Guava":                  ["Guava Juice","Guava Chaat","Guava Jam","Guava Smoothie","Guava Dessert"],
    "Watermelon":             ["Watermelon Juice","Watermelon Salad","Watermelon Popsicle","Watermelon Cooler","Watermelon Smoothie"],
    # ── FLOUR & BAKING ────────────────────────────────────────────────────────
    "All-Purpose Flour":      ["Pancakes","Chapati","Cookies","Pizza Dough","Banana Bread"],
    "Atta":                   ["Chapati","Paratha","Poori","Wheat Bread","Thepla"],
    "Oats":                   ["Overnight Oats","Oat Smoothie","Oatmeal Cookies","Granola","Oat Upma"],
    "Dal":                    ["Dal Tadka","Dal Makhani","Dal Soup","Sambar","Khichdi"],
    "Moong Dal":              ["Moong Dal Khichdi","Moong Dal Soup","Pesarattu","Moong Dal Halwa","Dal Tadka"],
    "Heavy Cream":            ["Whipped Cream","Cream Pasta","Panacotta","Crème Brûlée","Creamy Soup"],
    "Aashirvaad Multigrain Atta": ["Multigrain Chapati","Multigrain Paratha","Healthy Poori","Multigrain Pizza","Wheat Crackers"],
    "Weikfield Cornflour":    ["Crispy Pakoda","Corn Flour Halwa","Thickening Agent","Corn Fritters","Veg Manchurian"],
    "Weikfield Cocoa Powder": ["Chocolate Cake","Chocolate Brownies","Hot Chocolate","Chocolate Mousse","Cocoa Cookies"],
}

CATEGORY_EMOJI = {
    "Dairy":"🥛","Vegetables":"🥦","Fruits":"🍎","Meat & Seafood":"🥩",
    "Bakery":"🍞","Snacks":"🍿","Beverages":"🧃","Frozen":"🧊",
    "Condiments & Sauces":"🫙","Flours":"🌾","Grains & Pulses":"🌽",
    "Spices & Herbs":"🌶️","Other":"📦","Custom":"⭐"
}
LOCATION_ICON = {"Fridge":"❄️","Freezer":"🧊","Pantry":"🗄️","Counter":"🍽️"}

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            bak = path + '.bak'
            if os.path.exists(bak):
                try:
                    with open(bak) as f:
                        return json.load(f)
                except Exception:
                    pass
    return default

def save_json(path, data):
    """Save JSON atomically and keep a .bak rolling backup."""
    if os.path.exists(path):
        try:
            os.replace(path, path + '.bak')
        except Exception:
            pass
    _atomic(path, data)

# ── Per-user file paths ───────────────────────────────────────────────────────
def _user_file(email):
    return os.path.join(USERS_DIR, _safe(email) + '.json')

def _items_file(email):
    return os.path.join(ITEMS_DIR, _safe(email) + '.json')

def load_user(email):
    return load_json(_user_file(email), None)

def save_user(email, udata):
    save_json(_user_file(email), udata)

def user_exists(email):
    return os.path.exists(_user_file(email))

def all_users():
    result = {}
    for fname in os.listdir(USERS_DIR):
        if fname.endswith('.json') and not fname.endswith('.bak'):
            try:
                with open(os.path.join(USERS_DIR, fname)) as f:
                    u = json.load(f)
                if u.get('email'):
                    result[u['email']] = u
            except Exception:
                pass
    return result

def get_user_items(email):
    return load_json(_items_file(email), [])

def save_user_items(email, items):
    save_json(_items_file(email), items)

def days_until(expiry_str):
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return (expiry - date.today()).days

def get_status(days):
    if days < 0: return "expired"
    elif days <= 3: return "expiring"
    return "fresh"

def get_shelf_days(name):
    custom = load_json(CUSTOM_ITEMS_FILE, {})
    for cat_items in custom.values():
        for ci in cat_items:
            if ci['name'].lower() == name.lower():
                return ci['shelf_life']
    if name in SHELF_LIFE:
        return SHELF_LIFE[name]
    return smart_shelf_life(name)

def current_user():
    return session.get('email')

# ── Email notification sender ─────────────────────────────────────────────────
def send_expiry_email(to_email, user_name, expiring_items, expired_items):
    """Send expiry alert email with recipe suggestions, in a background thread."""
    def _send():
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = '🔔 FreshGuard — Items Expiring Soon + Recipe Ideas'
            msg['From']    = f'FreshGuard <{SENDER_EMAIL}>'
            msg['To']      = to_email

            # ── Build item rows with recipes ──────────────────────────────────
            def recipe_chips(name):
                recipes = RECIPE_SUGGESTIONS.get(name, [])
                if not recipes:
                    # fallback generic suggestions
                    recipes = [f"Quick stir fry with {name}", f"Soup using {name}", f"{name} curry"]
                chips = ''.join(
                    f'<span style="display:inline-block;background:#0d2347;border:1px solid #1f6feb;'
                    f'border-radius:20px;padding:3px 10px;font-size:0.7rem;color:#58a6ff;'
                    f'margin:2px 3px 2px 0;">'
                    f'🍳 {r}</span>'
                    for r in recipes[:4]
                )
                return chips

            def make_rows(items, status_color, status_label_fn):
                rows = ''
                for i in items:
                    label   = status_label_fn(i)
                    recipes = recipe_chips(i['name'])
                    rows += (
                        f'<tr>'
                        f'<td style="padding:12px 14px;border-bottom:1px solid #2d333b;vertical-align:top;">'
                        f'  <div style="font-weight:600;font-size:0.88rem;">{i["name"]}</div>'
                        f'  <div style="font-size:0.72rem;color:#8b949e;margin-top:2px;">📂 {i["category"]}</div>'
                        f'  <div style="margin-top:6px;">{recipes}</div>'
                        f'</td>'
                        f'<td style="padding:12px 14px;border-bottom:1px solid #2d333b;vertical-align:top;'
                        f'    white-space:nowrap;font-size:0.82rem;color:{status_color};">{label}</td>'
                        f'</tr>'
                    )
                return rows

            expired_rows  = make_rows(
                expired_items,
                '#f85149',
                lambda i: '⛔ Expired'
            )
            expiring_rows = make_rows(
                expiring_items,
                '#d29922',
                lambda i: f'⏰ Expires in {i["days"]} day{"s" if i["days"]!=1 else ""}'
            )
            all_rows = expired_rows + expiring_rows

            html = f"""
            <div style="font-family:Arial,sans-serif;background:#0d1117;color:#e6edf3;max-width:560px;margin:0 auto;border-radius:14px;overflow:hidden;border:1px solid #30363d;">
              <!-- Header -->
              <div style="background:linear-gradient(135deg,#1a3a22,#0d2347);padding:28px 30px;text-align:center;">
                <div style="font-size:2rem;margin-bottom:8px;">🥦</div>
                <div style="font-size:1.4rem;font-weight:800;letter-spacing:1px;">FreshGuard</div>
                <div style="font-size:0.85rem;opacity:0.7;margin-top:4px;">Grocery Expiry Alert + Recipe Ideas</div>
              </div>
              <!-- Body -->
              <div style="padding:26px 28px;">
                <p style="margin:0 0 6px;font-size:0.95rem;">Hi <strong>{user_name}</strong> 👋,</p>
                <p style="margin:0 0 20px;font-size:0.85rem;color:#8b949e;line-height:1.6;">
                  Some items in your FreshGuard tracker need your attention.
                  We've added recipe ideas so you can use them up before they go to waste!
                </p>

                <!-- Items table -->
                <table style="width:100%;border-collapse:collapse;background:#161b22;border-radius:10px;overflow:hidden;border:1px solid #30363d;">
                  <thead>
                    <tr style="background:#1c2330;">
                      <th style="padding:10px 14px;text-align:left;font-size:0.72rem;color:#8b949e;text-transform:uppercase;letter-spacing:0.06em;">
                        Item &amp; Recipe Suggestions
                      </th>
                      <th style="padding:10px 14px;text-align:left;font-size:0.72rem;color:#8b949e;text-transform:uppercase;letter-spacing:0.06em;white-space:nowrap;">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody>{all_rows}</tbody>
                </table>

                <p style="margin:20px 0 0;font-size:0.82rem;color:#8b949e;line-height:1.6;">
                  Log in to FreshGuard to mark items as used or remove expired ones.
                </p>
              </div>
              <!-- Footer -->
              <div style="background:#161b22;padding:14px 28px;text-align:center;border-top:1px solid #30363d;">
                <p style="margin:0;font-size:0.72rem;color:#484f58;">
                  You are receiving this because you registered with this email on FreshGuard.<br/>
                  Alerts are sent once per day when items are expiring or expired.
                </p>
              </div>
            </div>
            """

            # Plain text version
            plain = f"FreshGuard Expiry Alert for {user_name}\n\n"
            for i in expired_items:
                recipes = ', '.join(RECIPE_SUGGESTIONS.get(i['name'], ['Use it up soon'])[:3])
                plain += f"[EXPIRED]  {i['name']} ({i['category']})\n"
                plain += f"  Recipes: {recipes}\n\n"
            for i in expiring_items:
                recipes = ', '.join(RECIPE_SUGGESTIONS.get(i['name'], ['Use it up soon'])[:3])
                plain += f"[EXPIRING in {i['days']} day{'s' if i['days']!=1 else ''}]  {i['name']} ({i['category']})\n"
                plain += f"  Recipes: {recipes}\n\n"

            msg.attach(MIMEText(plain, 'plain'))
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASS)
                server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
            print(f'[FreshGuard] Expiry alert sent to {to_email}')
        except Exception as e:
            print(f'[FreshGuard] Email send failed: {e}')

    threading.Thread(target=_send, daemon=True).start()

# ── Barcode lookup via Open Food Facts ───────────────────────────────────────
# ── Built-in Indian product barcode database (300+ popular items) ─────────────
BUILTIN_BARCODES = {
    # ── AMUL (India's #1 dairy brand) ─────────────────────────────────────────
    "8901063510012": ("Amul Taaza Milk", "Dairy"),
    "8901063510029": ("Amul Gold Milk", "Dairy"),
    "8901063510036": ("Amul Slim & Trim Milk", "Dairy"),
    "8901063510043": ("Amul Shakti Milk", "Dairy"),
    "8901063113318": ("Amul Butter", "Dairy"),
    "8901063113325": ("Amul Salted Butter", "Dairy"),
    "8901063113332": ("Amul Lite Butter", "Dairy"),
    "8901063111734": ("Amul Processed Cheese", "Dairy"),
    "8901063111741": ("Amul Mozzarella Cheese", "Dairy"),
    "8901063111758": ("Amul Cheddar Cheese", "Dairy"),
    "8901063035131": ("Amul Cheese Slices", "Dairy"),
    "8901063035148": ("Amul Cheese Spread", "Dairy"),
    "8901063100832": ("Amul Dahi", "Dairy"),
    "8901063106513": ("Amul Masti Dahi", "Dairy"),
    "8901063106520": ("Amul Masti Spiced Buttermilk", "Dairy"),
    "8901063104229": ("Amul Ghee", "Dairy"),
    "8901063104236": ("Amul Pure Ghee 1L", "Dairy"),
    "8901063000016": ("Amul Shrikhand", "Dairy"),
    "8901063000023": ("Amul Kesar Shrikhand", "Dairy"),
    "8901063200011": ("Amul Cream", "Dairy"),
    "8901063200028": ("Amul Fresh Cream", "Dairy"),
    "8901063300018": ("Amul Lassi", "Dairy"),
    "8901063300025": ("Amul Mango Lassi", "Dairy"),
    "8901063400015": ("Amul Kool Milk Chocolate", "Beverages"),
    "8901063400022": ("Amul Kool Cafe", "Beverages"),
    "8901063400039": ("Amul Kool Rose Milk", "Beverages"),
    "8901063700014": ("Amul Ice Cream Vanilla", "Frozen"),
    "8901063700021": ("Amul Ice Cream Chocolate", "Frozen"),
    "8901063700038": ("Amul Chocobar", "Frozen"),
    "8901063800015": ("Amul Paneer", "Dairy"),
    "8901063800022": ("Amul Malai Paneer", "Dairy"),
    "8901063900011": ("Amul Mithai Mate Condensed Milk", "Dairy"),
    # ── BRITANNIA ─────────────────────────────────────────────────────────────
    "8901063810014": ("Britannia Whole Wheat Bread", "Bakery"),
    "8901063810021": ("Britannia White Sandwich Bread", "Bakery"),
    "8901063810038": ("Britannia Brown Bread", "Bakery"),
    "8901719114014": ("Britannia Good Day Butter Biscuits", "Bakery"),
    "8901719114021": ("Britannia Good Day Cashew Biscuits", "Bakery"),
    "8901719112621": ("Britannia Marie Gold Biscuits", "Bakery"),
    "8901719113024": ("Britannia NutriChoice Biscuits", "Bakery"),
    "8901719113031": ("Britannia NutriChoice Digestive", "Bakery"),
    "8901719117619": ("Britannia Bourbon Biscuits", "Bakery"),
    "8901719117626": ("Britannia Bourbon Chocolate Cream", "Bakery"),
    "8901719118012": ("Britannia 50-50 Biscuits", "Bakery"),
    "8901719118029": ("Britannia Treat Biscuits", "Bakery"),
    "8901719120015": ("Britannia Little Hearts Biscuits", "Bakery"),
    "8901719121012": ("Britannia Jim Jam Biscuits", "Bakery"),
    "8901719125010": ("Britannia Milk Bikis", "Bakery"),
    "8901719126017": ("Britannia Tiger Glucose Biscuits", "Bakery"),
    "8901719160012": ("Britannia Cake", "Bakery"),
    "8901719160029": ("Britannia Fruit Cake", "Bakery"),
    "8901719161019": ("Britannia Cheese Cake", "Bakery"),
    "8901063035131": ("Britannia Cheese Slices", "Dairy"),
    # ── MAGGI / NESTLE ────────────────────────────────────────────────────────
    "8901058000427": ("Maggi 2-Minute Masala Noodles", "Grains & Pulses"),
    "8901058001271": ("Maggi Masala Noodles Family Pack", "Grains & Pulses"),
    "8901058002018": ("Maggi Chicken Noodles", "Grains & Pulses"),
    "8901058003015": ("Maggi Atta Noodles", "Grains & Pulses"),
    "8901058004012": ("Maggi Oats Noodles", "Grains & Pulses"),
    "8901058851018": ("Maggi Tomato Ketchup", "Condiments & Sauces"),
    "8901058851025": ("Maggi Hot & Sweet Tomato Chilli Sauce", "Condiments & Sauces"),
    "8901058852015": ("Maggi Masala Sauce", "Condiments & Sauces"),
    "8901058130018": ("Nescafe Classic Coffee", "Beverages"),
    "8901058130025": ("Nescafe Gold Coffee", "Beverages"),
    "8901058130032": ("Nescafe Sunrise Coffee", "Beverages"),
    "8901058500015": ("KitKat Chocolate", "Snacks"),
    "8901058501012": ("KitKat Dark Chocolate", "Snacks"),
    "8901058600016": ("Milkmaid Condensed Milk", "Dairy"),
    "8901058700014": ("Munch Chocolate", "Snacks"),
    "8901058701011": ("Munch Crunch Chocolate", "Snacks"),
    "8901058800014": ("Bar-One Chocolate", "Snacks"),
    "8901058900010": ("Eclairs Toffee", "Snacks"),
    # ── MTR FOODS ─────────────────────────────────────────────────────────────
    "8906004310017": ("MTR Upma Mix", "Grains & Pulses"),
    "8906004310024": ("MTR Rava Idli Mix", "Grains & Pulses"),
    "8906004310031": ("MTR Masala Oats", "Grains & Pulses"),
    "8906004310048": ("MTR Poha Mix", "Grains & Pulses"),
    "8906004310055": ("MTR Khichdi Mix", "Grains & Pulses"),
    "8906004320016": ("MTR Dosa Mix", "Grains & Pulses"),
    "8906004320023": ("MTR Idli Mix", "Grains & Pulses"),
    "8906004320030": ("MTR Uttapam Mix", "Grains & Pulses"),
    "8906004360013": ("MTR Dal Makhani", "Grains & Pulses"),
    "8906004360020": ("MTR Palak Paneer", "Other"),
    "8906004360037": ("MTR Shahi Paneer", "Other"),
    "8906004360044": ("MTR Chana Masala", "Grains & Pulses"),
    "8906004360051": ("MTR Mixed Vegetable Curry", "Other"),
    "8906004500012": ("MTR Sambar Powder", "Spices & Herbs"),
    "8906004500029": ("MTR Rasam Powder", "Spices & Herbs"),
    "8906004500036": ("MTR Bisibele Bath Powder", "Spices & Herbs"),
    "8906004500043": ("MTR Puliyogare Mix", "Spices & Herbs"),
    "8906004600010": ("MTR Badam Drink Mix", "Beverages"),
    "8906004600027": ("MTR Rose Milk Mix", "Beverages"),
    # ── PARLE ─────────────────────────────────────────────────────────────────
    "8901719100017": ("Parle-G Glucose Biscuits", "Bakery"),
    "8901719100024": ("Parle-G Gold Biscuits", "Bakery"),
    "8901719122613": ("Parle Hide & Seek Biscuits", "Bakery"),
    "8901719122620": ("Parle Hide & Seek Milano", "Bakery"),
    "8901719108616": ("Parle Krackjack Biscuits", "Bakery"),
    "8901719124013": ("Parle Monaco Biscuits", "Bakery"),
    "8901719124020": ("Parle Monaco Classic", "Bakery"),
    "8901719130014": ("Parle Digestive Marie", "Bakery"),
    "8901719140013": ("Parle 20-20 Cookies", "Bakery"),
    "8901719150012": ("Parle Cheeselings", "Snacks"),
    "8901719160011": ("Parle Jeffs Cream Biscuits", "Bakery"),
    "8901719200014": ("Parle Mango Bite Toffee", "Snacks"),
    "8901719210013": ("Parle Melody Chocolate Toffee", "Snacks"),
    "8901719220012": ("Parle Poppins Toffee", "Snacks"),
    "8901719230011": ("Parle Kismi Toffee Bar", "Snacks"),
    # ── LAYS / PEPSICO ────────────────────────────────────────────────────────
    "8901491101015": ("Lays Classic Salted Chips", "Snacks"),
    "8901491102012": ("Lays Magic Masala Chips", "Snacks"),
    "8901491103019": ("Lays American Style Cream & Onion", "Snacks"),
    "8901491104016": ("Lays Spanish Tomato Tango", "Snacks"),
    "8901491105013": ("Lays Indian Chatpata Chips", "Snacks"),
    "8901491201016": ("Kurkure Masala Munch", "Snacks"),
    "8901491202013": ("Kurkure Chilli Chatka", "Snacks"),
    "8901491203010": ("Kurkure Naughty Tomato", "Snacks"),
    "8901491204017": ("Kurkure Solid Masti Corn Puffs", "Snacks"),
    "8901491301014": ("Doritos Nacho Cheese", "Snacks"),
    "8901491302011": ("Doritos Spicy Sweet Chilli", "Snacks"),
    "8901491601014": ("Tropicana Orange Juice", "Beverages"),
    "8901491602011": ("Tropicana Apple Juice", "Beverages"),
    "8901491603018": ("Tropicana Mixed Fruit Juice", "Beverages"),
    "8901491604015": ("Tropicana Guava Juice", "Beverages"),
    "8901491605012": ("Tropicana Mango Juice", "Beverages"),
    "8901491701014": ("Quaker Oats", "Grains & Pulses"),
    "8901491702011": ("Quaker Oats Masala", "Grains & Pulses"),
    # ── COCA-COLA / PEPSI ─────────────────────────────────────────────────────
    "8902080100017": ("Coca-Cola", "Beverages"),
    "8902080200015": ("Sprite", "Beverages"),
    "8902080300013": ("Fanta Orange", "Beverages"),
    "8902080400011": ("Thums Up", "Beverages"),
    "8902080500019": ("Limca", "Beverages"),
    "8902080600017": ("Maaza Mango Drink", "Beverages"),
    "8902080700015": ("Kinley Water", "Beverages"),
    "8902080800013": ("Minute Maid Pulpy Orange", "Beverages"),
    "8901039140015": ("Pepsi", "Beverages"),
    "8901039150014": ("7Up", "Beverages"),
    "8901039160013": ("Mirinda Orange", "Beverages"),
    "8901039170012": ("Mountain Dew", "Beverages"),
    "8901039180011": ("Sting Energy Drink", "Beverages"),
    "8901039190010": ("Slice Mango Drink", "Beverages"),
    "8901039200016": ("Aquafina Water", "Beverages"),
    # ── TATA ──────────────────────────────────────────────────────────────────
    "8901288000015": ("Tata Salt", "Spices & Herbs"),
    "8901288100013": ("Tata Rock Salt", "Spices & Herbs"),
    "8901288200011": ("Tata Iodised Salt", "Spices & Herbs"),
    "8904147000015": ("Tata Tea Premium", "Beverages"),
    "8904147100013": ("Tata Tea Gold", "Beverages"),
    "8904147200011": ("Tata Tea Agni", "Beverages"),
    "8904147300019": ("Tata Tea Chakra Gold", "Beverages"),
    "8904147400017": ("Tata Tea Life", "Beverages"),
    "8901289000017": ("Tata Sampann Chilli Powder", "Spices & Herbs"),
    "8901289000024": ("Tata Sampann Turmeric Powder", "Spices & Herbs"),
    "8901289000031": ("Tata Sampann Coriander Powder", "Spices & Herbs"),
    "8901289000048": ("Tata Sampann Garam Masala", "Spices & Herbs"),
    "8901289000055": ("Tata Sampann Cumin Powder", "Spices & Herbs"),
    "8901289100014": ("Tata Sampann Chana Dal", "Grains & Pulses"),
    "8901289100021": ("Tata Sampann Toor Dal", "Grains & Pulses"),
    "8901289100038": ("Tata Sampann Moong Dal", "Grains & Pulses"),
    "8901289100045": ("Tata Sampann Urad Dal", "Grains & Pulses"),
    "8901289200011": ("Tata Sampann Atta", "Flours"),
    "8901289200028": ("Tata Sampann Multigrain Atta", "Flours"),
    "8901289300018": ("Tata Soulfull Ragi Bites", "Snacks"),
    "8901289300025": ("Tata Soulfull Millet Muesli", "Grains & Pulses"),
    "8901289400015": ("Tata Gluco+ Glucose Biscuits", "Bakery"),
    # ── HALDIRAM ──────────────────────────────────────────────────────────────
    "8906014100016": ("Haldiram Aloo Bhujia", "Snacks"),
    "8906014100023": ("Haldiram Moong Dal", "Snacks"),
    "8906014100030": ("Haldiram Mixture", "Snacks"),
    "8906014100047": ("Haldiram Navratan Mix", "Snacks"),
    "8906014100054": ("Haldiram Chana Dal", "Snacks"),
    "8906014100061": ("Haldiram Mathi", "Snacks"),
    "8906014100078": ("Haldiram Sev", "Snacks"),
    "8906014100085": ("Haldiram Murukku", "Snacks"),
    "8906014200013": ("Haldiram Soan Papdi", "Snacks"),
    "8906014200020": ("Haldiram Kaju Katli", "Snacks"),
    "8906014200037": ("Haldiram Gulab Jamun", "Other"),
    "8906014300010": ("Haldiram Pani Puri Kit", "Snacks"),
    "8906014300027": ("Haldiram Bhel Puri Mix", "Snacks"),
    "8906014400017": ("Haldiram Atta Noodles", "Grains & Pulses"),
    "8906014400024": ("Haldiram Masala Noodles", "Grains & Pulses"),
    # ── FORTUNE / ADANI WILMAR ────────────────────────────────────────────────
    "8906003700017": ("Fortune Sunflower Oil", "Condiments & Sauces"),
    "8906003700024": ("Fortune Refined Oil", "Condiments & Sauces"),
    "8906003700031": ("Fortune Soyabean Oil", "Condiments & Sauces"),
    "8906003700048": ("Fortune Rice Bran Oil", "Condiments & Sauces"),
    "8906003700055": ("Fortune Mustard Oil", "Condiments & Sauces"),
    "8906003700062": ("Fortune Groundnut Oil", "Condiments & Sauces"),
    "8906003800014": ("Fortune Basmati Rice", "Grains & Pulses"),
    "8906003800021": ("Fortune Atta", "Flours"),
    "8906003800038": ("Fortune Multigrain Atta", "Flours"),
    "8906003800045": ("Fortune Maida", "Flours"),
    "8906003800052": ("Fortune Suji / Semolina", "Flours"),
    "8906003800069": ("Fortune Besan", "Flours"),
    # ── KISSAN / HUL ──────────────────────────────────────────────────────────
    "8901063220015": ("Kissan Tomato Ketchup", "Condiments & Sauces"),
    "8901063220022": ("Kissan Mixed Fruit Jam", "Condiments & Sauces"),
    "8901063220039": ("Kissan Orange Marmalade", "Condiments & Sauces"),
    "8901063220046": ("Kissan Pineapple Jam", "Condiments & Sauces"),
    "8901063220053": ("Kissan Strawberry Jam", "Condiments & Sauces"),
    "8901063220060": ("Kissan Squash Orange", "Beverages"),
    "8901063220077": ("Kissan Squash Lemon", "Beverages"),
    # ── DABUR ─────────────────────────────────────────────────────────────────
    "8901207002014": ("Dabur Honey", "Condiments & Sauces"),
    "8901207002021": ("Dabur Honey Tulsi", "Condiments & Sauces"),
    "8901207010019": ("Dabur Real Orange Juice", "Beverages"),
    "8901207010026": ("Dabur Real Apple Juice", "Beverages"),
    "8901207010033": ("Dabur Real Mango Juice", "Beverages"),
    "8901207010040": ("Dabur Real Mixed Fruit Juice", "Beverages"),
    "8901207010057": ("Dabur Real Guava Juice", "Beverages"),
    "8901207010064": ("Dabur Real Pomegranate Juice", "Beverages"),
    "8901207020018": ("Dabur Hommade Coconut Milk", "Condiments & Sauces"),
    "8901207030017": ("Dabur Chyawanprash", "Other"),
    "8901207040016": ("Dabur Amla Juice", "Beverages"),
    "8901207050015": ("Dabur Gulabari Rose Water", "Other"),
    # ── ITC / AASHIRVAAD ─────────────────────────────────────────────────────
    "8901098100010": ("Aashirvaad Atta", "Flours"),
    "8901098100027": ("Aashirvaad Multigrain Atta", "Flours"),
    "8901098100034": ("Aashirvaad Select Sharbati Atta", "Flours"),
    "8901098100041": ("Aashirvaad Superior MP Atta", "Flours"),
    "8901098110019": ("Aashirvaad Besan", "Flours"),
    "8901098110026": ("Aashirvaad Maida", "Flours"),
    "8901098110033": ("Aashirvaad Suji", "Flours"),
    "8901098200017": ("Sunfeast Dark Fantasy Biscuits", "Bakery"),
    "8901098200024": ("Sunfeast Marie Light Biscuits", "Bakery"),
    "8901098200031": ("Sunfeast Mom's Magic Butter", "Bakery"),
    "8901098200048": ("Sunfeast Bounce Biscuits", "Bakery"),
    "8901098200055": ("Sunfeast Farmlite Oat Biscuits", "Bakery"),
    "8901098300014": ("Bingo Mad Angles Chips", "Snacks"),
    "8901098300021": ("Bingo Original Style Chips", "Snacks"),
    "8901098300038": ("Bingo Tedhe Medhe", "Snacks"),
    "8901098300045": ("Bingo Yumitos Chips", "Snacks"),
    "8901098400011": ("Yippee Noodles Magic Masala", "Grains & Pulses"),
    "8901098400028": ("Yippee Noodles Classic Masala", "Grains & Pulses"),
    "8901098400035": ("Yippee Noodles Mood Masala", "Grains & Pulses"),
    "8901098500018": ("Aashirvaad Instant Poha Mix", "Grains & Pulses"),
    "8901098500025": ("Aashirvaad Instant Upma Mix", "Grains & Pulses"),
    "8901725016852": ("Aashirvaad Wheat Flour","Flours"),
    # ── MOTHER DAIRY ─────────────────────────────────────────────────────────
    "8901735100016": ("Mother Dairy Full Cream Milk", "Dairy"),
    "8901735100023": ("Mother Dairy Toned Milk", "Dairy"),
    "8901735100030": ("Mother Dairy Double Toned Milk", "Dairy"),
    "8901735200013": ("Mother Dairy Dahi", "Dairy"),
    "8901735200020": ("Mother Dairy Mishti Doi", "Dairy"),
    "8901735300010": ("Mother Dairy Ice Cream Vanilla", "Frozen"),
    "8901735300027": ("Mother Dairy Ice Cream Chocolate", "Frozen"),
    "8901735300034": ("Mother Dairy Softy Mix", "Frozen"),
    "8901735400017": ("Mother Dairy Butter", "Dairy"),
    "8901735500014": ("Mother Dairy Ghee", "Dairy"),
    "8901735600011": ("Mother Dairy Paneer", "Dairy"),
    "8901735700018": ("Mother Dairy Fruit Yogurt Mango", "Dairy"),
    "8901735700025": ("Mother Dairy Fruit Yogurt Strawberry", "Dairy"),
    # ── PATANJALI ─────────────────────────────────────────────────────────────
    "8904109000014": ("Patanjali Atta", "Flours"),
    "8904109000021": ("Patanjali Multigrain Atta", "Flours"),
    "8904109000038": ("Patanjali Maida", "Flours"),
    "8904109000045": ("Patanjali Besan", "Flours"),
    "8904109000052": ("Patanjali Suji", "Flours"),
    "8904109100011": ("Patanjali Pure Honey", "Condiments & Sauces"),
    "8904109200018": ("Patanjali Chyawanprash", "Other"),
    "8904109300015": ("Patanjali Ghee", "Dairy"),
    "8904109400012": ("Patanjali Mustard Oil", "Condiments & Sauces"),
    "8904109400029": ("Patanjali Sesame Oil", "Condiments & Sauces"),
    "8904109500019": ("Patanjali Amla Juice", "Beverages"),
    "8904109500026": ("Patanjali Aloe Vera Juice", "Beverages"),
    "8904109600016": ("Patanjali Cornflakes", "Grains & Pulses"),
    "8904109600023": ("Patanjali Oats", "Grains & Pulses"),
    "8904109700013": ("Patanjali Cow Desi Ghee", "Dairy"),
    "8904109800010": ("Patanjali Rice", "Grains & Pulses"),
    # ── CATCH SPICES ──────────────────────────────────────────────────────────
    "8906023000015": ("Catch Turmeric Powder", "Spices & Herbs"),
    "8906023000022": ("Catch Red Chilli Powder", "Spices & Herbs"),
    "8906023000039": ("Catch Coriander Powder", "Spices & Herbs"),
    "8906023000046": ("Catch Garam Masala", "Spices & Herbs"),
    "8906023000053": ("Catch Cumin Powder", "Spices & Herbs"),
    "8906023000060": ("Catch Black Pepper Powder", "Spices & Herbs"),
    "8906023000077": ("Catch Amchur Powder", "Spices & Herbs"),
    "8906023000084": ("Catch Chaat Masala", "Spices & Herbs"),
    "8906023000091": ("Catch Kitchen King Masala", "Spices & Herbs"),
    # ── EVEREST SPICES ────────────────────────────────────────────────────────
    "8906003200017": ("Everest Garam Masala", "Spices & Herbs"),
    "8906003200024": ("Everest Sambhar Masala", "Spices & Herbs"),
    "8906003200031": ("Everest Chicken Masala", "Spices & Herbs"),
    "8906003200048": ("Everest Turmeric Powder", "Spices & Herbs"),
    "8906003200055": ("Everest Red Chilli Powder", "Spices & Herbs"),
    "8906003200062": ("Everest Coriander Powder", "Spices & Herbs"),
    "8906003200079": ("Everest Biryani Masala", "Spices & Herbs"),
    "8906003200086": ("Everest Chana Masala", "Spices & Herbs"),
    "8906003200093": ("Everest Pav Bhaji Masala", "Spices & Herbs"),
    "8906003200109": ("Everest Kitchen King Masala", "Spices & Herbs"),
    "8906003200116": ("Everest Meat Masala", "Spices & Herbs"),
    "8906003200123": ("Everest Rajma Masala", "Spices & Herbs"),
    # ── MDH SPICES ────────────────────────────────────────────────────────────
    "8901719300016": ("MDH Deggi Mirch", "Spices & Herbs"),
    "8901719300023": ("MDH Garam Masala", "Spices & Herbs"),
    "8901719300030": ("MDH Chole Masala", "Spices & Herbs"),
    "8901719300047": ("MDH Dal Makhani Masala", "Spices & Herbs"),
    "8901719300054": ("MDH Biryani Masala", "Spices & Herbs"),
    "8901719300061": ("MDH Sambhar Masala", "Spices & Herbs"),
    "8901719300078": ("MDH Kitchen King Masala", "Spices & Herbs"),
    "8901719300085": ("MDH Chunky Chat Masala", "Spices & Herbs"),
    "8901719300092": ("MDH Chicken Masala", "Spices & Herbs"),
    # ── SAFFOLA / MARICO ──────────────────────────────────────────────────────
    "8901163000015": ("Saffola Gold Oil", "Condiments & Sauces"),
    "8901163000022": ("Saffola Active Oil", "Condiments & Sauces"),
    "8901163000039": ("Saffola Total Oil", "Condiments & Sauces"),
    "8901163100012": ("Saffola Oats Plain", "Grains & Pulses"),
    "8901163100029": ("Saffola Masala Oats", "Grains & Pulses"),
    "8901163100036": ("Saffola Masala Oats Veggie Twist", "Grains & Pulses"),
    "8901163200019": ("Saffola Peanut Butter Crunchy", "Condiments & Sauces"),
    "8901163200026": ("Saffola Peanut Butter Smooth", "Condiments & Sauces"),
    "8901163300016": ("Saffola Choco Muesli", "Grains & Pulses"),
    "8901163300023": ("Saffola Muesli Nuts & Raisins", "Grains & Pulses"),
    # ── SUNFEAST / ITC BISCUITS ───────────────────────────────────────────────
    "8901098210016": ("Sunfeast Yippee Magic Masala", "Grains & Pulses"),
    "8901098210023": ("Sunfeast Yippee Tricolor Pasta", "Grains & Pulses"),
    # ── GODREJ / NATURE'S BASKET ──────────────────────────────────────────────
    "8901719400015": ("Nature's Basket Mixed Nuts", "Snacks"),
    # ── PRIYA PICKLES ─────────────────────────────────────────────────────────
    "8901072000014": ("Priya Mango Pickle", "Condiments & Sauces"),
    "8901072000021": ("Priya Lime Pickle", "Condiments & Sauces"),
    "8901072000038": ("Priya Gongura Pickle", "Condiments & Sauces"),
    "8901072000045": ("Priya Tomato Pickle", "Condiments & Sauces"),
    "8901072000052": ("Priya Ginger Pickle", "Condiments & Sauces"),
    # ── EASTERN SPICES ────────────────────────────────────────────────────────
    "8906003300014": ("Eastern Chicken Masala", "Spices & Herbs"),
    "8906003300021": ("Eastern Fish Masala", "Spices & Herbs"),
    "8906003300038": ("Eastern Mutton Masala", "Spices & Herbs"),
    "8906003300045": ("Eastern Biryani Masala", "Spices & Herbs"),
    "8906003300052": ("Eastern Turmeric Powder", "Spices & Herbs"),
    "8906003300069": ("Eastern Chilli Powder", "Spices & Herbs"),
    # ── BIKAJI ────────────────────────────────────────────────────────────────
    "8906010100014": ("Bikaji Bikaneri Bhujia", "Snacks"),
    "8906010100021": ("Bikaji Aloo Bhujia", "Snacks"),
    "8906010100038": ("Bikaji Moong Dal", "Snacks"),
    "8906010100045": ("Bikaji Mixture", "Snacks"),
    "8906010200011": ("Bikaji Rasgulla", "Other"),
    "8906010200028": ("Bikaji Gulab Jamun Mix", "Other"),
    # ── PAPER BOAT / HECTOR BEVERAGES ─────────────────────────────────────────
    "8906056800014": ("Paper Boat Aamras", "Beverages"),
    "8906056800021": ("Paper Boat Jamun Kala Khatta", "Beverages"),
    "8906056800038": ("Paper Boat Jaljeera", "Beverages"),
    "8906056800045": ("Paper Boat Coconut Water", "Beverages"),
    "8906056800052": ("Paper Boat Raw Mango Panna", "Beverages"),
    # ── B NATURAL / ITC BEVERAGES ─────────────────────────────────────────────
    "8901098600015": ("B Natural Mango Juice", "Beverages"),
    "8901098600022": ("B Natural Orange Juice", "Beverages"),
    "8901098600039": ("B Natural Mixed Fruit Juice", "Beverages"),
    "8901098600046": ("B Natural Guava Juice", "Beverages"),
    # ── NANDINI DAIRY (Karnataka) ─────────────────────────────────────────────
    "8906003400011": ("Nandini Full Cream Milk", "Dairy"),
    "8906003400028": ("Nandini Toned Milk", "Dairy"),
    "8906003400035": ("Nandini Ghee", "Dairy"),
    "8906003400042": ("Nandini Butter", "Dairy"),
    "8906003400059": ("Nandini Dahi", "Dairy"),
    "8906003400066": ("Nandini Paneer", "Dairy"),
    # ── HERITAGE DAIRY ────────────────────────────────────────────────────────
    "8906003500018": ("Heritage Fresh Milk", "Dairy"),
    "8906003500025": ("Heritage Dahi", "Dairy"),
    "8906003500032": ("Heritage Butter", "Dairy"),
    "8906003500049": ("Heritage Ghee", "Dairy"),
    # ── VIJAYA DAIRY ──────────────────────────────────────────────────────────
    "8906003600015": ("Vijaya Full Cream Milk", "Dairy"),
    "8906003600022": ("Vijaya Butter", "Dairy"),
    "8906003600039": ("Vijaya Ghee", "Dairy"),
    # ── DR. OETKER / FUNFOODS ─────────────────────────────────────────────────
    "8901108000013": ("FunFoods Mayonnaise", "Condiments & Sauces"),
    "8901108000020": ("FunFoods Garlic Mayonnaise", "Condiments & Sauces"),
    "8901108000037": ("FunFoods Burger Spread", "Condiments & Sauces"),
    "8901108000044": ("FunFoods Pasta Pesto", "Condiments & Sauces"),
    "8901108100010": ("Dr. Oetker Vanilla Custard Powder", "Other"),
    "8901108100027": ("Dr. Oetker Chocolate Pudding", "Other"),
    # ── DEL MONTE ─────────────────────────────────────────────────────────────
    "8901228000010": ("Del Monte Tomato Ketchup", "Condiments & Sauces"),
    "8901228000027": ("Del Monte Mixed Fruit Jam", "Condiments & Sauces"),
    "8901228000034": ("Del Monte Spaghetti", "Grains & Pulses"),
    "8901228000041": ("Del Monte Penne Pasta", "Grains & Pulses"),
    "8901228000058": ("Del Monte Fusilli Pasta", "Grains & Pulses"),
    "8901228000065": ("Del Monte Olive Oil", "Condiments & Sauces"),
    # ── REAL / FRESH JUICES ───────────────────────────────────────────────────
    "8901207011016": ("Dabur Real Peach Nectar", "Beverages"),
    "8901207011023": ("Dabur Real Litchi Juice", "Beverages"),
    # ── HEINZ ─────────────────────────────────────────────────────────────────
    "8901058910013": ("Heinz Tomato Ketchup", "Condiments & Sauces"),
    "8901058910020": ("Heinz Mayonnaise", "Condiments & Sauces"),
    "8901058910037": ("Heinz Chili Sauce", "Condiments & Sauces"),
    # ── CHING'S SECRET ────────────────────────────────────────────────────────
    "8906004110013": ("Ching's Secret Schezwan Sauce", "Condiments & Sauces"),
    "8906004110020": ("Ching's Secret Soy Sauce", "Condiments & Sauces"),
    "8906004110037": ("Ching's Secret Chilli Sauce", "Condiments & Sauces"),
    "8906004120012": ("Ching's Secret Hakka Noodles", "Grains & Pulses"),
    "8906004120029": ("Ching's Secret Egg Hakka Noodles", "Grains & Pulses"),
    "8906004120036": ("Ching's Secret Schezwan Noodles", "Grains & Pulses"),
    "8906004130011": ("Ching's Secret Instant Noodles Masala", "Grains & Pulses"),
    # ── SUNRIDGE / WEIKFIELD ──────────────────────────────────────────────────
    "8901097000016": ("Weikfield Custard Powder Vanilla", "Other"),
    "8901097000023": ("Weikfield Cornflour", "Flours"),
    "8901097000030": ("Weikfield Baking Powder", "Other"),
    "8901097000047": ("Weikfield Cocoa Powder", "Other"),
    # ── TOPS / GOLDEN HARVEST ─────────────────────────────────────────────────
    "8906012000019": ("Tops Mango Pickle", "Condiments & Sauces"),
    "8906012000026": ("Tops Mixed Pickle", "Condiments & Sauces"),
    "8906012000033": ("Tops Tomato Sauce", "Condiments & Sauces"),
    "8906012000040": ("Tops Chilli Sauce", "Condiments & Sauces"),
    # ── KOHINOOR ──────────────────────────────────────────────────────────────
    "8906001000017": ("Kohinoor Basmati Rice", "Grains & Pulses"),
    "8906001000024": ("Kohinoor Platinum Basmati Rice", "Grains & Pulses"),
    "8906001000031": ("Kohinoor Super Value Basmati Rice", "Grains & Pulses"),
    # ── INDIA GATE / KRBL ─────────────────────────────────────────────────────
    "8901083000018": ("India Gate Basmati Rice Classic", "Grains & Pulses"),
    "8901083000025": ("India Gate Basmati Rice Feast", "Grains & Pulses"),
    "8901083000032": ("India Gate Basmati Rice Tibar", "Grains & Pulses"),
    "8901083000049": ("India Gate Brown Basmati Rice", "Grains & Pulses"),
    # ── DAWAT ─────────────────────────────────────────────────────────────────
    "8901060000016": ("Daawat Traditional Basmati Rice", "Grains & Pulses"),
    "8901060000023": ("Daawat Rozana Basmati Rice", "Grains & Pulses"),
    "8901060000030": ("Daawat Super Basmati Rice", "Grains & Pulses"),
    # ── USTAAD / DEEP FOODS ───────────────────────────────────────────────────
    "8906007000015": ("Deep Frozen Paratha", "Frozen"),
    "8906007000022": ("Deep Frozen Aloo Paratha", "Frozen"),
    "8906007000039": ("Deep Frozen Gobi Paratha", "Frozen"),
    # ── SUNPURE / ADANI ───────────────────────────────────────────────────────
    "8906003900012": ("Sundrop Heart Oil", "Condiments & Sauces"),
    "8906003900029": ("Sundrop NutriLite Oil", "Condiments & Sauces"),
    # ── REAL / TROPICANA (EXTRA) ──────────────────────────────────────────────
    "8901491606019": ("Tropicana Cranberry Juice", "Beverages"),
    "8901491607016": ("Tropicana Pomegranate Juice", "Beverages"),
    # ── SUNRISE / AACHI SPICES ────────────────────────────────────────────────
    "8906011000016": ("Aachi Chicken Masala", "Spices & Herbs"),
    "8906011000023": ("Aachi Sambar Powder", "Spices & Herbs"),
    "8906011000030": ("Aachi Biryani Masala", "Spices & Herbs"),
    "8906011000047": ("Aachi Fish Masala", "Spices & Herbs"),
    "8906011000054": ("Aachi Coriander Powder", "Spices & Herbs"),
    "8906011000061": ("Aachi Turmeric Powder", "Spices & Herbs"),
    # ── PREETHI / KENT / PRESTIGE KITCHEN READY MIXES ────────────────────────
    "8906015000014": ("Gits Gulab Jamun Mix", "Other"),
    "8906015000021": ("Gits Jalebi Mix", "Other"),
    "8906015000038": ("Gits Dhokla Mix", "Grains & Pulses"),
    "8906015000045": ("Gits Dosa Mix", "Grains & Pulses"),
    "8906015000052": ("Gits Idli Mix", "Grains & Pulses"),
    "8906015000069": ("Gits Upma Mix", "Grains & Pulses"),
    "8906015000076": ("Gits Khaman Dhokla Mix", "Grains & Pulses"),
    # ── KELLOGG'S ─────────────────────────────────────────────────────────────
    "8901499000016": ("Kellogg's Corn Flakes", "Grains & Pulses"),
    "8901499000023": ("Kellogg's Chocos", "Grains & Pulses"),
    "8901499000030": ("Kellogg's Froot Loops", "Grains & Pulses"),
    "8901499000047": ("Kellogg's Special K", "Grains & Pulses"),
    "8901499000054": ("Kellogg's Muesli Fruit & Nut", "Grains & Pulses"),
    "8901499000061": ("Kellogg's Oats", "Grains & Pulses"),
    "8901499100013": ("Kellogg's All-Bran", "Grains & Pulses"),
    "8901499300016": ("Kellogg's Honey Loops", "Grains & Pulses"),
    # ── HORLICKS / GSK ───────────────────────────────────────────────────────
    "8901024000017": ("Horlicks Classic Malt", "Beverages"),
    "8901024000024": ("Horlicks Junior", "Beverages"),
    "8901024000031": ("Horlicks Women's Plus", "Beverages"),
    "8901024000048": ("Horlicks Chocolate", "Beverages"),
    "8901024100014": ("Boost Energy Drink", "Beverages"),
    "8901024200011": ("Maltova Malt Drink", "Beverages"),
    # ── BOURNVITA / CADBURY / MONDELEZ ───────────────────────────────────────
    "8901396000018": ("Bournvita Chocolate Health Drink", "Beverages"),
    "8901396000025": ("Bournvita 5 Star Magic", "Beverages"),
    "8901396100015": ("Cadbury Dairy Milk", "Snacks"),
    "8901396100022": ("Cadbury Dairy Milk Silk", "Snacks"),
    "8901396100039": ("Cadbury Dairy Milk Fruit & Nut", "Snacks"),
    "8901396200012": ("Cadbury 5 Star", "Snacks"),
    "8901396200029": ("Cadbury Gems", "Snacks"),
    "8901396300019": ("Cadbury Eclairs", "Snacks"),
    "8901396400016": ("Cadbury Oreo Biscuits", "Bakery"),
    "8901396400023": ("Cadbury Oreo Chocolate Cream", "Bakery"),
    "8901396500013": ("Cadbury Perk Chocolate", "Snacks"),
    # ── FERRERO / NUTELLA ─────────────────────────────────────────────────────
    "8000500000018": ("Ferrero Rocher", "Snacks"),
    "8000500037088": ("Nutella Hazelnut Spread", "Condiments & Sauces"),
    # ── NOODLES EXTRA ─────────────────────────────────────────────────────────
    "8906004140018": ("Ching's Secret Manchow Noodles", "Grains & Pulses"),
    "8906004140025": ("Ching's Secret Veg Stir Fry Noodles", "Grains & Pulses"),
    # ── ASHOKA READY-TO-EAT ──────────────────────────────────────────────────
    "8906019100010": ("Ashoka Ready Rajma Curry", "Other"),
    "8906019100027": ("Ashoka Ready Chana Masala", "Other"),
    "8906019100034": ("Ashoka Ready Dal Makhani", "Other"),
    "8906019100041": ("Ashoka Ready Palak Paneer", "Other"),
    # ── BONN / ENGLISH OVEN BREAD ─────────────────────────────────────────────
    "8906020100016": ("Bonn White Bread", "Bakery"),
    "8906020100023": ("Bonn Brown Bread", "Bakery"),
    "8906020100030": ("Bonn Multigrain Bread", "Bakery"),
    "8906020200013": ("English Oven Sandwich Bread", "Bakery"),
    "8906020200020": ("English Oven Whole Wheat Bread", "Bakery"),
    # ── WEIKFIELD PASTA ───────────────────────────────────────────────────────
    "8901097100013": ("Weikfield Pasta Penne", "Grains & Pulses"),
    "8901097100020": ("Weikfield Pasta Spaghetti", "Grains & Pulses"),
    "8901097100037": ("Weikfield Pasta Fusilli", "Grains & Pulses"),
    # ── LIJJAT PAPAD ──────────────────────────────────────────────────────────
    "8906022000016": ("Lijjat Urad Papad", "Snacks"),
    "8906022000023": ("Lijjat Moong Papad", "Snacks"),
    "8906022000030": ("Lijjat Masala Papad", "Snacks"),
    # ── IDHAYAM / SESAME OIL ─────────────────────────────────────────────────
    "8906025000013": ("Idhayam Sesame Oil", "Condiments & Sauces"),
    "8906025000020": ("Idhayam Groundnut Oil", "Condiments & Sauces"),
    # ── RED LABEL / BRU TEA & COFFEE ──────────────────────────────────────────
    "8901030190017": ("Brooke Bond Red Label Tea", "Beverages"),
    "8901030190024": ("Brooke Bond Taaza Tea", "Beverages"),
    "8901030190031": ("Brooke Bond 3 Roses Tea", "Beverages"),
    "8901030190048": ("Brooke Bond Taj Mahal Tea", "Beverages"),
    "8901030290014": ("Bru Instant Coffee", "Beverages"),
    "8901030290021": ("Bru Gold Coffee", "Beverages"),
    "8901030290038": ("Bru Green Label Coffee", "Beverages"),
    # ── LIPTON ────────────────────────────────────────────────────────────────
    "8901030390011": ("Lipton Yellow Label Tea", "Beverages"),
    "8901030390028": ("Lipton Green Tea", "Beverages"),
    "8901030390035": ("Lipton Honey Lemon Green Tea", "Beverages"),
    # ── TATA TEA EXTRA ────────────────────────────────────────────────────────
    "8904147500014": ("Tata Tea Elaichi", "Beverages"),
    "8904147600011": ("Tata Tetley Green Tea", "Beverages"),
    "8904147600028": ("Tata Tetley Lemon Green Tea", "Beverages"),
    # ── KEYA SPICES ───────────────────────────────────────────────────────────
    "8906032000016": ("Keya Italian Pizza Seasoning", "Spices & Herbs"),
    "8906032000023": ("Keya Peri Peri Spice Mix", "Spices & Herbs"),
    "8906032000030": ("Keya Mexican Seasoning", "Spices & Herbs"),
    "8906032000047": ("Keya Oregano", "Spices & Herbs"),
    # ── VEEBA SAUCES ──────────────────────────────────────────────────────────
    "8906035000013": ("Veeba Burger Sauce", "Condiments & Sauces"),
    "8906035000020": ("Veeba Chipotle Mayo Sauce", "Condiments & Sauces"),
    "8906035000037": ("Veeba Honey Mustard Sauce", "Condiments & Sauces"),
    "8906035000044": ("Veeba Sriracha Sauce", "Condiments & Sauces"),
    # ── BAMBINO PASTA ─────────────────────────────────────────────────────────
    "8901048000017": ("Bambino Vermicelli", "Grains & Pulses"),
    "8901048000024": ("Bambino Pasta", "Grains & Pulses"),
    "8901048000031": ("Bambino Macaroni", "Grains & Pulses"),
    "8901048100014": ("Bambino Instant Vermicelli", "Grains & Pulses"),
    # ── ACT II POPCORN ────────────────────────────────────────────────────────
    "8906037000011": ("Act II Butter Popcorn", "Snacks"),
    "8906037000028": ("Act II Masala Popcorn", "Snacks"),
    "8906037000035": ("Act II Caramel Popcorn", "Snacks"),
    # ── YELLOW DIAMOND CHIPS ──────────────────────────────────────────────────
    "8906028000018": ("Yellow Diamond Magic Masala Chips", "Snacks"),
    "8906028000025": ("Yellow Diamond Chilli Chatak Chips", "Snacks"),
    # ── BORGES OLIVE OIL ──────────────────────────────────────────────────────
    "8410108000018": ("Borges Extra Virgin Olive Oil", "Condiments & Sauces"),
    "8410108000025": ("Borges Olive Oil Classic", "Condiments & Sauces"),
    # ── MAPRO FOODS ───────────────────────────────────────────────────────────
    "8906053000015": ("Mapro Strawberry Jam", "Condiments & Sauces"),
    "8906053000022": ("Mapro Mixed Fruit Jam", "Condiments & Sauces"),
    "8906053000039": ("Mapro Rose Syrup", "Beverages"),
    # ── PILLSBURY ATTA ────────────────────────────────────────────────────────
    "8901719500013": ("Pillsbury Chakki Fresh Atta", "Flours"),
    "8901719500020": ("Pillsbury Multigrain Atta", "Flours"),
    "8901719500037": ("Pillsbury Maida", "Flours"),
    # ── NUTELLA / SPREAD ──────────────────────────────────────────────────────
    "8906053100012": ("Sundrop Peanut Butter Crunchy", "Condiments & Sauces"),
    "8906053100029": ("Sundrop Peanut Butter Creamy", "Condiments & Sauces"),
    # ── KITCHENS OF INDIA / ITC READY MEALS ──────────────────────────────────
    "8901098800010": ("Kitchens of India Butter Chicken Paste", "Condiments & Sauces"),
    "8901098800027": ("Kitchens of India Biryani Paste", "Condiments & Sauces"),
    "8901098800034": ("Kitchens of India Dal Makhani", "Other"),
    # ── KWALITY WALLS ICE CREAM ───────────────────────────────────────────────
    "8901030490017": ("Kwality Wall's Mango Dolly", "Frozen"),
    "8901030490024": ("Kwality Wall's Cornetto", "Frozen"),
    "8901030490031": ("Kwality Wall's Feast", "Frozen"),
    # ── BISLERI WATER ─────────────────────────────────────────────────────────
    "8906044000014": ("Bisleri Mineral Water 1L", "Beverages"),
    "8906044000021": ("Bisleri Mineral Water 500ml", "Beverages"),
    # ── RASNA CONCENTRATE ─────────────────────────────────────────────────────
    "8906045000013": ("Rasna Orange Concentrate", "Beverages"),
    "8906045000020": ("Rasna Mango Concentrate", "Beverages"),
    "8906045000037": ("Rasna Lemon Concentrate", "Beverages"),
    # ── FROOTI / APPY ─────────────────────────────────────────────────────────
    "8906046000012": ("Frooti Mango Drink", "Beverages"),
    "8906046100019": ("Appy Fizz Apple Drink", "Beverages"),
    "8906046100026": ("Appy Apple Juice", "Beverages"),
    # ── GOWARDHAN DAIRY ───────────────────────────────────────────────────────
    "8906056900011": ("Gowardhan Ghee", "Dairy"),
    "8906056900028": ("Gowardhan Butter", "Dairy"),
    "8906056900035": ("Gowardhan Paneer", "Dairy"),
    "8906056900042": ("Gowardhan Dahi", "Dairy"),
    # ── LAXMI DALS & RICE ─────────────────────────────────────────────────────
    "8906058000019": ("Laxmi Toor Dal", "Grains & Pulses"),
    "8906058000026": ("Laxmi Chana Dal", "Grains & Pulses"),
    "8906058000033": ("Laxmi Moong Dal", "Grains & Pulses"),
    "8906058000040": ("Laxmi Urad Dal", "Grains & Pulses"),
    "8906058000057": ("Laxmi Masoor Dal", "Grains & Pulses"),
    "8906058100016": ("Laxmi Basmati Rice", "Grains & Pulses"),
    "8906058100023": ("Laxmi Idli Rice", "Grains & Pulses"),
    # ── ANNAPURNA / HUL ATTA ─────────────────────────────────────────────────
    "8901030590014": ("Annapurna Atta", "Flours"),
    "8901030590021": ("Annapurna Multigrain Atta", "Flours"),
    # ── TIRUMALA / ANDHRA DAIRY ───────────────────────────────────────────────
    "8906059000018": ("Tirumala Full Cream Milk", "Dairy"),
    "8906059000025": ("Tirumala Ghee", "Dairy"),
    "8906059000032": ("Tirumala Butter", "Dairy"),
}

CAT_MAP = {
    'milk': 'Dairy', 'dairy': 'Dairy', 'cheese': 'Dairy',
    'yogurt': 'Dairy', 'butter': 'Dairy', 'paneer': 'Dairy', 'ghee': 'Dairy',
    'bread': 'Bakery', 'bakery': 'Bakery', 'biscuit': 'Bakery',
    'cake': 'Bakery', 'cookie': 'Bakery', 'cracker': 'Bakery',
    'beverage': 'Beverages', 'juice': 'Beverages', 'water': 'Beverages',
    'soda': 'Beverages', 'tea': 'Beverages', 'coffee': 'Beverages', 'drink': 'Beverages',
    'meat': 'Meat & Seafood', 'chicken': 'Meat & Seafood',
    'fish': 'Meat & Seafood', 'seafood': 'Meat & Seafood',
    'vegetable': 'Vegetables', 'fruit': 'Fruits',
    'snack': 'Snacks', 'chip': 'Snacks', 'chocolate': 'Snacks',
    'candy': 'Snacks', 'namkeen': 'Snacks',
    'flour': 'Flours', 'atta': 'Flours', 'maida': 'Flours',
    'rice': 'Grains & Pulses', 'pasta': 'Grains & Pulses',
    'cereal': 'Grains & Pulses', 'pulse': 'Grains & Pulses',
    'noodle': 'Grains & Pulses', 'dal': 'Grains & Pulses',
    'sauce': 'Condiments & Sauces', 'oil': 'Condiments & Sauces',
    'ketchup': 'Condiments & Sauces', 'pickle': 'Condiments & Sauces',
    'jam': 'Condiments & Sauces', 'honey': 'Condiments & Sauces',
    'spice': 'Spices & Herbs', 'masala': 'Spices & Herbs',
    'turmeric': 'Spices & Herbs', 'chilli': 'Spices & Herbs', 'salt': 'Spices & Herbs',
    'frozen': 'Frozen',
}

def guess_category(name):
    name_lower = name.lower()
    for kw, cat in CAT_MAP.items():
        if kw in name_lower:
            return cat
    return 'Other'

def load_barcode_db():
    db = dict(BUILTIN_BARCODES)
    if os.path.exists(BARCODE_DB_FILE):
        with open(BARCODE_DB_FILE) as f:
            custom = json.load(f)
        db.update(custom)
    return db

def save_barcode_db(db):
    # Only save user-added barcodes (not built-in ones)
    custom = {k: v for k, v in db.items() if k not in BUILTIN_BARCODES}
    with open(BARCODE_DB_FILE, 'w') as f:
        json.dump(custom, f, indent=2)

def lookup_barcode_online(barcode):
    # 1. Check local DB first (instant)
    db = load_barcode_db()
    if barcode in db:
        entry = db[barcode]
        name, category = entry[0], entry[1]
        shelf_days = smart_shelf_life(name)
        expiry_date = str(date.today() + timedelta(days=shelf_days))
        return {'found': True, 'name': name, 'category': category,
                'shelf_days': shelf_days, 'expiry_date': expiry_date, 'source': 'local'}

    # 2. Try Open Food Facts API
    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'FreshGuard/1.0'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        if data.get('status') == 1:
            product = data.get('product', {})
            # Try multiple name fields
            name = (product.get('product_name_en') or
                    product.get('product_name') or
                    product.get('abbreviated_product_name') or
                    product.get('generic_name_en') or
                    product.get('generic_name') or '').strip()
            if not name:
                return {'found': False}

            # Get category from tags
            categories_raw = product.get('categories_tags') or []
            category = 'Other'
            for tag in categories_raw:
                tag_lower = tag.lower()
                for key, val in CAT_MAP.items():
                    if key in tag_lower:
                        category = val
                        break
                if category != 'Other':
                    break
            # Fallback: guess from name
            if category == 'Other':
                category = guess_category(name)

            shelf_days = smart_shelf_life(name)
            expiry_date = str(date.today() + timedelta(days=shelf_days))
            # ✅ Auto-save to local DB so it's found instantly next time
            db[barcode] = (name, category)
            save_barcode_db(db)
            return {'found': True, 'name': name, 'category': category,
                    'shelf_days': shelf_days, 'expiry_date': expiry_date,
                    'source': 'openfoodfacts', 'auto_saved': True}
    except Exception:
        pass
    return {'found': False}


# ── Auto-save unknown barcode when user manually registers it ─────────────────
@app.route('/api/barcode/save', methods=['POST'])
def auto_save_barcode():
    """Called from frontend when user scans unknown barcode and fills product name"""
    if not current_user():
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    data = request.get_json()
    barcode = (data.get('barcode') or '').strip()
    name    = (data.get('name') or '').strip()
    category = (data.get('category') or 'Other').strip()
    if not barcode or not name:
        return jsonify({'success': False, 'error': 'Barcode and name required'})
    db = load_barcode_db()
    db[barcode] = (name, category)
    save_barcode_db(db)
    shelf_days = smart_shelf_life(name)
    expiry_date = str(date.today() + timedelta(days=shelf_days))
    return jsonify({'success': True, 'name': name, 'category': category,
                    'shelf_days': shelf_days, 'expiry_date': expiry_date})

# ── Barcode API ───────────────────────────────────────────────────────────────
@app.route('/api/barcode')
def barcode_lookup():
    if not current_user():
        return jsonify({'found': False, 'error': 'Not logged in'}), 401
    barcode = request.args.get('code', '').strip()
    if not barcode:
        return jsonify({'found': False, 'error': 'No barcode provided'})
    result = lookup_barcode_online(barcode)
    return jsonify(result)

# ── Add barcode to local DB ───────────────────────────────────────────────────
@app.route('/add-barcode', methods=['POST'])
def add_barcode():
    if not current_user(): return redirect(url_for('login'))
    barcode = request.form.get('barcode', '').strip()
    name    = request.form.get('product_name', '').strip()
    category = request.form.get('category', 'Other')
    if not barcode or not name:
        flash('Barcode and product name are required.', 'error')
        return redirect(url_for('index'))
    db = load_barcode_db()
    db[barcode] = (name, category)
    save_barcode_db(db)
    flash(f'✅ "{name}" (barcode: {barcode}) added to database! You can now scan it.', 'success')
    return redirect(url_for('index'))

# ── Auth ──────────────────────────────────────────────────────────────────────
def send_welcome_email(to_email, user_name, mobile):
    """Send rich welcome email on registration — always attempted in background."""
    def _send():
        try:
            joined = str(date.today())
            msg = MIMEMultipart('alternative')
            msg['Subject'] = '🥦 Welcome to FreshGuard — Your Smart Grocery Tracker is Ready!'
            msg['From']    = f'FreshGuard <{SENDER_EMAIL}>'
            msg['To']      = to_email

            html = f"""
            <div style="font-family:Arial,sans-serif;background:#0d1117;color:#e6edf3;max-width:580px;margin:0 auto;border-radius:16px;overflow:hidden;border:1px solid #30363d;">
              <div style="background:linear-gradient(135deg,#1a3a22,#0d2347);padding:36px 30px;text-align:center;">
                <div style="font-size:3rem;margin-bottom:10px;">🥦</div>
                <div style="font-size:1.7rem;font-weight:800;letter-spacing:1.5px;">FreshGuard</div>
                <div style="font-size:0.9rem;opacity:0.75;margin-top:6px;">Smart Grocery Expiry Tracker</div>
              </div>
              <div style="background:#1c2330;padding:22px 30px;border-bottom:1px solid #30363d;">
                <p style="margin:0;font-size:1.05rem;">Hi <strong>{user_name}</strong> 👋,</p>
                <p style="margin:10px 0 0;font-size:0.88rem;color:#8b949e;line-height:1.7;">
                  Welcome to <strong>FreshGuard</strong>! Your account is ready.
                  Start tracking your groceries — we'll make sure nothing goes to waste!
                </p>
              </div>
              <div style="padding:24px 30px;">
                <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px 20px;margin-bottom:22px;">
                  <div style="font-size:0.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:0.09em;margin-bottom:12px;">📋 Your Account Details</div>
                  <table style="width:100%;border-collapse:collapse;">
                    <tr><td style="padding:7px 0;font-size:0.83rem;color:#8b949e;width:38%;">👤 Full Name</td>
                        <td style="padding:7px 0;font-size:0.83rem;font-weight:700;">{user_name}</td></tr>
                    <tr><td style="padding:7px 0;font-size:0.83rem;color:#8b949e;">📧 Email</td>
                        <td style="padding:7px 0;font-size:0.83rem;font-weight:700;">{to_email}</td></tr>
                    <tr><td style="padding:7px 0;font-size:0.83rem;color:#8b949e;">📱 Mobile</td>
                        <td style="padding:7px 0;font-size:0.83rem;font-weight:700;">{mobile}</td></tr>
                    <tr><td style="padding:7px 0;font-size:0.83rem;color:#8b949e;">📅 Joined</td>
                        <td style="padding:7px 0;font-size:0.83rem;font-weight:700;">{joined}</td></tr>
                    <tr><td style="padding:7px 0;font-size:0.83rem;color:#8b949e;">🔑 Login With</td>
                        <td style="padding:7px 0;font-size:0.83rem;font-weight:700;">Your email — no password needed</td></tr>
                  </table>
                </div>
                <div style="background:#0a1929;border:1px solid #1f6feb;border-radius:12px;padding:18px 20px;margin-bottom:22px;">
                  <div style="font-size:0.75rem;font-weight:700;color:#58a6ff;margin-bottom:14px;text-transform:uppercase;letter-spacing:0.07em;">✨ Everything You Can Do with FreshGuard</div>
                  <div style="font-size:0.84rem;color:#e6edf3;line-height:1.6;display:grid;gap:9px;">
                    <div>📷 <strong>Barcode Scanner</strong> — Point your camera at any grocery packet. 500+ Indian brands (Amul, Maggi, MTR, Tata, Britannia &amp; more) recognised instantly.</div>
                    <div>📋 <strong>150+ Item Dropdown</strong> — Manually pick from Dairy, Vegetables, Fruits, Meat, Bakery, Snacks, Beverages, Frozen, Condiments, Grains, Spices and more.</div>
                    <div>🧮 <strong>Auto Expiry Calculation</strong> — No dates to type. Expiry is calculated from the day you add an item using built-in food safety data.</div>
                    <div>🔔 <strong>In-App Alerts</strong> — Colour-coded badges: 🟢 Fresh &nbsp;🟡 Expiring Soon &nbsp;🔴 Expired — see everything at a glance.</div>
                    <div>📧 <strong>Daily Email Alerts</strong> — One email per day listing items expiring or expired, so you never miss anything even when the app is closed.</div>
                    <div>🍳 <strong>Recipe Suggestions</strong> — When items are about to expire, FreshGuard suggests recipes to use them up — shown in-app and in every email alert.</div>
                    <div>⭐ <strong>Custom Items</strong> — Add any item not in the built-in list with your own shelf life. Saved permanently and appears in your dropdown.</div>
                    <div>🔍 <strong>Search &amp; Filter</strong> — Search by name or filter by Fresh / Expiring / Expired to focus on what needs attention.</div>
                    <div>📱 <strong>Works on Mobile</strong> — Open the HTTPS link on any phone browser. Keep-alive ping and Wake Lock keep the app running continuously.</div>
                    <div>💾 <strong>Safe Data Storage</strong> — Your items are stored in their own dedicated files — crash-safe atomic writes so data is never lost.</div>
                  </div>
                </div>
                <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px 20px;">
                  <div style="font-size:0.72rem;color:#8b949e;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:8px;">🚀 How to Use</div>
                  <ol style="margin:0;padding-left:18px;font-size:0.83rem;color:#e6edf3;line-height:2.1;">
                    <li>Open the FreshGuard link on your phone or computer</li>
                    <li>Log in with your email: <strong>{to_email}</strong></li>
                    <li>Tap <strong>Scan Barcode</strong> or pick from the dropdown</li>
                    <li>Your item is added — expiry calculated automatically ✅</li>
                  </ol>
                </div>
              </div>
              <div style="background:#161b22;padding:16px 30px;text-align:center;border-top:1px solid #30363d;">
                <p style="margin:0;font-size:0.73rem;color:#484f58;line-height:1.7;">
                  You are receiving this because you just created a FreshGuard account.<br/>
                  Daily expiry alerts will be sent to this address automatically.<br/>
                  To log in, enter your email at the app — no password required.
                </p>
              </div>
            </div>
            """

            plain = (
                f"Welcome to FreshGuard, {user_name}!\n\n"
                f"Account Details:\n"
                f"  Name:    {user_name}\n"
                f"  Email:   {to_email}\n"
                f"  Mobile:  {mobile}\n"
                f"  Joined:  {joined}\n"
                f"  Login:   Enter your email — no password needed\n\n"
                f"What You Can Do:\n"
                f"  - Scan barcodes (500+ Indian grocery brands)\n"
                f"  - Add 150+ items from dropdown\n"
                f"  - Auto expiry calculation — no dates needed\n"
                f"  - In-app colour-coded alerts\n"
                f"  - Daily email alerts for expiring/expired items\n"
                f"  - Recipe suggestions when items expire soon\n"
                f"  - Add custom items with your own shelf life\n"
                f"  - Search & filter your grocery list\n"
                f"  - Mobile-optimised with continuous keep-alive\n"
                f"  - Crash-safe atomic file writes\n\n"
                f"How to Use:\n"
                f"  1. Open the FreshGuard link\n"
                f"  2. Log in with: {to_email}\n"
                f"  3. Scan a barcode or pick from the dropdown\n"
                f"  4. Done — expiry is set automatically!\n"
            )

            msg.attach(MIMEText(plain, 'plain'))
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASS)
                server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
            print(f'[FreshGuard] Welcome email sent to {to_email}')
        except Exception as e:
            print(f'[FreshGuard] Welcome email failed: {e}')

    threading.Thread(target=_send, daemon=True).start()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        mobile = request.form.get('mobile', '').strip()
        email  = request.form.get('email', '').strip().lower()

        # Validate all fields
        if not name:
            flash('Please enter your full name.', 'error')
            return redirect(url_for('register'))
        if not re.match(r'^\d{10}$', mobile):
            flash('Please enter a valid 10-digit mobile number.', 'error')
            return redirect(url_for('register'))
        if not re.match(r'^[\w.+-]+@[\w-]+\.[\w.]+$', email):
            flash('Please enter a valid email address.', 'error')
            return redirect(url_for('register'))

        users = all_users()
        if user_exists(email):
            flash('An account with this email already exists. Please log in.', 'error')
            return redirect(url_for('login'))

        # Check if mobile already used
        if any(u.get('mobile') == mobile for u in users.values()):
            flash('This mobile number is already registered. Please log in.', 'error')
            return redirect(url_for('login'))

        udata = {
            'name':   name,
            'email':  email,
            'mobile': mobile,
            'joined': str(date.today())
        }
        save_user(email, udata)
        session['email'] = email
        session['name']  = name

        # Always send welcome email (runs in background — never blocks the page)
        send_welcome_email(email, name, mobile)

        flash(f'Welcome, {name}! Your account has been created 🎉', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not re.match(r'^[\w.+-]+@[\w-]+\.[\w.]+$', email):
            flash('Please enter a valid email address.', 'error')
            return redirect(url_for('login'))
        udata = load_user(email)
        if not udata:
            flash('No account found with this email. Please register first.', 'error')
            return redirect(url_for('register'))
        session['email'] = email
        session['name']  = udata['name']
        flash(f'Welcome back, {udata["name"]}! 👋', 'success')
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if not current_user(): return redirect(url_for('login'))
    email = current_user()
    items = get_user_items(email)
    custom = load_json(CUSTOM_ITEMS_FILE, {})
    filter_status = request.args.get('filter', 'all')
    search = request.args.get('search', '').lower()

    for item in items:
        item['days'] = days_until(item['expiry'])
        item['status'] = get_status(item['days'])
        item['emoji'] = CATEGORY_EMOJI.get(item['category'], '📦')

    expiring_soon = [i for i in items if i['status'] == 'expiring']
    expired_items = [i for i in items if i['status'] == 'expired']

    notifications = []
    for i in expired_items:
        notifications.append({'type': 'expired', 'msg': f'⛔ {i["name"]} has expired! Please discard it.'})
    for i in expiring_soon:
        d = i['days']
        label = 'today' if d == 0 else f'in {d} day{"s" if d>1 else ""}'
        notifications.append({'type': 'expiring', 'msg': f'⏰ {i["name"]} expires {label}. Use it soon!'})

    # ── Send email notification — max once per day per user ─────────────────
    user_name = session.get('name', 'there')
    if expiring_soon or expired_items:
        udata     = load_user(email) or {}
        last_sent = udata.get('last_email_sent', '')
        today_str = str(date.today())
        if last_sent != today_str and SENDER_EMAIL != 'your_email@gmail.com':
            send_expiry_email(email, user_name, expiring_soon, expired_items)
            udata['last_email_sent'] = today_str
            save_user(email, udata)

    stats = {
        'total': len(items),
        'fresh': sum(1 for i in items if i['status'] == 'fresh'),
        'expiring': len(expiring_soon),
        'expired': len(expired_items),
    }

    filtered = items
    if filter_status != 'all':
        filtered = [i for i in items if i['status'] == filter_status]
    if search:
        filtered = [i for i in filtered if search in i['name'].lower() or search in i['category'].lower()]

    all_grocery = {cat: list(lst) for cat, lst in GROCERY_ITEMS.items()}
    for cat, cat_items in custom.items():
        names = [ci['name'] for ci in cat_items]
        all_grocery[cat] = all_grocery.get(cat, []) + names

    shelf_life_map = dict(SHELF_LIFE)
    for cat_items in custom.values():
        for ci in cat_items:
            shelf_life_map[ci['name']] = ci['shelf_life']

    return render_template('index.html',
        items=filtered, stats=stats,
        grocery_items=all_grocery,
        shelf_life_map=shelf_life_map,
        category_emoji=CATEGORY_EMOJI,
        location_icon=LOCATION_ICON,
        filter_status=filter_status,
        search=search,
        notifications=notifications,
        expiring_soon=expiring_soon,
        today=str(date.today()),
        user_name=session.get('name'),
        user_email=email
    )

# ── Add item ──────────────────────────────────────────────────────────────────
@app.route('/add', methods=['POST'])
def add_item():
    if not current_user(): return redirect(url_for('login'))
    email = current_user()
    items = get_user_items(email)
    category = request.form.get('category')
    name = request.form.get('item_name', '').strip()
    location = request.form.get('location', 'Fridge')
    quantity = request.form.get('quantity', '')

    if not name or not category:
        flash('Please provide item name and category.', 'error')
        return redirect(url_for('index'))

    shelf_days = get_shelf_days(name)
    expiry = str(date.today() + timedelta(days=shelf_days))

    items.insert(0, {
        'id': int(datetime.now().timestamp() * 1000),
        'name': name, 'category': category,
        'added': str(date.today()),
        'expiry': expiry,
        'shelf_life': shelf_days,
        'location': location,
        'quantity': quantity
    })
    save_user_items(email, items)
    flash(f'✅ "{name}" added! Expires in {shelf_days} days ({expiry}).', 'success')
    return redirect(url_for('index'))

# ── Add custom item ───────────────────────────────────────────────────────────
@app.route('/add-custom-item', methods=['POST'])
def add_custom_item():
    if not current_user(): return redirect(url_for('login'))
    custom = load_json(CUSTOM_ITEMS_FILE, {})
    name = request.form.get('custom_name', '').strip()
    category = request.form.get('custom_category', 'Other')
    try:
        shelf_life = int(request.form.get('custom_shelf_life', 7))
    except:
        shelf_life = 7
    if not name:
        flash('Please enter an item name.', 'error')
        return redirect(url_for('index'))
    if name in SHELF_LIFE:
        flash(f'"{name}" already exists in built-in list!', 'error')
        return redirect(url_for('index'))
    if category not in custom: custom[category] = []
    if any(ci['name'] == name for ci in custom[category]):
        flash(f'"{name}" already exists!', 'error')
        return redirect(url_for('index'))
    custom[category].append({'name': name, 'shelf_life': shelf_life})
    save_json(CUSTOM_ITEMS_FILE, custom)
    flash(f'⭐ "{name}" added with {shelf_life}-day shelf life!', 'success')
    return redirect(url_for('index'))

# ── Delete item ───────────────────────────────────────────────────────────────
@app.route('/delete/<int:item_id>')
def delete_item(item_id):
    if not current_user(): return redirect(url_for('login'))
    email = current_user()
    items = get_user_items(email)
    name = next((i['name'] for i in items if i['id'] == item_id), 'Item')
    items = [i for i in items if i['id'] != item_id]
    save_user_items(email, items)
    flash(f'✔ "{name}" marked as used.', 'success')
    return redirect(url_for('index'))

# ── Recipe API ────────────────────────────────────────────────────────────────
@app.route('/api/recipes')
def get_recipes():
    if not current_user(): return jsonify({'error': 'Not logged in'}), 401
    item_name = request.args.get('item', '')
    if item_name in RECIPE_SUGGESTIONS:
        return jsonify({'recipes': RECIPE_SUGGESTIONS[item_name]})
    fallback = [
        f"Quick stir fry with {item_name}",
        f"Soup using {item_name}",
        f"{item_name} salad",
        f"Baked {item_name}",
        f"Curry with {item_name}"
    ]
    return jsonify({'recipes': fallback})

# ── Keep-alive ping ───────────────────────────────────────────────────────────
@app.route('/ping')
def ping():
    """Polled every 30s by the browser to keep connection alive on mobile."""
    return jsonify({'status': 'ok', 'time': str(datetime.now())})

if __name__ == '__main__':
    print("\n" + "="*60)
    print("  🥦 FreshGuard is running!")
    print("="*60)
    print("  Local:   http://localhost:5000")
    print("  Network: http://0.0.0.0:5000")
    print("\n  📱 For mobile (camera barcode scanning):")
    print("     Run: python run.py  — auto-generates HTTPS link")
    print("="*60 + "\n")
    app.run(debug=False, host='0.0.0.0', port=5000,
            use_reloader=False, threaded=True)
