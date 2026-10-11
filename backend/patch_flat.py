import ast, shutil, sys, time
p = "app/api/banners.py"
s0 = open(p, encoding="utf-8").read()
anchor = '"or skew the overall composition or the camera angle even slightly — no Dutch angle, no diagonal framing."'
add = ('"or skew the overall composition or the camera angle even slightly — no Dutch angle, no diagonal framing.\\n\\n"\n'
       '            "CRITICAL FLATNESS RULE: The output IS the banner itself — a flat 2D graphic design, exactly as exported "\n'
       '            "from a design tool. Do NOT depict the banner as a physical object inside a scene: no poster on a wall, "\n'
       '            "no billboard, no signboard, no screen, no monitor, no paper or device mockup, no frame, no easel. "\n'
       '            "No 3D perspective, no vanishing point, no foreshortening, no glare or shadow falling on the banner surface. "\n'
       '            "Text and layout lie flat on the picture plane, viewed straight on at 0 degrees."')
n = s0.count(anchor)
print("вхождений правила:", n)
if "CRITICAL FLATNESS RULE" in s0:
    print("уже пропатчено"); raise SystemExit(0)
s = s0.replace(anchor, add)
ast.parse(s)
print("заменено:", n, "| символов добавлено:", len(s) - len(s0))
if "--apply" in sys.argv:
    shutil.copy(p, "%s.bak_flat_%d" % (p, int(time.time())))
    open(p, "w", encoding="utf-8").write(s)
    print("ЗАПИСАНО")
else:
    print("dry-run OK")
