from __future__ import annotations
import os, signal, socket, sys, time

PORT=int(os.getenv("BORIS_QA_REG_PORT","5077"))
MODE_FILE=os.getenv("BORIS_QA_REG_MODE","/tmp/boris_mcn_fake_registrar_mode")
COUNT_FILE=os.getenv("BORIS_QA_REG_COUNT","/tmp/boris_mcn_fake_registrar_count")
running=True

def stop(*_):
    global running
    running=False
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)

def headers(msg:str)->dict[str,str]:
    out={}
    for line in msg.replace("\r\n","\n").split("\n")[1:]:
        if ":" not in line: continue
        k,v=line.split(":",1)
        out[k.strip().lower()]=v.strip()
    return out

def mode():
    try:
        v=open(MODE_FILE).read().strip().lower()
        return "reject" if v=="reject" else "ok"
    except Exception:
        return "ok"

register_count=0
options_count=0
sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
sock.bind(("127.0.0.1",PORT))
sock.settimeout(.2)
print("FAKE_REGISTRAR_READY",PORT,flush=True)
while running:
    try:
        data,addr=sock.recvfrom(65535)
    except socket.timeout:
        continue
    except OSError:
        break
    msg=data.decode("utf-8","replace")
    lines=msg.splitlines()
    first=lines[0] if lines else ""
    h=headers(msg)
    if first.startswith("REGISTER "):
        register_count+=1
        status="403 Forbidden" if mode()=="reject" else "200 OK"
        to=h.get("to","<sip:qa@localhost>")
        if "tag=" not in to: to += ";tag=borisqa"
        resp=[
            f"SIP/2.0 {status}",
            f"Via: {h.get('via','SIP/2.0/UDP 127.0.0.1')}",
            f"From: {h.get('from','<sip:qa@localhost>')}",
            f"To: {to}",
            f"Call-ID: {h.get('call-id','borisqa')}",
            f"CSeq: {h.get('cseq','1 REGISTER')}",
        ]
        if h.get("contact"): resp.append(f"Contact: {h['contact']}")
        resp += ["Server: BORIS-QA-Registrar/1.0","Content-Length: 0","",""]
        sock.sendto("\r\n".join(resp).encode(),addr)
    elif first.startswith("OPTIONS "):
        options_count+=1
        to=h.get("to","<sip:qa@localhost>")
        if "tag=" not in to: to += ";tag=borisqa"
        resp=[
            "SIP/2.0 200 OK",
            f"Via: {h.get('via','SIP/2.0/UDP 127.0.0.1')}",
            f"From: {h.get('from','<sip:qa@localhost>')}",
            f"To: {to}",
            f"Call-ID: {h.get('call-id','borisqa')}",
            f"CSeq: {h.get('cseq','1 OPTIONS')}",
            "Content-Length: 0","",""
        ]
        sock.sendto("\r\n".join(resp).encode(),addr)
    try:
        open(COUNT_FILE,"w").write(f"{register_count},{options_count}\n")
    except Exception:
        pass
sock.close()
try:
    open(COUNT_FILE,"w").write(f"{register_count},{options_count}\n")
except Exception:
    pass
print("FAKE_REGISTRAR_STOPPED",register_count,options_count,flush=True)
