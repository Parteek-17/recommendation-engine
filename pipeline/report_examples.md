# Report examples (`python -m pipeline.report_examples`)

## Post-purchase switch: U05835 buys “Redmi Flagship 5G Smartphone (12GB/256GB, Phantom Black)” (₹46,399)

Before the purchase U05835 had been looking at this phone. Ground-truth labels are relative to the phone.

**Before** (still shopping for a phone)

| # | Product | Category | Source | Reason shown | Ground truth |
|---|---|---|---|---|---|
| 1 | McVitie's Biscuits (Family Pack) (Marie, Pack of 1) | Snacks | restock | Time to restock McVitie's Biscuits (Family Pack) (Marie, Pack of 1)? You bought it 15 days ago |  |
| 2 | Jio Keypad Feature Phone (Dual SIM, Red) | Mobiles | complement | Goes well with your Samsung Wireless Earbuds (40H Playback, White) |  |
| 3 | Lava Keypad Feature Phone (Dual SIM, Blue) | Mobiles | complement | Goes well with your Samsung Power Bank (10000mAh, 22.5W) |  |
| 4 | itel Keypad Feature Phone (Single SIM, Red) | Mobiles | item_cf | Because you added to cart Redmi Flagship 5G Smartphone (12GB/256GB, Phantom Black) |  |
| 5 | Kore Skipping Rope (Adjustable, Ball Bearing) | Fitness Equipment | als | Picked for your shopping style |  |
| 6 | Red Tape Flip Flops (Women, Grey) | Footwear | als | Picked for your shopping style |  |
| 7 | Nokia Keypad Feature Phone (Dual SIM, Blue) | Mobiles | als | Matches your taste in Mobiles |  |
| 8 | Jio Keypad Feature Phone (Single SIM, Black) | Mobiles | als | Matches your taste in Mobiles |  |

**After** buying it

| # | Product | Category | Source | Reason shown | Ground truth |
|---|---|---|---|---|---|
| 1 | McVitie's Biscuits (Family Pack) (Marie, Pack of 1) | Snacks | restock | Time to restock McVitie's Biscuits (Family Pack) (Marie, Pack of 1)? You bought it 15 days ago |  |
| 2 | Jio Keypad Feature Phone (Dual SIM, Red) | Mobiles | complement | Goes well with your Samsung Wireless Earbuds (40H Playback, White) |  |
| 3 | Lava Keypad Feature Phone (Dual SIM, Blue) | Mobiles | complement | Goes well with your Samsung Power Bank (10000mAh, 22.5W) |  |
| 4 | itel Keypad Feature Phone (Single SIM, Red) | Mobiles | item_cf | Because you bought Redmi Flagship 5G Smartphone (12GB/256GB, Phantom Black) |  |
| 5 | Kore Skipping Rope (Adjustable, Ball Bearing) | Fitness Equipment | als | Picked for your shopping style |  |
| 6 | Red Tape Flip Flops (Women, Grey) | Footwear | als | Picked for your shopping style |  |
| 7 | Nokia Keypad Feature Phone (Dual SIM, Blue) | Mobiles | als | Matches your taste in Mobiles |  |
| 8 | Jio Keypad Feature Phone (Single SIM, Black) | Mobiles | als | Matches your taste in Mobiles |  |

Substitutes of the phone in the top 8: **0 before → 0 after**. True complements after: **0**.

