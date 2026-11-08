---
name: clone-search
version: "1.0"
description: How the Roblox Scout turns a Steam game into Roblox search queries and spots clones.
agents: [roblox_scout]
tasks: [search-clones]
data:
  tag_keywords:
    Horror: horror
    Psychological Horror: horror
    Survival Horror: horror
    Climbing: climb
    Parkour: parkour
    3D Platformer: obby
    Platformer: obby
    Management: tycoon
    Economy: tycoon
    Idler: idle
    Clicker: clicker
    Job Simulator: simulator
    Life Sim: simulator
    Farming Sim: farm
    Cooking: cooking
    Fishing: fishing
    Driving: driving
    Racing: racing
    Survival: survival
    Zombies: zombie
    Social Deduction: impostor
    Party Game: party
    Card Game: cards
    Deckbuilding: cards
    Roguelike: roguelike
    Tower Defense: tower defense
    Battle Royale: battle royale
    Extraction Shooter: extraction
    Hidden Object: hide
    Shop Keeper: shop
    Crafting: craft
    Colony Sim: colony
    Open World Survival Craft: survival
    Escape Room: escape
    Puzzle: puzzle
  description_keywords:
    climb: climb
    supermarket: supermarket
    store: store
    shop: shop
    restaurant: restaurant
    cook: cooking
    farm: farm
    garden: garden
    fish: fishing
    horror: horror
    ghost: ghost
    paranormal: ghost
    monster: monster
    zombie: zombie
    hide and seek: hide and seek
    impostor: impostor
    imposter: impostor
    traitor: traitor
    liar: liar
    bluff: bluff
    roulette: roulette
    chameleon: chameleon
    camera: camera
    quota: quota
    loot: loot
    chain: chained
    ragdoll: ragdoll
    parkour: parkour
    obby: obby
    survive: survival
    tower defense: tower defense
    racing: racing
    clean: cleaning
    delivery: delivery
    prison: prison
    heist: heist
    tycoon: tycoon
    dungeon: dungeon
    wizard: wizard
    mage: mage
    pirate: pirate
    space: space
    dinosaur: dino
    dragon: dragon
    backrooms: backrooms
    zoo: zoo
---
Queries, in order: the title, then the subtitle (or title plus the first concept keyword),
then two concept keywords together. Concept keywords come from the short description first
(more specific), then from the Steam tags, using the two maps above.

A search hit is relevant when its name contains the title, or when it matches at least two
concept keywords (name or Roblox genre). A one-word common title (for example "PEAK") also
needs a concept keyword.

- **Clone**: relevant game created after (Steam release minus 30 days).
- **Established game**: relevant game created before that date with 1 000 players or more;
  it signals genre competition that was already there.
- **Saturated concept**: best clone or established leader at 20 000 players or more. The
  scorer then applies the saturation malus and F9 drops to 1.

Player counts are live snapshots, not historical peaks. Keep the source URL and the fetch
time with every number.
