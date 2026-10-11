import sys; sys.path.insert(0, "/root/BORIS/backend")
"""Сравнение моделей и качества на трёх сюжетах. 9 генераций, ~18,5 ₽."""
import time
from banner_generator import generate_ai_image

BASE = "/root/BORIS/backend/images/qa/compare_1508"
ACC = "qa_internal"

SCENES = {
 "bytovka": ("Photorealistic commercial photograph of a modern site cabin "
   "(bytovka) 6x2.4 meters, green corrugated metal cladding, one window on the "
   "right, metal door on the left, standing on a levelled gravel plot, light "
   "overcast daylight, birch trees in the background. Real photo look, no text, "
   "no watermark, no people."),
 "shkaf": ("Photorealistic interior photograph of a modern sliding wardrobe "
   "2.4 meters wide, matte light grey slab doors, one mirrored door in the "
   "middle, black slim aluminium profile, in a bright hallway with light oak "
   "herringbone floor and white walls, daylight from the left. Real photo look, "
   "no text, no watermark, no people."),
 "kuhnya": ("Photorealistic interior photograph of a modern straight kitchen "
   "3 meters long, matte graphite grey handleless fronts, light concrete grey "
   "countertop, upper cabinets to the ceiling, built-in oven on the right, sink "
   "on the left, light oak floor, white walls, daylight from a window on the "
   "left. Real photo look, no text, no watermark, no people."),
}
VARIANTS = [
 ("flagman_medium", "gpt-image-2", "medium"),
 ("mini_medium", "gpt-image-1-mini", "medium"),
 ("mini_low", "gpt-image-1-mini", "low"),
]

t0 = time.time()
ok = 0
for scene, prompt in SCENES.items():
    for tag, model, quality in VARIANTS:
        path = "%s/%s__%s.png" % (BASE, scene, tag)
        s = time.time()
        r = generate_ai_image(prompt, path, size="1024x1024", quality=quality,
                              model=model, account_id=ACC,
                              operation="qa_model_compare")
        ok += 1 if r else 0
        print("%-10s %-16s %s  %d сек" % (scene, tag, "OK" if r else "ОШИБКА",
                                          time.time() - s), flush=True)
print("готово: %d из 9 за %d сек" % (ok, time.time() - t0), flush=True)