**After, as shown in the storefront** (`GET /recommend`: reco + the API's post-purchase switch)

| # | Product | Category | Source | Reason shown | Ground truth |
|---|---|---|---|---|---|
| 1 | Spigen Back Cover (for Galaxy M35, Rugged Armor) | Mobile Accessories | post_purchase | Goes with the Redmi Flagship 5G Smartphone (12GB/256GB, Phantom Black) you just bought | true complement |
| 2 | JBL 20W Fast Charger (Type-C, Adapter Only) | Mobile Accessories | post_purchase | Goes with the Redmi Flagship 5G Smartphone (12GB/256GB, Phantom Black) you just bought | true complement |
| 3 | Realme Tempered Glass Screen Guard (for Realme 12 Pro, Pack of 2) | Mobile Accessories | post_purchase | Goes with the Redmi Flagship 5G Smartphone (12GB/256GB, Phantom Black) you just bought | true complement |
| 4 | Mi Wireless Earbuds (30H Playback, White) | Mobile Accessories | post_purchase | Goes with the Vivo Flagship 5G Smartphone (16GB/512GB, Cream) you just bought |  |
| 5 | Samsung 20W Fast Charger (USB-A + Type-C, with Cable) | Mobile Accessories | post_purchase | Goes with the Vivo Flagship 5G Smartphone (16GB/512GB, Cream) you just bought |  |
| 6 | Mi 20W Fast Charger (USB-A + Type-C, Adapter Only) | Mobile Accessories | post_purchase | Goes with the Samsung 5G Smartphone (12GB/256GB, Midnight Black) you just bought |  |
| 7 | URBN Wireless Earbuds (50H Playback, Blue) | Mobile Accessories | post_purchase | Goes with the Samsung 5G Smartphone (12GB/256GB, Midnight Black) you just bought |  |
| 8 | McVitie's Biscuits (Family Pack) (Marie, Pack of 1) | Snacks | restock | Time to restock McVitie's Biscuits (Family Pack) (Marie, Pack of 1)? You bought it 15 days ago |  |

Other phones in the storefront list: **0**; true complements: **3**.

`get_complements` for the phone: Spigen Back Cover (for Galaxy M35, Rugged Armor) (true complement); JBL 20W Fast Charger (Type-C, Adapter Only) (true complement); Realme Tempered Glass Screen Guard (for Realme 12 Pro, Pack of 2) (true complement); Caseology Back Cover (for OnePlus Nord CE4, Rugged Armor) (not in ground truth); Realme Power Bank (10000mAh, 20W PD) (true complement)

## Cold-start user: U08992 (signed up, no events yet)

| # | Product | Category | Source | Reason shown | |
|---|---|---|---|---|---|
| 1 | Red Tape Flip Flops (Women, Grey) | Footwear | popularity | Popular with shoppers like you |  |
| 2 | Puma Cotton Socks (Pack of 3) (Crew, Assorted) | Fashion Accessories | popularity | Popular with shoppers like you |  |
| 3 | Realme Tempered Glass Screen Guard (for Realme 12 Pro, Pack of 2) | Mobile Accessories | popularity | Popular with shoppers like you |  |
| 4 | Only Anarkali Kurta (Embroidered, Peach) | Women's Clothing | popularity | Popular with shoppers like you |  |
| 5 | Parachute Anti-Dandruff Shampoo (340ml, Cool Menthol) | Haircare | popularity | Popular with shoppers like you |  |
| 6 | Daawat Whole Wheat Atta (5kg, Chakki Fresh) | Staples | popularity | Popular with shoppers like you |  |

## Cold-start product: “Red Tape Casual Sneakers (Men, Olive)” (launched 2026-09-30, zero interactions)

Collaborative filtering knows nothing about it; the content model (TF-IDF on title/brand/category/tags) still finds alternatives:

| # | Similar product | Category | Source | Reason shown | |
|---|---|---|---|---|---|
| 1 | Red Tape Casual Sneakers (Women, Black) | Footwear | content | Similar to Red Tape Casual Sneakers (Men, Olive) |  |
| 2 | Sparx Casual Sneakers (Men, Olive) | Footwear | content | Similar to Red Tape Casual Sneakers (Men, Olive) |  |
| 3 | Campus Casual Sneakers (Men, Olive) | Footwear | content | Similar to Red Tape Casual Sneakers (Men, Olive) |  |
| 4 | Woodland Casual Sneakers (Men, Olive) | Footwear | content | Similar to Red Tape Casual Sneakers (Men, Olive) |  |
| 5 | Nike Casual Sneakers (Men, Olive) | Footwear | content | Similar to Red Tape Casual Sneakers (Men, Olive) |  |
