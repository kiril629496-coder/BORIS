from pathlib import Path
from collections import Counter
from PIL import Image, ImageDraw
from app.services.avito_premium_compositor import compose_premium_avito_banner

def _source(path):
    im=Image.new("RGB",(1024,1024),(215,220,225))
    d=ImageDraw.Draw(im)
    d.rectangle((460,180,960,900),fill=(120,135,150))
    d.ellipse((550,260,850,560),fill=(175,185,195))
    im.save(path)

def test_v7_is_deterministic_exact_title_and_diverse(tmp_path):
    src=tmp_path/"src.jpg"; _source(src)
    families=Counter()
    for i in range(1,101):
        title=f"Тротуарная плитка для дорожек {i:03d}"
        out=tmp_path/f"campaign_522_item_{6200+i}_op_test.png"
        a=compose_premium_avito_banner(str(src),str(out),title)
        b=compose_premium_avito_banner(str(src),str(out),title)
        assert a["ok"] and b["ok"]
        assert a["layout"]==b["layout"]
        assert a["creative_family"]==b["creative_family"]
        assert a["display_title"]==title
        assert a["line_count"] in (1,2)
        assert a["font_size"]>=48
        assert out.is_file() and out.stat().st_size>0
        families[a["creative_family"]]+=1
    assert len(families)==4
    assert max(families.values()) <= 40
    assert min(families.values()) >= 10

def test_v7_never_invents_copy(tmp_path):
    src=tmp_path/"src.jpg"; _source(src)
    title="Панорамное остекление Grand Line"
    out=tmp_path/"campaign_1_item_1_op_x.png"
    result=compose_premium_avito_banner(str(src),str(out),title)
    assert result["ok"]
    assert result["display_title"]==title
    assert result["source_title"]==title
    assert result["visible_word_count"]==len(title.split())
