#!/usr/bin/env python3

"""
BORIS Factory universal pipeline A-J.

A-D -> readiness/state/onboarding
E   -> bootstrap
J   -> controlled accepted facts
H   -> structured resolver
F   -> safe internal executor
G   -> provisioning
I   -> remaining missing-facts queue

Accepted J facts therefore flow:
J -> H -> F -> G -> I
"""

import subprocess
import sys


FACTORY_J_V1=True


def run(cmd):
    print(
        "$",
        " ".join(cmd),
        flush=True,
    )

    p=subprocess.run(cmd)

    if p.returncode:
        raise SystemExit(
            p.returncode
        )


def main():
    py=sys.executable

    run([
        py,
        "factory_d_runner.py",
        "--apply",
    ])

    run([
        py,
        "factory_e_runner.py",
        "--apply",
    ])

    run([
        py,
        "factory_j.py",
        "--apply",
    ])

    run([
        py,
        "factory_h.py",
        "--apply",
    ])

    run([
        py,
        "factory_f.py",
        "--apply",
    ])

    run([
        py,
        "factory_k.py",
        "--apply",
    ])

    run([
        py,
        "factory_g.py",
        "--apply",
    ])

    run([
        py,
        "factory_i.py",
        "--apply",
    ])

    run([
        py,
        "factory_l.py",
        "--apply",
    ])

    run([
        py,
        "factory_m.py",
        "--apply",
    ])

    run([
        py,
        "factory_o.py",
        "--apply",
    ])

    run([
        py,
        "factory_p.py",
        "--apply",
    ])

    run([
        py,
        "factory_q.py",
        "--apply",
    ])

    run([
        py,
        "factory_r.py",
        "--apply",
    ])

    run([
        py,
        "factory_s.py",
        "--apply",
    ])

    run([
        py,
        "factory_n.py",
        "--apply",
    ])


if __name__=="__main__":
    main()
