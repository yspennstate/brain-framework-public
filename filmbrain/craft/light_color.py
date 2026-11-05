# -*- coding: utf-8 -*-
"""Lighting and colour: the two things that decide whether a frame looks like money."""

DOMAIN = ("craft.lighting", "lighting", "Where the light comes from and what it costs.")

NODES = [
    # ---------------- the basic grammar ----------------
    ("craft.three_point", "three-point lighting",
     "Key, fill, back. It is not a style, it is a diagnostic: if a shot looks wrong, ask which of the "
     "three is missing. Most bad video is missing the backlight, which is the one that separates a "
     "head from the wall behind it and costs the least to add.",
     [("part_of", "craft.lighting"), ("requires", "craft.key_light"),
      ("requires", "craft.fill_light"), ("requires", "craft.back_light")], ["lighting", "setup"]),

    ("craft.key_light", "key light",
     "The main source, and the one that decides where the shadows fall. Its position relative to the "
     "lens does more to the mood than its brightness: on the lens axis it is flat and clinical, at "
     "45 degrees it is conventional and flattering, at 90 it is dramatic and unforgiving.",
     [("part_of", "craft.lighting"), ("causes", "craft.shadow_direction")], ["lighting"]),

    ("craft.fill_light", "fill light",
     "Lifts the shadow side so the audience can read detail there. The ratio between key and fill is "
     "the contrast of the face, and it is the most legible mood control in lighting: 2:1 is warm and "
     "conversational, 8:1 is film noir.",
     [("part_of", "craft.lighting"), ("measured_by", "metric.lighting_ratio")], ["lighting"]),

    ("craft.back_light", "back light / rim / kicker",
     "Behind the subject, aimed at the back of the head and shoulders. Draws a bright edge that lifts "
     "them off the background. In a dark scene it is often the only thing keeping a person visible at "
     "all, and in a render it is the fastest cure for the flat look.",
     [("part_of", "craft.lighting"), ("produces", "craft.subject_separation"),
      ("prevents", "failure.flat_image")], ["lighting"]),

    ("craft.practical_light", "practical",
     "A lamp, a screen, a window that appears in shot and appears to be the source. Practicals are "
     "worth more than their output because they justify everything else: once the audience sees a "
     "lamp, any warm light on that side is motivated.",
     [("part_of", "craft.lighting"), ("produces", "craft.motivated_light")], ["lighting"]),

    ("craft.motivated_light", "motivated lighting",
     "Every source in the frame can be traced to something the audience can believe in - a window, a "
     "lamp, a fire, the moon. It is the lighting equivalent of a motivated camera move, and it is why "
     "beautifully lit scenes do not look lit.",
     [("part_of", "craft.lighting"), ("prevents", "failure.unmotivated_light")], ["lighting"]),

    ("craft.hard_light", "hard light",
     "A small source relative to the subject. Sharp-edged shadows, texture, drama, every pore. The "
     "sun on a clear day, a bare bulb, an unmodified spot. Hard light is honest and unkind.",
     [("part_of", "craft.lighting"), ("opposite_of", "craft.soft_light")], ["lighting", "quality"]),

    ("craft.soft_light", "soft light",
     "A large source relative to the subject. Shadows with gradients rather than edges. Get it by "
     "making the source bigger or closer - a bedsheet in front of a lamp, an overcast sky, a bounce "
     "off a wall. Almost every 'expensive-looking' interview is a very large soft key close to the "
     "subject.",
     [("part_of", "craft.lighting"), ("used_in", "craft.interview"),
      ("opposite_of", "craft.hard_light")], ["lighting", "quality"]),

    ("craft.bounce_light", "bounce",
     "Aim the light at a white surface and use what comes back. Cheapest soft source there is, and "
     "the reason a piece of foam board is standard kit. In a 3D render the equivalent is letting "
     "global illumination do the work rather than adding a second lamp.",
     [("is_a", "craft.soft_light"), ("implemented_by", "tool.blender")], ["lighting", "technique"]),

    ("craft.negative_fill", "negative fill",
     "Put something black where light is bouncing in, and take contrast away from nothing. As "
     "important as adding light and almost never taught. A black flag on the shadow side is what "
     "makes a face sculpted rather than pasty.",
     [("part_of", "craft.lighting"), ("produces", "effect.modelling")], ["lighting", "technique"]),

    # ---------------- portrait patterns ----------------
    ("craft.rembrandt", "Rembrandt light",
     "Key at roughly 45 degrees horizontally and 45 up, producing a triangle of light on the shadow "
     "cheek under the eye. It is the default dramatic portrait because it gives a face volume with "
     "one instrument.",
     [("is_a", "craft.portrait_lighting"), ("produces", "effect.modelling")], ["lighting", "portrait"]),

    ("craft.butterfly", "butterfly / paramount",
     "Key high and straight on, shadow under the nose. Glamour lighting - it flattens skin texture "
     "and emphasises cheekbones. Add a reflector below and it becomes clamshell, which is the modern "
     "beauty and talking-head standard.",
     [("is_a", "craft.portrait_lighting")], ["lighting", "portrait"]),

    ("craft.split_light", "split light",
     "Key at ninety degrees: half the face lit, half in shadow. Reads as division, conflict, or "
     "concealment. Very strong, so it declares itself.",
     [("is_a", "craft.portrait_lighting"), ("causes", "effect.unease")], ["lighting", "portrait"]),

    ("craft.loop_light", "loop light",
     "Key slightly off axis, small nose shadow looping down towards the corner of the mouth. The "
     "least noticeable, most generally flattering pattern, which is why it is the safe interview "
     "answer.",
     [("is_a", "craft.portrait_lighting"), ("used_in", "craft.interview")], ["lighting", "portrait"]),

    ("craft.portrait_lighting", "portrait lighting patterns",
     "The named positions of a key light on a face. Knowing them matters less for choosing than for "
     "diagnosing: when a face looks wrong, the pattern tells you which way to move the light.",
     [("part_of", "craft.lighting")], ["lighting", "portrait"]),

    ("craft.high_key", "high key",
     "Bright, low contrast, few shadows. Comedy, commercials, corporate, daytime television. Easy to "
     "shoot and hard to make interesting, because contrast is where mood lives.",
     [("part_of", "craft.lighting_style"), ("opposite_of", "craft.low_key")], ["lighting", "style"]),

    ("craft.low_key", "low key",
     "Most of the frame dark, small areas lit. Noir, horror, thriller, and the look most people mean "
     "by cinematic. The trick is that it is not underexposure - the lit parts must still be properly "
     "exposed; only the amount of the frame that is lit changes.",
     [("part_of", "craft.lighting_style"), ("requires", "craft.negative_fill"),
      ("prevents", "failure.muddy_underexposure")], ["lighting", "style"]),

    ("craft.chiaroscuro", "chiaroscuro",
     "Extreme light against extreme dark with little in between, borrowed from painting. Works best "
     "when a single believable source does it - a candle, a doorway - so the audience reads it as a "
     "place rather than as a lighting choice.",
     [("is_a", "craft.low_key")], ["lighting", "style"]),

    ("craft.lighting_style", "lighting styles", "Named overall approaches to the balance of light and dark.",
     [("part_of", "craft.lighting")], ["lighting"]),

    ("craft.golden_hour", "golden hour",
     "The hour after sunrise and before sunset: low, warm, soft, with long shadows and a sky that is "
     "not blown out. Free production value, and utterly unreliable - you get about forty usable "
     "minutes, so shoot the shot that needs it first.",
     [("part_of", "craft.natural_light")], ["lighting", "exterior"]),

    ("craft.blue_hour", "blue hour",
     "The twenty minutes after the sun is down when the sky is still bright and deeply blue. The only "
     "time practicals and sky balance, which is why every establishing shot of a city at dusk is shot "
     "then.",
     [("part_of", "craft.natural_light")], ["lighting", "exterior"]),

    ("craft.day_for_night", "day for night",
     "Shoot in daylight and grade it to read as night: underexpose, cool the shadows, crush the sky, "
     "keep a hard 'moon' key. It never fully convinces on faces, but it beats an underexposed real "
     "night, and in animation it is simply a lighting rig plus a grade.",
     [("part_of", "craft.color_grading"), ("requires", "craft.color_grading")],
     ["lighting", "grading"]),

    ("craft.natural_light", "natural light", "Working with the sun and sky as your instrument.",
     [("part_of", "craft.lighting")], ["lighting"]),

    ("craft.color_temperature", "colour temperature",
     "Daylight is about 5600 K and blue, tungsten about 3200 K and orange. Mixing them without "
     "meaning to is the classic amateur error - a window and a room lamp in one shot gives you a "
     "blue face and an orange wall. Mixing them on purpose is one of the strongest looks available.",
     [("part_of", "craft.lighting"), ("causes", "failure.mixed_color_temp")],
     ["lighting", "color"]),

    ("craft.white_balance", "white balance",
     "Telling the camera which light is white. Get it right in camera when you can; get it "
     "consistent always, because two shots of the same scene at different balances cannot be cut "
     "together without a grade.",
     [("part_of", "craft.color_grading")], ["color", "camera"]),

    # ---------------- colour ----------------
    ("craft.color_grading", "colour grading",
     "Deciding the colour of the finished picture. The order that avoids pain: normalise every shot to "
     "a neutral base first, then match shots to each other, then apply a look on top of the matched "
     "sequence. Grading shot by shot to taste guarantees a sequence that flickers between looks.",
     [("part_of", "craft.post"), ("requires", "craft.color_management")], ["color", "post"]),

    ("craft.color_correction", "colour correction",
     "The first pass: get exposure, black point, white point and neutrality right so every shot "
     "starts from the same place. Unglamorous and it is where most of the improvement is.",
     [("part_of", "craft.color_grading"), ("step_of", "craft.color_grading")], ["color", "post"]),

    ("craft.color_management", "colour management",
     "Knowing what space your pixels are in at every stage. Get it wrong and everything downstream is "
     "guesswork: a Rec.709 file interpreted as sRGB is subtly washed, and a log file treated as "
     "display-referred is flat and grey. In Blender this is the view transform; in ffmpeg it is the "
     "colour primaries, transfer and matrix tags, and untagged output is the single most common cause "
     "of 'it looked different on the other machine'.",
     [("part_of", "craft.color_grading"), ("prevents", "failure.wrong_gamma"),
      ("implemented_by", "tool.ffmpeg")], ["color", "pipeline"]),

    ("craft.lut", "LUT",
     "A lookup table that maps colour in to colour out. Two honest uses: converting from a camera's "
     "log space to display, and carrying a creative look. A LUT applied on top of an unmatched shot "
     "does not fix the shot; it just makes the mismatch a different colour.",
     [("part_of", "craft.color_grading"), ("implemented_by", "tool.ffmpeg")], ["color", "post"]),

    ("craft.lift_gamma_gain", "lift, gamma, gain",
     "Three handles on shadows, midtones and highlights. Tinting the lift blue and the gain warm is "
     "the whole of the teal-and-orange look in two moves; it is a cliche because it works - skin sits "
     "in the warm end and almost everything else can be pushed cool.",
     [("part_of", "craft.color_grading")], ["color", "post"]),

    ("craft.contrast_curve", "the S-curve",
     "Lift the highlights, drop the shadows, leave the midtones. It is what 'more contrast' means in "
     "practice and what film stock did chemically. Overdone it crushes detail you cannot get back; a "
     "gentle S plus a slightly lifted black point is the film look in two moves.",
     [("part_of", "craft.color_grading")], ["color", "post"]),

    ("craft.film_emulation", "film emulation",
     "Halation around highlights, grain that moves, a black point that is not zero, subtle crosstalk "
     "between channels, and highlight roll-off rather than a clip. Digital looks digital mostly "
     "because it clips highlights abruptly and has perfectly clean blacks.",
     [("part_of", "craft.color_grading"), ("prevents", "failure.cgi_look"),
      ("requires", "craft.grain")], ["color", "post"]),

    ("craft.grain", "grain",
     "Structured noise across the image. Its real job is to hide the seams: it breaks up banding, "
     "masks the softness of an upscale, and glues composited elements together because they all now "
     "share one texture. Four units of subtle grain saved every clip in the Bastogne film from "
     "reading as plastic.",
     [("part_of", "craft.color_grading"), ("prevents", "failure.banding"),
      ("prevents", "failure.plastic_upscale"), ("implemented_by", "tool.ffmpeg")],
     ["color", "post", "vfx"]),

    ("craft.banding", "banding",
     "Visible steps in a gradient - skies and soft shadows first. Caused by too few bits: an 8-bit "
     "output of a gradient that was graded hard. Cure with a higher bit depth through the pipeline, "
     "a dither, or a little grain.",
     [("part_of", "craft.color_grading"), ("measured_by", "metric.bit_depth")], ["color", "delivery"]),

    ("craft.skin_tone", "skin tone",
     "The one colour an audience judges absolutely. Everything else can be pushed; skin cannot. On a "
     "vectorscope real skin of every complexion sits close to one line, differing in saturation and "
     "brightness rather than in hue - which is why that line is the fastest check in a grade.",
     [("part_of", "craft.color_grading"), ("measured_by", "metric.vectorscope")], ["color", "post"]),

    ("craft.color_palette", "palette",
     "Deciding in advance which colours the film is allowed to use, and where. A restricted palette "
     "makes an image read as designed; it also makes one unexpected colour enormously powerful. "
     "Choose it before you build a set, not in the grade.",
     [("part_of", "craft.color_grading"), ("applies_to", "craft.production_design")], ["color", "design"]),

    ("craft.color_script", "colour script",
     "A strip of the whole film reduced to one swatch or thumbnail per scene, so the emotional arc is "
     "visible as colour. Pixar's habit and worth stealing for any long piece, including a lecture - it "
     "will tell you instantly that your middle forty minutes are all the same grey.",
     [("part_of", "craft.preproduction"), ("applies_to", "craft.color_palette")], ["color", "planning"]),

    ("craft.complementary_color", "complementary contrast",
     "Opposite hues placed together intensify each other. Orange skin against a teal background is "
     "the industrial version; the general principle is that a subject separated by hue needs less "
     "separation by light.",
     [("part_of", "craft.color_palette"), ("produces", "craft.subject_separation")], ["color"]),

    ("craft.desaturation", "desaturation",
     "Removing colour reads as bleakness, memory, or documentary seriousness. Partial desaturation "
     "with one colour retained is a strong device and a spent one - it has been done, and the "
     "audience knows.",
     [("part_of", "craft.color_grading")], ["color"]),

    ("craft.subject_separation", "subject separation",
     "Making the subject distinct from the background by any means: focus, light, colour, contrast or "
     "motion. If a shot feels muddy, it almost always lacks one of these five, and adding any one of "
     "them fixes it.",
     [("part_of", "craft.composition"), ("prevents", "failure.flat_image")], ["camera", "lighting"]),

    ("craft.production_design", "production design",
     "What is in the frame besides the people. It is cheaper to fix a shot by moving an object than "
     "by adding a light, and it is the difference between a room and a set.",
     [("part_of", "craft.preproduction")], ["design"]),
]

