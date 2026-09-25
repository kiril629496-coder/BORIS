# -*- coding: utf-8 -*-
from __future__ import annotations

TEMPLATES=('clean_line','glass_bottom','glass_top','side_accent','corner_marks','progress','center_card','cinema_bars','soft_frame','split_hint','badge','cta_panel')

def template_for_scene(style,role,index):
    if role=='cta': return 'cta_panel'
    pools={
      'premium':['clean_line','soft_frame','glass_bottom','corner_marks'],
      'sales':['badge','glass_top','progress','cta_panel'],
      'expert':['glass_bottom','side_accent','clean_line','center_card'],
      'dynamic':['progress','split_hint','corner_marks','badge'],
    }
    p=pools.get(style,pools['premium']); return p[index%len(p)]

def scene_filter(name,w,h,duration):
    m=max(18,int(w*.04)); t=max(5,int(w*.006)); bottom=int(h*.82)
    if name=='glass_bottom': return f"drawbox=x={m}:y={bottom}:w={w-2*m}:h={int(h*.11)}:color=black@0.28:t=fill"
    if name=='glass_top': return f"drawbox=x={m}:y={int(h*.08)}:w={w-2*m}:h={int(h*.10)}:color=black@0.28:t=fill"
    if name=='side_accent': return f"drawbox=x={m}:y={int(h*.18)}:w={t}:h={int(h*.42)}:color=white@0.9:t=fill"
    if name=='corner_marks': return f"drawbox=x={m}:y={m}:w={int(w*.14)}:h={t}:color=white@0.85:t=fill,drawbox=x={m}:y={m}:w={t}:h={int(h*.08)}:color=white@0.85:t=fill"
    if name=='progress': return f"drawbox=x={m}:y={int(h*.91)}:w='min({w-2*m},({w-2*m})*t/{max(.1,duration)})':h={t}:color=white@0.92:t=fill"
    if name=='center_card': return f"drawbox=x={int(w*.12)}:y={int(h*.30)}:w={int(w*.76)}:h={int(h*.28)}:color=black@0.18:t=fill"
    if name=='cinema_bars': return f"drawbox=x=0:y=0:w=iw:h={int(h*.055)}:color=black@0.35:t=fill,drawbox=x=0:y={int(h*.945)}:w=iw:h={int(h*.055)}:color=black@0.35:t=fill"
    if name=='soft_frame': return f"drawbox=x={m}:y={int(h*.05)}:w={w-2*m}:h={int(h*.90)}:color=white@0.22:t={max(2,int(w*.002))}"
    if name=='split_hint': return f"drawbox=x={int(w*.5)}:y={int(h*.12)}:w={t}:h={int(h*.76)}:color=white@0.45:t=fill"
    if name=='badge': return f"drawbox=x={m}:y={int(h*.10)}:w={int(w*.24)}:h={int(h*.055)}:color=black@0.38:t=fill"
    if name=='cta_panel': return f"drawbox=x={m}:y={int(h*.68)}:w={w-2*m}:h={int(h*.20)}:color=black@0.42:t=fill"
    return f"drawbox=x={m}:y={int(h*.07)}:w={int(w*.16)}:h={t}:color=white@0.92:t=fill"

def overlay_filter(style,w,h,duration):
    m=max(24,int(w*.045)); top=int(h*.06); bottom=int(h*.86)
    if style=='sales': return f"drawbox=x={m}:y={top}:w={w-2*m}:h={int(h*.08)}:color=0x101828@0.28:t=fill"
    if style=='dynamic': return f"drawbox=x={m}:y={bottom}:w='min({w-2*m},({w-2*m})*t/{max(.1,duration)})':h=7:color=white@0.65:t=fill"
    if style=='expert': return f"drawbox=x={m}:y={top}:w={w-2*m}:h={int(h*.055)}:color=black@0.18:t=fill"
    return f"drawbox=x={m}:y={top}:w={int(w*.16)}:h=6:color=white@0.72:t=fill"
