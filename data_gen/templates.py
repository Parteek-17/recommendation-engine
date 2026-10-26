"""Word lists that make the catalog look like a real Indian marketplace.

Each sub-category has brands and product TYPES. A product title is built as
    brand + type + 2-3 attributes      e.g. "boAt Wireless Earbuds (40H Playback, Black)"

A type's `kind` groups products that do the same job ("smartphone", "phone_cover").
Kinds drive the ground truth:
  * substitutes = same sub-category + same kind + similar price
  * complements = COMPLEMENT_RULES below (kind -> kinds that go well with it)

Category ids C001-C024 match data/sample/ so both datasets use the same names.
"""

from __future__ import annotations

from typing import NamedTuple


class ProductType(NamedTuple):
    name: str                                   # words used in the title
    kind: str                                   # what job it does (for substitutes/complements)
    lo: int                                     # price range in ₹ (log-uniform inside)
    hi: int
    attrs: list[list[str]]                      # pick one value from each list
    rep: tuple[int, int] | None = None          # repurchase_days range -> consumable
    query: str | None = None                    # what a shopper types in search
    brands: list[str] | None = None             # overrides the sub-category's brands
    adult: bool = False                         # age_restricted


PARENTS: dict[str, str] = {
    "C001": "Electronics", "C002": "Fashion", "C003": "Home & Kitchen", "C004": "Beauty",
    "C005": "Grocery", "C006": "Sports", "C007": "Books", "C008": "Toys",
}

COLORS = ["Black", "Blue", "Grey", "White", "Green", "Red", "Navy", "Maroon"]
PHONES = ["for Galaxy M35", "for Redmi Note 13", "for iPhone 15", "for OnePlus Nord CE4", "for Realme 12 Pro",
          "for Vivo T3", "for iQOO Z9"]
MEN_SIZES = ["S", "M", "L", "XL"]

