# -*- coding: utf-8 -*-
"""
Where the material comes from, and what you are allowed to do with it.

The licence is the load-bearing part, because he may sell what we make. Every
entry says what the terms actually permit, not what the site's marketing says.
"Free" on a stock site usually means free to download and not free to use.
"""

DOMAIN = ("craft.assets", "sourcing material",
          "Where footage, stills, sound, music and type come from, and the licence attached.")

NODES = [
    ("craft.licence_classes", "the four licences that matter",
     "PUBLIC DOMAIN: no rights remain, use it for anything. Old enough, or dedicated with CC0. "
     "PERMISSIVE with attribution (CC BY): use commercially, credit the author, and the credit must "
     "actually appear. SHARE-ALIKE (CC BY-SA): the same, but your work may inherit the obligation - "
     "avoid it for anything you intend to sell. NON-COMMERCIAL (CC BY-NC, and most 'free for "
     "personal use' fonts and models): unusable for us, whatever else it offers.\n"
     "Record the licence in a file beside the asset the day you download it. A folder of assets "
     "whose terms you cannot reconstruct is a folder you cannot publish from.",
     [("part_of", "craft.assets"), ("requires", "craft.licence_hygiene")], ["legal", "assets"]),

    ("craft.public_domain_sources", "public-domain archives",
     "The safest material there is, and mostly historical. The Internet Archive holds LibriVox "
     "narration - which is where every reference voice in our narration lab comes from - plus film, "
     "radio and print. National archives, the Library of Congress, NASA and most government "
     "photography sit in the public domain in their own country. Wikimedia Commons aggregates a lot "
     "of it but mixes licences, so check each file rather than the site.",
     [("part_of", "craft.assets"), ("used_in", "craft.documentary")], ["legal", "assets"]),

    ("craft.stock_footage_reality", "free stock footage, honestly",
     "The genuinely free libraries exist and their footage is recognisable - other people use the "
     "same clips, and an audience that has seen a shot before discounts your whole piece. Two ways "
     "to use it without that cost: take it for texture rather than for subject (a blurred crowd, "
     "rain on glass, a time-lapse sky), and grade it hard into your own look so it stops matching "
     "its source. Never use stock for the thing your film is actually about.",
     [("part_of", "craft.assets"), ("causes", "failure.stock_look")], ["assets", "production"]),

    ("craft.make_it_yourself", "the cheapest asset is one you make",
     "For most of what an explainer needs - a diagram, a chart, a card, a texture, an abstract "
     "background - generating it is faster than searching for it, and it is exactly what you meant "
     "rather than approximately. Our own visual vocabulary exists for this reason. Search when you "
     "need reality; generate when you need clarity.",
     [("part_of", "craft.assets"), ("implemented_by", "tool.blender")], ["assets", "production"]),

    ("craft.generated_assets_licence", "who owns what a model makes",
     "Unsettled, and the part you CAN control is the model's own licence. Kokoro is Apache 2.0 and "
     "Chatterbox is MIT including weights, so both are usable commercially; many capable models are "
     "explicitly non-commercial and are therefore unusable no matter how good they sound. Separately: "
     "a cloned voice needs a reference you are allowed to use, and cloning a living person without "
     "permission is a different problem from licensing - do not do it.",
     [("part_of", "craft.assets"), ("requires", "craft.tts_reference_clone")], ["legal", "ai"]),

    ("craft.fonts", "type licences bite hardest",
     "A font licensed for a document is often not licensed for embedding in video or for a logo, and "
     "'free for personal use' is the commonest trap on font sites. The safe ground is the open "
     "families - the SIL Open Font License permits commercial use and embedding - and the faces that "
     "shipped with the operating system, which are licensed for use on it. Georgia and Cambria are "
     "on this machine and we use them.",
     [("part_of", "craft.assets"), ("applies_to", "craft.screen_typography")], ["legal", "typography"]),

    ("craft.music_sources", "music you can actually keep",
     "Public-domain compositions are not public-domain RECORDINGS - the score of a Beethoven "
     "symphony is free and a 1990 performance of it is not. What is usable: recordings explicitly "
     "released CC0 or CC BY, properly licensed library music, and anything you make. Automated "
     "content matching finds everything else, and the usual penalty is the whole video's revenue "
     "rather than a takedown.",
     [("part_of", "craft.assets"), ("requires", "craft.music_licensing")], ["legal", "music"]),

    ("craft.sound_effects_sources", "sound effects",
     "Freesound holds a large CC library, mixed between CC0 and attribution - the licence is "
     "per-file. Better for most purposes: record it yourself, because a specific sound you made is "
     "always closer to what the picture needs than a generic one, and most effects are household "
     "objects near a microphone.",
     [("part_of", "craft.assets"), ("applies_to", "craft.foley")], ["assets", "sound"]),

    ("craft.asset_organisation", "name it when you get it, not later",
     "Every asset goes into the project with a source, a licence and a date, in a manifest beside "
     "it. The cost of doing this is a minute; the cost of not doing it is discovering three months "
     "later that you cannot prove you may use the shot the whole piece is built on. Our LibriVox "
     "corpus has a MANIFEST.json for exactly this reason.",
     [("part_of", "craft.assets"), ("requires", "craft.naming_convention")], ["assets", "pipeline"]),

    ("craft.attribution_practice", "how to attribute without ruining the film",
     "An attribution has to be findable, not prominent. A credits card at the end plus a full list "
     "in the description satisfies every CC BY licence I know of and costs the film nothing. What "
     "does not satisfy it is a general thank-you with no names.",
     [("part_of", "craft.assets")], ["legal", "delivery"]),

    ("craft.reference_not_copy", "reference is not material",
     "Watching how a film you admire solved a problem is how you learn. Lifting its footage, its "
     "music or its exact shot design is a different act. The useful discipline is to work out WHY "
     "the thing worked - which decision, in which order - and then make that decision yourself with "
     "your own material.",
     [("part_of", "craft.assets"), ("applies_to", "craft.looks")], ["method"]),
]

SUPPORT = [
    ("failure.stock_look", "failure", "it looks like stock footage",
     "The audience has seen the shot before, or it has the particular gloss of material made to be "
     "sold rather than to say something. Grade it hard, use it as texture, and never let stock "
     "carry the subject."),
]
