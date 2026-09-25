# -*- coding: utf-8 -*-
from __future__ import annotations

def audio_filter(duration,kind='voice'):
    fade=max(.2,min(.8,duration*.05)); out=max(.1,duration-fade)
    if kind=='music': return f"loudnorm=I=-24:TP=-2:LRA=9,volume=0.22,afade=t=in:st=0:d={fade:.2f},afade=t=out:st={out:.2f}:d={fade:.2f}"
    return f"loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d={fade:.2f},afade=t=out:st={out:.2f}:d={fade:.2f}"

def mix_filter(duration,voice_idx,music_idx):
    fade=max(.2,min(.8,duration*.05)); out=max(.1,duration-fade)
    # Voice is normalized first. Music is normalized, attenuated and side-chain compressed by voice.
    return (f"[{voice_idx}:a]loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d={fade:.2f},afade=t=out:st={out:.2f}:d={fade:.2f}[voicebase];[voicebase]asplit=2[voice][side];"
            f"[{music_idx}:a]loudnorm=I=-24:TP=-2:LRA=9,volume=0.30,afade=t=in:st=0:d={fade:.2f},afade=t=out:st={out:.2f}:d={fade:.2f}[music];"
            f"[music][side]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=350[ducked];"
            f"[voice][ducked]amix=inputs=2:duration=longest:weights='1 0.75':normalize=0[mix]")