# sub_id -> dict(name, parent, weight (how popular the category is), brands, types)
SUBCATEGORIES: dict[str, dict] = {
    # ----------------------------------------------------------------- Electronics
    "C009": dict(name="Mobiles", parent="C001", weight=10, brands=[
        "Samsung", "Redmi", "Realme", "OnePlus", "iQOO", "Motorola", "Vivo", "Oppo", "Nothing", "POCO", "Lava"],
        types=[
            ProductType("5G Smartphone", "smartphone", 9999, 34999,
                        [["6GB/128GB", "8GB/128GB", "8GB/256GB", "12GB/256GB"], ["Midnight Black", "Ocean Blue",
                                                                                   "Forest Green", "Silver"]],
                        query="5g phone"),
            ProductType("Smartphone", "smartphone", 6499, 12999,
                        [["4GB/64GB", "4GB/128GB", "6GB/128GB"], ["Black", "Blue", "Gold"]], query="phone"),
            ProductType("Flagship 5G Smartphone", "smartphone", 39999, 79999,
                        [["12GB/256GB", "12GB/512GB", "16GB/512GB"], ["Phantom Black", "Titanium Grey", "Cream"]],
                        query="flagship phone"),
            ProductType("Keypad Feature Phone", "feature_phone", 999, 2499,
                        [["Dual SIM", "Single SIM"], ["Black", "Blue", "Red"]], query="keypad phone",
                        brands=["Nokia", "Lava", "itel", "Jio"]),
        ]),
    "C010": dict(name="Mobile Accessories", parent="C001", weight=9, brands=[
        "boAt", "Spigen", "Ambrane", "Portronics", "Noise", "Mi", "Samsung", "URBN", "JBL", "Realme"],
        types=[
            ProductType("Back Cover", "phone_cover", 199, 1299, [PHONES, ["Transparent", "Matte Black",
                                                                        "Rugged Armor"]], query="phone cover",
                        brands=["Spigen", "Amazon Basics", "Pikkme", "TheGiftKart", "Caseology"]),
            ProductType("20W Fast Charger", "charger", 399, 1999, [["Type-C", "USB-A + Type-C"],
                                                                  ["with Cable", "Adapter Only"]],
                        query="fast charger"),
            ProductType("Wireless Earbuds", "earbuds", 799, 4999, [["30H Playback", "40H Playback", "50H Playback"],
                                                                  ["Black", "White", "Blue"]], query="earbuds"),
            ProductType("Tempered Glass Screen Guard", "screen_guard", 149, 599, [PHONES, ["Pack of 1", "Pack of 2"]],
                        query="tempered glass"),
            ProductType("Power Bank", "power_bank", 699, 2999, [["10000mAh", "20000mAh"], ["22.5W", "20W PD"]],
                        query="power bank"),
            ProductType("Smartwatch", "smartwatch", 1299, 5999, [['1.83" Display', '1.96" AMOLED'],
                                                                ["Black", "Silver", "Pink"]], query="smartwatch"),
        ]),
    "C025": dict(name="Laptops", parent="C001", weight=4, brands=[
        "HP", "Dell", "Lenovo", "ASUS", "Acer", "MSI", "Samsung", "Infinix"],
        types=[
            ProductType("Thin & Light Laptop", "laptop", 32999, 79999,
                        [["Intel Core i3", "Intel Core i5", "AMD Ryzen 5", "AMD Ryzen 7"],
                         ["8GB/512GB SSD", "16GB/512GB SSD", "16GB/1TB SSD"], ["14 inch", "15.6 inch"]],
                        query="laptop"),
            ProductType("Gaming Laptop", "laptop", 59999, 80000,
                        [["RTX 3050", "RTX 4050", "RTX 4060"], ["16GB/512GB SSD", "16GB/1TB SSD"]],
                        query="gaming laptop"),
            ProductType("Chromebook", "laptop", 14999, 29999, [["4GB/64GB", "8GB/128GB"], ["11.6 inch", "14 inch"]],
                        query="chromebook"),
            ProductType("2-in-1 Touchscreen Laptop", "laptop", 39999, 74999,
                        [["Intel Core i5", "Intel Core i7"], ["8GB/512GB SSD", "16GB/512GB SSD"]],
                        query="2 in 1 laptop"),
        ]),
    "C026": dict(name="Computer Accessories", parent="C001", weight=5, brands=[
        "Logitech", "HP", "Dell", "Zebronics", "Portronics", "Lenovo", "TVS Electronics"],
        types=[
            ProductType("Wireless Mouse", "mouse", 349, 1999, [["2.4GHz", "Bluetooth", "Silent Click"],
                                                              ["Black", "Grey", "White"]], query="wireless mouse"),
            ProductType("Keyboard", "keyboard", 499, 3999, [["Wired", "Wireless", "Mechanical"],
                                                           ["Full Size", "Compact"]], query="keyboard"),
            ProductType("Laptop Backpack", "laptop_bag", 799, 3499, [["15.6 inch", "14 inch"], ["Black", "Grey",
                                                                                               "Navy"]],
                        query="laptop bag", brands=["Wildcraft", "American Tourister", "Skybags", "Safari", "HP"]),
            ProductType("USB Pen Drive", "pen_drive", 299, 1499, [["32GB", "64GB", "128GB"], ["USB 3.0", "Type-C"]],
                        query="pen drive", brands=["SanDisk", "HP", "Kingston", "Samsung"]),
            ProductType("External Hard Drive", "hard_drive", 3999, 8999, [["1TB", "2TB"], ["USB 3.0"]],
                        query="external hard disk", brands=["Seagate", "WD", "Toshiba"]),
            ProductType("HD Webcam", "webcam", 999, 4999, [["720p", "1080p"], ["with Mic"]], query="webcam"),
        ]),
    "C027": dict(name="Cameras & Photography", parent="C001", weight=2, brands=[
        "Canon", "Nikon", "Sony", "Fujifilm"],
        types=[
            ProductType("Mirrorless Camera", "camera", 39999, 79999,
                        [["with 18-55mm Lens", "with 15-45mm Lens", "Body Only"], ["24.2MP", "26MP"]],
                        query="mirrorless camera"),
            ProductType("DSLR Camera", "camera", 34999, 69999, [["with 18-55mm Lens", "Dual Lens Kit"],
                                                               ["24.1MP", "24.2MP"]], query="dslr camera"),
            ProductType("Action Camera", "action_camera", 4999, 39999, [["4K 60fps", "5.3K", "1080p"],
                                                                       ["Waterproof"]],
                        query="action camera", brands=["GoPro", "DJI", "SJCAM", "Insta360"]),
            ProductType("Memory Card", "memory_card", 349, 2999, [["64GB", "128GB", "256GB"], ["U3 V30", "A2"]],
                        query="memory card", brands=["SanDisk", "Samsung", "Kingston", "HP"]),
            ProductType("Tripod", "tripod", 499, 2999, [["55 inch", "60 inch", "Mini Table-top"],
                                                       ["with Phone Holder", "Aluminium"]],
                        query="tripod", brands=["DIGITEK", "Osaka", "Simpex", "Amazon Basics"]),
            ProductType("Camera Bag", "camera_bag", 699, 2999, [["Shoulder", "Backpack"], ["Black", "Grey"]],
                        query="camera bag", brands=["Lowepro", "DIGITEK", "Caden"]),
        ]),
    # ----------------------------------------------------------------- Fashion
    "C011": dict(name="Men's Clothing", parent="C002", weight=8, brands=[
        "Roadster", "Allen Solly", "Levi's", "Peter England", "U.S. Polo Assn.", "Louis Philippe", "Jack & Jones",
        "Van Heusen", "Wrogn", "Highlander"],
        types=[
            ProductType("Cotton Slim Fit T-shirt", "tshirt", 249, 1299, [MEN_SIZES, COLORS], query="t shirt for men"),
            ProductType("Denim Jeans", "jeans", 699, 2999, [["Slim Fit", "Regular Fit", "Skinny Fit"],
                                                           ["Dark Blue", "Light Blue", "Black"]], query="jeans for men"),
            ProductType("Casual Shirt", "shirt", 499, 2499, [["Checked", "Solid", "Printed"], COLORS],
                        query="casual shirt"),
            ProductType("Cotton Kurta", "kurta", 499, 2999, [["Straight", "Short"], ["White", "Maroon", "Mustard"]],
                        query="kurta for men", brands=["Manyavar", "FabIndia", "Peter England", "Sojanya"]),
            ProductType("Formal Trousers", "trousers", 799, 2499, [["Slim Fit", "Regular Fit"],
                                                                  ["Charcoal", "Navy", "Beige"]],
                        query="formal trousers"),
        ]),
    "C028": dict(name="Women's Clothing", parent="C002", weight=8, brands=[
        "Biba", "W", "Libas", "AND", "Global Desi", "Aurelia", "Only", "Vero Moda", "FabIndia", "Anouk"],
        types=[
            ProductType("Printed Kurta Set", "kurta_set", 699, 3999, [["Cotton", "Rayon"], ["Pink", "Blue", "Yellow",
                                                                                           "Green"]],
                        query="kurta set"),
            ProductType("Anarkali Kurta", "kurta_set", 899, 4499, [["Embroidered", "Printed"], ["Maroon", "Teal",
                                                                                               "Peach"]],
                        query="anarkali"),
            ProductType("Saree with Blouse Piece", "saree", 599, 4999, [["Georgette", "Cotton", "Silk Blend"],
                                                                       ["Red", "Green", "Purple"]], query="saree"),
            ProductType("Maxi Dress", "dress", 699, 2999, [["Floral", "Solid"], ["Black", "Blue", "White"]],
                        query="maxi dress"),
            ProductType("Ankle Length Leggings", "leggings", 249, 799, [["Pack of 1", "Pack of 2"],
                                                                       ["Black", "Navy", "Beige"]],
                        query="leggings"),
        ]),
    "C012": dict(name="Footwear", parent="C002", weight=7, brands=[
        "Puma", "Campus", "Bata", "Red Tape", "Sparx", "Nike", "Adidas", "Asics", "Woodland", "Metro", "Liberty"],
        types=[
            ProductType("Running Shoes", "running_shoes", 999, 4999, [["Men", "Women"], ["Black", "Grey", "Blue",
                                                                                         "White"]],
                        query="running shoes"),
            ProductType("Casual Sneakers", "sneakers", 799, 4999, [["Men", "Women"], ["White", "Black", "Olive"]],
                        query="sneakers"),
            ProductType("Flip Flops", "slippers", 199, 999, [["Men", "Women"], ["Black", "Blue", "Grey"]],
                        query="slippers"),
            ProductType("Formal Shoes", "formal_shoes", 999, 4999, [["Derby", "Oxford", "Loafers"],
                                                                   ["Black", "Brown", "Tan"]], query="formal shoes"),
            ProductType("Sandals", "sandals", 399, 1999, [["Men", "Women"], ["Brown", "Black"]], query="sandals"),
        ]),
    "C029": dict(name="Fashion Accessories", parent="C002", weight=5, brands=[
        "Fastrack", "Titan", "Casio", "Lavie", "Caprese", "Wildhorn"],
        types=[
            ProductType("Analog Watch", "watch", 799, 4999, [["Men", "Women"], ["Leather Strap", "Metal Strap"],
                                                            ["Black Dial", "Blue Dial", "White Dial"]],
                        query="watch", brands=["Titan", "Fastrack", "Casio", "Sonata", "Timex"]),
            ProductType("Leather Wallet", "wallet", 299, 1499, [["Bi-fold", "Tri-fold"], ["Brown", "Black", "Tan"]],
                        query="wallet for men", brands=["Wildhorn", "Woodland", "Allen Solly", "Hidesign"]),
            ProductType("Handbag", "handbag", 699, 3499, [["Tote", "Sling", "Satchel"], ["Black", "Beige", "Pink"]],
                        query="handbag", brands=["Lavie", "Caprese", "Baggit", "Hidesign"]),
            ProductType("Sunglasses", "sunglasses", 499, 2999, [["Aviator", "Wayfarer", "Round"], ["UV Protected"]],
                        query="sunglasses", brands=["Fastrack", "IDEE", "Vincent Chase", "Ray-Ban"]),
            ProductType("Cotton Socks (Pack of 3)", "socks", 199, 599, [["Ankle", "Crew"], ["Assorted", "Black"]],
                        query="socks", brands=["Jockey", "Bonjour", "Puma", "Van Heusen"]),
            ProductType("Leather Belt", "belt", 299, 1499, [["Formal", "Casual"], ["Black", "Brown"]],
                        query="belt for men", brands=["Woodland", "Allen Solly", "Louis Philippe", "Wildhorn"]),
        ]),
    # ----------------------------------------------------------------- Home & Kitchen
    "C013": dict(name="Kitchen Appliances", parent="C003", weight=5, brands=[
        "Prestige", "Bajaj", "Pigeon", "Philips", "Butterfly", "Havells", "Morphy Richards", "Usha", "Inalsa"],
        types=[
            ProductType("Mixer Grinder", "mixer", 1499, 6999, [["500W", "750W", "1000W"], ["3 Jars", "4 Jars"]],
                        query="mixer grinder"),
            ProductType("Electric Kettle", "kettle", 549, 1999, [["1.5L", "1.8L"], ["Steel", "Glass"]],
                        query="electric kettle"),
            ProductType("Induction Cooktop", "induction", 1499, 3999, [["1600W", "2000W"], ["Touch Panel",
                                                                                           "Push Button"]],
                        query="induction"),
            ProductType("Air Fryer", "air_fryer", 2999, 8999, [["4.2L", "5L", "6.5L"], ["Digital", "Manual"]],
                        query="air fryer"),
            ProductType("Water Purifier", "purifier", 6999, 15000, [["RO+UV", "RO+UV+UF"], ["7L", "8L"]],
                        query="water purifier", brands=["Kent", "Aquaguard", "Livpure", "Havells"]),
        ]),
    "C030": dict(name="Cookware & Dining", parent="C003", weight=4, brands=[
        "Prestige", "Hawkins", "Pigeon", "Milton", "Cello", "Borosil", "Vinod", "Wonderchef", "Tupperware"],
        types=[
            ProductType("Pressure Cooker", "pressure_cooker", 899, 2999, [["3L", "5L"], ["Aluminium",
                                                                                        "Stainless Steel"]],
                        query="pressure cooker"),
            ProductType("Non-Stick Kadhai", "kadhai", 599, 1999, [["Induction Base", "Gas Only"], ["24cm", "28cm"]],
                        query="kadhai"),
            ProductType("Lunch Box", "lunch_box", 299, 1299, [["2 Containers", "3 Containers"], ["Insulated",
                                                                                                "Steel"]],
                        query="lunch box"),
            ProductType("Dinner Set", "dinner_set", 999, 4999, [["18 pcs", "24 pcs", "32 pcs"], ["Opalware",
                                                                                                "Melamine"]],
                        query="dinner set"),
            ProductType("Chef Knife Set", "knife_set", 499, 2999, [["3 pcs", "5 pcs"], ["with Stand"]],
                        query="knife set", adult=True),
            ProductType("Silicone Spatula Set", "spatula", 199, 699, [["3 pcs", "5 pcs"], ["Heat Resistant"]],
                        query="spatula"),
        ]),
    "C014": dict(name="Home Decor", parent="C003", weight=4, brands=[
        "Bombay Dyeing", "Ajanta", "Home Centre", "Story@Home", "Spaces", "D'Decor", "Philips"],
        types=[
            ProductType("Cotton Double Bedsheet with 2 Pillow Covers", "bedsheet", 499, 2499,
                        [["Floral", "Geometric", "Solid"], ["Blue", "Pink", "Grey"]], query="bedsheet"),
            ProductType("Wall Clock", "clock", 349, 1999, [["Round", "Square"], ["Wooden", "Silent Sweep"]],
                        query="wall clock"),
            ProductType("Cushion Covers (Set of 5)", "cushion", 299, 999, [["16x16 inch", "12x12 inch"],
                                                                         ["Velvet", "Cotton"]],
                        query="cushion covers"),
            ProductType("LED String Lights", "lights", 199, 799, [["10m", "15m"], ["Warm White", "Multicolour"]],
                        query="string lights"),
            ProductType("Blackout Curtains (Set of 2)", "curtains", 599, 1999, [["5 ft", "7 ft"],
                                                                              ["Grey", "Beige", "Maroon"]],
                        query="curtains"),
        ]),
    "C031": dict(name="Cleaning & Household", parent="C003", weight=4, brands=[
        "Surf Excel", "Ariel", "Tide", "Vim", "Harpic", "Lizol", "Colin", "Scotch-Brite", "Good Knight"],
        types=[
            ProductType("Detergent Powder", "detergent", 199, 999, [["1kg", "2kg", "4kg"], ["Matic", "Easy Wash"]],
                        rep=(30, 45), query="detergent", brands=["Surf Excel", "Ariel", "Tide", "Rin", "Wheel"]),
            ProductType("Dishwash Liquid", "dishwash", 99, 399, [["500ml", "750ml", "2L"], ["Lemon", "Pudina"]],
                        rep=(20, 35), query="dishwash liquid", brands=["Vim", "Pril", "Exo"]),
            ProductType("Toilet Cleaner", "toilet_cleaner", 99, 349, [["500ml", "1L"], ["Pack of 1", "Pack of 2"]],
                        rep=(30, 45), query="toilet cleaner", brands=["Harpic", "Domex", "Lizol"]),
            ProductType("Floor Cleaner", "floor_cleaner", 149, 499, [["1L", "2L"], ["Citrus", "Floral", "Jasmine"]],
                        rep=(30, 45), query="floor cleaner", brands=["Lizol", "Harpic", "Dettol"]),
            ProductType("Mosquito Repellent Refill", "repellent", 79, 299, [["Pack of 2", "Pack of 4"],
                                                                           ["45 Nights"]],
                        rep=(30, 40), query="mosquito refill", brands=["Good Knight", "All Out", "Mortein"]),
            ProductType("Garbage Bags", "garbage_bags", 99, 299, [["Medium", "Large"], ["90 Bags", "120 Bags"]],
                        rep=(20, 30), query="garbage bags", brands=["Scotch-Brite", "Ezee", "Presto"]),
        ]),
    # ----------------------------------------------------------------- Beauty
    "C015": dict(name="Skincare", parent="C004", weight=6, brands=[
        "Minimalist", "Mamaearth", "Lakme", "Nivea", "Himalaya", "Plum", "The Derma Co", "Cetaphil", "Neutrogena",
        "Pond's", "Dot & Key"],
        types=[
            ProductType("Vitamin C Face Serum", "serum", 299, 899, [["30ml", "20ml"], ["for Glowing Skin"]],
                        rep=(45, 60), query="vitamin c serum"),
            ProductType("Sunscreen SPF 50", "sunscreen", 249, 799, [["50g", "80g"], ["Matte Finish", "Gel"]],
                        rep=(30, 45), query="sunscreen"),
            ProductType("Face Wash", "face_wash", 149, 499, [["100ml", "150ml"], ["Oil Control", "Hydrating"]],
                        rep=(30, 45), query="face wash"),
            ProductType("Moisturiser", "moisturiser", 199, 799, [["100ml", "200ml"], ["Oily Skin", "Dry Skin"]],
                        rep=(30, 60), query="moisturiser"),
            ProductType("Body Lotion", "body_lotion", 199, 599, [["400ml", "600ml"], ["Cocoa", "Aloe"]],
                        rep=(30, 45), query="body lotion"),
        ]),
    "C016": dict(name="Haircare", parent="C004", weight=5, brands=[
        "Dove", "Parachute", "Indulekha", "Head & Shoulders", "Bajaj Almond", "L'Oreal Paris", "Tresemme",
        "Mamaearth", "Sunsilk", "Pantene"],
        types=[
            ProductType("Anti-Dandruff Shampoo", "shampoo", 149, 699, [["340ml", "650ml"], ["Cool Menthol",
                                                                                           "Smooth & Silky"]],
                        rep=(30, 45), query="shampoo"),
            ProductType("Hair Oil", "hair_oil", 120, 499, [["200ml", "300ml", "500ml"], ["Coconut", "Almond",
                                                                                         "Onion"]],
                        rep=(30, 45), query="hair oil"),
            ProductType("Conditioner", "conditioner", 149, 599, [["180ml", "340ml"], ["Repair", "Smooth"]],
                        rep=(30, 45), query="conditioner"),
            ProductType("Hair Serum", "hair_serum", 199, 699, [["50ml", "100ml"], ["Frizz Control"]],
                        rep=(45, 60), query="hair serum"),
            ProductType("Hair Dryer", "hair_dryer", 799, 2499, [["1200W", "1600W"], ["Foldable"]],
                        query="hair dryer", brands=["Philips", "Havells", "Vega", "Syska"]),
            ProductType("Beard Trimmer", "trimmer", 799, 2499, [["60 min Runtime", "90 min Runtime"],
                                                               ["Cordless"]],
                        query="trimmer", brands=["Philips", "Mi", "Beardo", "Havells"]),
        ]),
    "C032": dict(name="Makeup", parent="C004", weight=4, brands=[
        "Lakme", "Maybelline", "Sugar", "Swiss Beauty", "Nykaa Cosmetics", "Colorbar", "Faces Canada", "Insight"],
        types=[
            ProductType("Matte Lipstick", "lipstick", 199, 1199, [["Red", "Nude", "Pink", "Brown"], ["Long Lasting"]],
                        rep=(45, 60), query="lipstick"),
            ProductType("Kajal", "kajal", 149, 499, [["Black", "Smudge Proof"], ["0.35g"]], rep=(30, 45),
                        query="kajal"),
            ProductType("Liquid Foundation", "foundation", 299, 1499, [["Ivory", "Natural", "Sand", "Honey"],
                                                                      ["30ml"]],
                        rep=(45, 60), query="foundation"),
            ProductType("Compact Powder", "compact", 199, 699, [["Natural", "Beige"], ["9g"]], rep=(45, 60),
                        query="compact"),
            ProductType("Nail Polish (Set of 4)", "nail_polish", 199, 599, [["Pastel", "Bold", "Glitter"]],
                        rep=(45, 60), query="nail polish"),
            ProductType("Makeup Brush Set", "brush_set", 299, 1299, [["7 pcs", "12 pcs"], ["with Pouch"]],
                        query="makeup brushes"),
        ]),
    # ----------------------------------------------------------------- Grocery
    "C017": dict(name="Staples", parent="C005", weight=6, brands=[
        "Aashirvaad", "India Gate", "Tata Sampann", "Fortune", "Daawat", "Saffola", "Patanjali", "24 Mantra Organic",
        "Rajdhani"],
        types=[
            ProductType("Whole Wheat Atta", "atta", 249, 599, [["5kg", "10kg"], ["Chakki Fresh", "Multigrain"]],
                        rep=(25, 35), query="atta 5kg"),
            ProductType("Basmati Rice", "rice", 399, 1199, [["5kg", "1kg"], ["Classic", "Rozana", "Super"]],
                        rep=(30, 45), query="basmati rice"),
            ProductType("Toor Dal", "dal", 149, 249, [["1kg", "500g"], ["Unpolished"]], rep=(20, 30),
                        query="toor dal"),
            ProductType("Sunflower Oil", "oil", 149, 999, [["1L", "5L"], ["Refined", "Cold Pressed"]],
                        rep=(20, 30), query="sunflower oil"),
            ProductType("Iodised Salt", "salt", 30, 60, [["1kg"], ["Pack of 1", "Pack of 2"]], rep=(30, 45),
                        query="salt"),
            ProductType("Garam Masala", "masala", 49, 149, [["100g", "200g"], ["Pouch", "Box"]], rep=(30, 45),
                        query="garam masala", brands=["MDH", "Everest", "Catch", "Tata Sampann"]),
        ]),
    "C018": dict(name="Beverages", parent="C005", weight=4, brands=[
        "Tata Tea", "Nescafe", "Bru", "Lipton", "Red Label", "Brooke Bond", "Tetley"],
        types=[
            ProductType("Premium Tea", "tea", 149, 599, [["250g", "500g", "1kg"], ["Strong", "Elaichi"]],
                        rep=(15, 30), query="tea", brands=["Tata Tea", "Red Label", "Brooke Bond", "Wagh Bakri"]),
            ProductType("Green Tea", "tea", 149, 499, [["25 Bags", "100 Bags"], ["Lemon Honey", "Classic"]],
                        rep=(20, 30), query="green tea", brands=["Lipton", "Tetley", "Organic India"]),
            ProductType("Instant Coffee", "coffee", 99, 599, [["50g", "100g", "200g"], ["Jar", "Pouch"]],
                        rep=(20, 30), query="coffee", brands=["Nescafe", "Bru", "Continental"]),
            ProductType("Health Drink", "health_drink", 199, 499, [["500g", "1kg"], ["Chocolate", "Kesar Badam"]],
                        rep=(20, 30), query="health drink", brands=["Bournvita", "Horlicks", "Boost", "Complan"]),
            ProductType("Fruit Juice", "juice", 99, 149, [["1L"], ["Mixed Fruit", "Mango", "Orange"]],
                        rep=(15, 20), query="juice", brands=["Real", "Tropicana", "Paper Boat"]),
        ]),
    "C033": dict(name="Snacks", parent="C005", weight=5, brands=[
        "Haldiram's", "Bikaji", "Britannia", "Parle", "Cadbury", "Sunfeast", "Balaji"],
        types=[
            ProductType("Aloo Bhujia", "namkeen", 49, 299, [["200g", "400g", "1kg"], ["Pouch"]], rep=(15, 25),
                        query="bhujia", brands=["Haldiram's", "Bikaji", "Balaji"]),
            ProductType("Biscuits (Family Pack)", "biscuits", 30, 199, [["Cream", "Digestive", "Marie"],
                                                                       ["Pack of 1", "Pack of 3"]],
                        rep=(15, 20), query="biscuits", brands=["Britannia", "Parle", "Sunfeast", "McVitie's"]),
            ProductType("Chocolate Gift Pack", "chocolate", 199, 899, [["Celebrations", "Assorted"], ["Small",
                                                                                                     "Large"]],
                        rep=(30, 45), query="chocolate gift", brands=["Cadbury", "Ferrero Rocher", "Amul"]),
            ProductType("Potato Chips", "chips", 30, 99, [["Classic Salted", "Magic Masala", "Cream & Onion"],
                                                         ["Pack of 1", "Pack of 4"]],
                        rep=(15, 20), query="chips", brands=["Lay's", "Bingo", "Pringles"]),
            ProductType("Almonds", "dry_fruits", 399, 999, [["250g", "500g"], ["California", "Premium"]],
                        rep=(30, 45), query="almonds", brands=["Happilo", "Nutraj", "Tata Sampann"]),
        ]),
    # ----------------------------------------------------------------- Sports
    "C019": dict(name="Fitness Equipment", parent="C006", weight=3, brands=[
        "Boldfit", "Domyos", "Strauss", "Cockatoo", "Kore", "Nivia", "Lifelong", "PowerMax"],
        types=[
            ProductType("Yoga Mat", "yoga_mat", 299, 1999, [["4mm", "6mm", "8mm"], ["Anti-Slip"], ["Blue", "Purple",
                                                                                                  "Black"]],
                        query="yoga mat"),
            ProductType("Adjustable Dumbbells", "dumbbells", 999, 4999, [["10kg", "20kg"], ["PVC", "Steel"]],
                        query="dumbbells"),
            ProductType("Skipping Rope", "skipping_rope", 149, 499, [["Adjustable"], ["with Counter",
                                                                                   "Ball Bearing"]],
                        query="skipping rope"),
            ProductType("Resistance Bands Set", "resistance_bands", 299, 999, [["5 Bands", "3 Bands"],
                                                                             ["with Handles"]],
                        query="resistance bands"),
            ProductType("Yoga Block (Pair)", "yoga_block", 199, 599, [["EVA Foam"], ["Blue", "Purple"]],
                        query="yoga block"),
            ProductType("Gym Gloves", "gym_gloves", 199, 699, [["S", "M", "L"], ["Wrist Support"]],
                        query="gym gloves"),
            ProductType("Home Exercise Bike", "exercise_bike", 6999, 10000, [["Magnetic", "Belt"], ["Max 100kg"]],
                        query="exercise cycle", brands=["Lifelong", "PowerMax", "Cockatoo"]),
            ProductType("Protein Shaker Bottle", "shaker", 149, 499, [["500ml", "700ml"], ["BPA Free"]],
                        query="shaker"),
        ]),
    "C020": dict(name="Sports Gear & Apparel", parent="C006", weight=3, brands=[
        "Kalenji", "Puma", "Adidas", "HRX", "Nivia"],
        types=[
            ProductType("Dry-Fit Training T-shirt", "sports_tee", 249, 1499, [MEN_SIZES, ["Black", "Neon", "Navy"]],
                        query="sports t shirt"),
            ProductType("Track Pants", "track_pants", 399, 1999, [MEN_SIZES, ["Black", "Grey", "Navy"]],
                        query="track pants"),
            ProductType("Sports Socks (Pack of 3)", "socks", 199, 599, [["Ankle", "Crew"], ["Cushioned"]],
                        query="sports socks"),
            ProductType("Kashmir Willow Cricket Bat", "bat", 799, 3999, [["Full Size", "Size 5"], ["with Cover"]],
                        query="cricket bat", brands=["SG", "SS", "MRF", "Kookaburra"]),
            ProductType("English Willow Cricket Bat", "bat", 4999, 9999, [["Full Size"], ["Grade 2", "Grade 3"]],
                        query="english willow bat", brands=["SG", "SS", "MRF", "Kookaburra"]),
            ProductType("Leather Cricket Ball", "ball", 249, 999, [["Red", "White"], ["4 Piece", "2 Piece"]],
                        query="cricket ball", brands=["SG", "SS", "Kookaburra"]),
            ProductType("Badminton Racket", "racket", 699, 4999, [["Lightweight", "Graphite"], ["with Cover"]],
                        query="badminton racket", brands=["Yonex", "Li-Ning", "Cosco"]),
            ProductType("Nylon Shuttlecocks", "shuttlecock", 299, 1299, [["Pack of 6", "Pack of 10"],
                                                                        ["Medium Speed"]],
                        rep=(30, 45), query="shuttlecock", brands=["Yonex", "Li-Ning", "Cosco"]),
            ProductType("Football", "football", 399, 1999, [["Size 5", "Size 4"], ["Training", "Match"]],
                        query="football", brands=["Nivia", "Cosco", "Adidas", "Puma"]),
        ]),
    # ----------------------------------------------------------------- Books
    "C021": dict(name="Fiction & Self-help", parent="C007", weight=3, brands=[
        "Penguin", "HarperCollins", "Rupa", "Jaico", "Bloomsbury", "Westland", "Hachette"],
        types=[ProductType(t, k, lo, hi, [["Paperback", "Hardcover"], ["English", "Hindi"]], query=q)
               for t, k, lo, hi, q in [
                   ("Atomic Habits by James Clear", "self_help", 299, 699, "atomic habits"),
                   ("The Psychology of Money by Morgan Housel", "self_help", 249, 599, "psychology of money"),
                   ("Ikigai", "self_help", 249, 499, "ikigai"),
                   ("Rich Dad Poor Dad", "self_help", 199, 499, "rich dad poor dad"),
                   ("Think Like a Monk", "self_help", 299, 599, "self help books"),
                   ("Wings of Fire by A.P.J. Abdul Kalam", "biography", 199, 449, "wings of fire"),
                   ("The White Tiger by Aravind Adiga", "novel", 249, 499, "novel"),
                   ("The God of Small Things", "novel", 299, 599, "arundhati roy"),
                   ("Five Point Someone by Chetan Bhagat", "novel", 149, 299, "chetan bhagat"),
                   ("The Palace of Illusions", "novel", 299, 599, "mythology novel"),
                   ("The Immortals of Meluha", "novel", 249, 499, "amish tripathi"),
                   ("Sapiens by Yuval Noah Harari", "non_fiction", 399, 899, "sapiens"),
                   ("Steve Jobs by Walter Isaacson", "biography", 449, 999, "biography"),
                   ("The Alchemist by Paulo Coelho", "novel", 199, 399, "alchemist"),
               ]]),
    "C022": dict(name="Exam Prep", parent="C007", weight=3, brands=[
        "Arihant", "McGraw Hill", "Disha", "S. Chand", "Oswaal", "MTG", "Lucent"],
        types=[ProductType(t, k, lo, hi, [["2026 Edition", "2025 Edition"], ["English", "Hindi"]], query=q)
               for t, k, lo, hi, q in [
                   ("NCERT Exemplar Physics Class 12", "school", 149, 399, "physics class 12"),
                   ("Chemistry Question Bank Class 12", "school", 199, 499, "chemistry class 12"),
                   ("Mathematics Sample Papers Class 10", "school", 149, 399, "class 10 sample papers"),
                   ("Quantitative Aptitude for Competitive Exams", "competitive", 399, 899, "quantitative aptitude"),
                   ("Indian Polity for Civil Services", "competitive", 499, 999, "indian polity"),
                   ("General Knowledge 2026", "competitive", 99, 299, "general knowledge"),
                   ("JEE Main Previous Years Papers", "competitive", 399, 899, "jee main"),
                   ("NEET Biology Guide", "competitive", 499, 1199, "neet biology"),
               ]]),
    # ----------------------------------------------------------------- Toys
    "C023": dict(name="Kids Toys", parent="C008", weight=3, brands=[
        "Funskool", "Hot Wheels", "Lego", "Mirada", "Hamleys", "Fisher-Price", "Mattel"],
        types=[
            ProductType("Remote Control Car", "rc_car", 499, 3499, [["1:18 Scale", "1:24 Scale"], ["Red", "Blue"]],
                        query="remote control car"),
            ProductType("Building Blocks", "blocks", 299, 3999, [["250 pcs", "500 pcs", "1000 pcs"],
                                                                ["Age 6+", "Age 3+"]], query="building blocks"),
            ProductType("Soft Teddy Bear", "soft_toy", 299, 1499, [["1 ft", "2 ft", "3 ft"], ["Brown", "Pink"]],
                        query="teddy bear", brands=["Mirada", "Hamleys", "Archies"]),
            ProductType("Doctor Play Set", "play_set", 299, 899, [["12 pcs", "20 pcs"], ["Age 3+"]],
                        query="doctor set"),
            ProductType("Fashion Doll", "doll", 699, 1999, [["with Accessories", "Dreamhouse Edition"]],
                        query="doll", brands=["Barbie", "Mattel", "Funskool"]),
            ProductType("Foam Dart Blaster", "blaster", 799, 3999, [["Elite", "Mega"], ["with 12 Darts"]],
                        query="nerf gun", brands=["Nerf", "Hasbro"]),
            ProductType("AA Batteries", "battery", 149, 399, [["Pack of 4", "Pack of 10"], ["Alkaline"]],
                        rep=(45, 60), query="aa batteries", brands=["Duracell", "Eveready", "Panasonic"]),
        ]),
    "C024": dict(name="Board Games", parent="C008", weight=2, brands=[
        "Funskool", "Hasbro", "Mattel", "Toyshine", "Kidology", "Ratnas", "Zephyr"],
        types=[
            ProductType("Monopoly Classic", "board_game", 499, 1499, [["English", "India Edition"]],
                        query="monopoly"),
            ProductType("Wooden Chess Set", "chess", 299, 1499, [["12 inch", "16 inch"], ["Folding", "Magnetic"]],
                        query="chess"),
            ProductType("Scrabble", "board_game", 499, 1299, [["Original", "Junior"]], query="scrabble"),
            ProductType("Ludo & Snakes and Ladders", "board_game", 199, 499, [["2-in-1", "4-in-1"]], query="ludo"),
            ProductType("UNO Card Game", "card_game", 149, 399, [["Classic", "Flip"]], query="uno"),
            ProductType("Jigsaw Puzzle", "puzzle", 299, 999, [["500 pcs", "1000 pcs"], ["Landscape", "Map of India"]],
                        query="jigsaw puzzle"),
            ProductType("Party Card Game (18+)", "card_game", 299, 999, [["Party Edition", "Expansion Pack"]],
                        query="party game", adult=True),
            ProductType("Poker Chip Set (18+)", "poker_set", 699, 2499, [["200 Chips", "300 Chips"],
                                                                        ["with Case"]],
                        query="poker set", adult=True),
        ]),
}

