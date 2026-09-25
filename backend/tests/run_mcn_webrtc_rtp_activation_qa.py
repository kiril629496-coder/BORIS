from __future__ import annotations

# Backward-compatible entry point. The historical script name says "RTP",
# but synthetic SIP/WSS activation cannot prove audible bidirectional RTP.
# Delegate to the canonical truthful signaling/bridge QA instead of emitting a
# false RTP success marker.
from run_media_webrtc_rtp_bind_qa import main


if __name__ == "__main__":
    main()