SUPPORT = [
    ("effect.modelling", "effect", "modelling", "Light describes the three-dimensional shape of a face or object."),
    ("metric.lighting_ratio", "metric", "key to fill ratio",
     "The brightness of the key side over the fill side, in stops or as a ratio. 2:1 conversational, "
     "4:1 dramatic, 8:1 and beyond noir."),
    ("metric.vectorscope", "metric", "vectorscope skin line",
     "The diagonal between red and yellow on a vectorscope where human skin sits regardless of "
     "complexion. Deviation from it is the fastest objective check on a grade."),
    ("metric.bit_depth", "metric", "bit depth", "Bits per channel. 8 bits bands on gradients once graded; 10 or more survives."),
    ("failure.unmotivated_light", "failure", "light with no source",
     "A rim light with nothing behind the subject to make it, or a key from a wall. The audience "
     "does not analyse it, but the image reads as staged."),
    ("failure.mixed_color_temp", "failure", "mixed colour temperature by accident",
     "Daylight through a window and a tungsten lamp in one shot: one side of the face blue, the "
     "other orange, and no white balance fixes both."),
    ("failure.muddy_underexposure", "failure", "underexposure mistaken for low key",
     "The whole frame is dim and noisy rather than mostly dark with correctly exposed highlights. "
     "Noise lives in the shadows, so lifting it in the grade makes it worse."),
    ("failure.wrong_gamma", "failure", "wrong transfer function",
     "The file was tagged, or not tagged, differently from how it was made; the picture is washed "
     "out or crushed on someone else's screen. Tag colour primaries, transfer and matrix explicitly "
     "on every deliverable."),
    ("failure.banding", "failure", "banding in gradients"),
    ("failure.plastic_upscale", "failure", "an upscale that reads as plastic",
     "Smooth, waxy areas with no fine texture, because the detail was never there and the upscaler "
     "invented smoothness. Grain and a little unsharp mask hide it better than a bigger model does."),
]