# kind -> kinds that go well with it (ground-truth complements).
COMPLEMENT_RULES: dict[str, list[str]] = {
    "smartphone": ["phone_cover", "charger", "earbuds", "screen_guard", "power_bank"],
    "laptop": ["laptop_bag", "mouse", "keyboard"],
    "camera": ["memory_card", "tripod", "camera_bag"],
    "action_camera": ["memory_card", "tripod"],
    "running_shoes": ["socks", "sports_tee"],
    "sneakers": ["socks"],
    "formal_shoes": ["socks", "belt"],
    "jeans": ["belt"],
    "induction": ["kadhai", "pressure_cooker"],
    "kadhai": ["spatula"],
    "shampoo": ["conditioner"],
    "face_wash": ["moisturiser", "sunscreen"],
    "tea": ["biscuits"],
    "coffee": ["biscuits"],
    "yoga_mat": ["yoga_block", "resistance_bands"],
    "dumbbells": ["gym_gloves"],
    "exercise_bike": ["shaker"],
    "bat": ["ball"],
    "racket": ["shuttlecock"],
    "rc_car": ["battery"],
}

# Sub-categories people browse when buying a gift.
GIFT_SUBS = ["C023", "C024", "C021", "C029", "C014", "C032", "C033"]

# Search query patterns; {q} = the product type's query, {brand}, {price} = a round budget.
SEARCH_PATTERNS = ["{q}", "{q}", "{brand} {q}", "{q} under {price}", "best {q}", "{q} for gift", "cheap {q}"]

# Review text per star rating ({kind} is replaced with a friendly product word).
REVIEW_TEXTS: dict[int, list[str]] = {
    5: ["Excellent {kind}, totally worth the money!", "Loved it. Fast delivery too.",
        "Best {kind} I have bought online.", "Superb quality, highly recommended."],
    4: ["Good quality for the price.", "Nice {kind}, packaging could be better.",
        "Works well, happy with the purchase.", "Value for money."],
    3: ["Okay okay. Does the job.", "Average {kind}, expected a bit more.", "Decent, but delivery was late."],
    2: ["Not happy with the quality.", "Stopped working properly after a few weeks.",
        "The {kind} looks different from the photos."],
    1: ["Very poor. Returned it.", "Waste of money.", "Received a damaged {kind}. Disappointed."],
}
