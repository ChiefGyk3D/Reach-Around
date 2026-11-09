#!/usr/bin/env python3
"""Generates the faithful frame art (static/frame-fa-*.svg), one file per size class.

Traced from the brand button art. Usage: python scripts/gen-frame-art.py src/reach_around/site/static
Static only, no glow. The -am files use ink rules for solid amber fills.
"""

import sys

AMBER = "#FBB03B"
TL_OUT = [
    (0, 3),
    (201, 3),
    (147, 52),
    (97, 52),
    (83, 70),
    (55, 70),
    (54.5, 98),
    (41, 112),
    (38, 151),
    (0, 151),
]
TL_HOLE = [
    (21, 24),
    (148, 24),
    (139, 32),
    (91, 33),
    (73, 51),
    (34, 52),
    (33, 89),
    (21, 102),
]
TOP_PLATE = [(253.3, 29.5), (276, 2.5), (364, 2.5), (379, 17), (474, 17.5), (456, 29.5)]
BOT_PLATE = [
    (240.7, 278.3),
    (456.7, 278.3),
    (493.3, 302.7),
    (378.3, 302.7),
    (366.7, 317.3),
    (272.7, 317.3),
]
BOT_WIN = [(266.7, 290.7), (376, 290.7), (362.7, 305.7), (278.3, 305.7)]
GRAD = (
    '<linearGradient id="m" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="#050505"/>'
    '<stop offset=".5" stop-color="#555"/><stop offset="1" stop-color="#1c1c1c"/></linearGradient>'
)
EDGE = (
    'stroke="#8a8a8a" stroke-opacity=".75" stroke-width="{w}" stroke-linejoin="round"'
)


def f(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def path(polys, fx, fy):
    return (
        "M"
        + "M".join(
            " ".join(f"{f(fx(x))},{f(fy(y))}" for x, y in p).replace(" ", "L", 0)
            for p in polys
        )
        if False
        else " ".join(
            "M" + " L".join(f"{f(fx(x))} {f(fy(y))}" for x, y in p) + "Z" for p in polys
        )
    )


RULE = [AMBER]


def rrect(x, y, w, h, fill=None):
    fill = fill or RULE[0]
    return f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}" rx="{f(min(h, w) / 2)}" fill="{fill}"/>'


def shape(polys, fx, fy, mirror_x, mirror_y, W, H, ew):
    X = (lambda x: W - fx(x)) if mirror_x else fx
    Y = (lambda y: H - fy(y)) if mirror_y else fy
    return f'<path d="{path(polys, X, Y)}" fill="url(#m)" fill-rule="evenodd" {EDGE.format(w=ew)}/>'


def reduced_bracket(a, b, t):
    out = [
        (0, 0),
        (a, 0),
        (a - 1.2 * t, t),
        (3.4 * t, t),
        (2.4 * t, 2 * t),
        (t, 2 * t),
        (t, b),
        (0, b),
    ]
    return [out]


def clean_bracket(a, b, t):
    return [[(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]]


def svg(W, H, level, rule=AMBER):
    RULE[0] = rule
    if level == "none":
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"/>'
    s = H / 318.0
    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs>{GRAD}</defs>'
    ]
    small = H < 60
    ew = max(0.7, 0.9 * min(1, s * 3))
    if level == "clean":
        t = max(3, H * 0.06)
        a = max(16, min(H * 0.5, W * 0.16))
        b = max(12, min(H * 0.42, H / 2 - 3))
        rt = max(1.5, H * 0.012)
        ry = max(5, H * 0.075)
        rule = lambda y, x1, x2: (
            f'<rect x="{f(x1)}" y="{f(y)}" width="{f(x2 - x1)}" height="{f(rt)}" fill="{RULE[0]}"/>'
        )
        o.append(rule(ry, a + 4, W - a - 4))
        o.append(rule(H - ry - rt, a + 4, W - a - 4))
        br = clean_bracket(a, b, t)
        for mx in (0, 1):
            for my in (0, 1):
                o.append(shape(br, lambda x: x, lambda y: y, mx, my, W, H, ew))
        o.append("</svg>")
        return "".join(o)
    # faithful
    sx = sy = s
    # plate scale / placement
    sp = min(s, (W / 2 - 6 - 240.7 * s) / 252.6)
    Xl = 240.7 * s
    if sp < s:
        Xl = 200 * s
        sp = max(0.04, min(s, (W / 2 - 6 - Xl) / 252.6))
    spy = sp
    if small:
        spy = max(sp, 0.26)  # keep plates tall enough to carry a dash

    # vertical anchoring helpers
    def px(x):
        return Xl + (x - 240.7) * sp

    rt = max(1.5, 6 * s) if not small else 1.5
    if small:
        # reduced: rules and plates compressed into the top/bottom 9px
        ry1 = 1.0
        ry2 = 1 + rt + 2.2
        yl_top = ry2
    else:
        ry1 = 1 * s
        ry2 = 18 * s
    yl_top = ry2
    yl_bot = H - ry2 - rt
    ys_top = ry1
    ys_bot = H - ry1 - rt
    # rules (behind everything)
    lx1, lx2 = 180 * s, W - 180 * s
    if small:
        lx1, lx2 = 22, W - 22
    sx1 = max(0.287 * W, px(379) + 4)
    sx2 = W - sx1
    o.append(rrect(lx1, yl_top, lx2 - lx1, rt))
    o.append(rrect(lx1, yl_bot, lx2 - lx1, rt))
    o.append(rrect(sx1, ys_top, sx2 - sx1, rt))
    if not small:
        o.append(rrect(sx1, ys_bot, sx2 - sx1, rt))
    # brackets
    if small:
        t = 4.2
        a = max(26, min(H * 1.0, W * 0.24))
        b = H / 2 - 1.2
        br = reduced_bracket(max(a, 22), b, t)
    else:
        br = [TL_OUT, TL_HOLE]
    bfx = (lambda x: x) if small else (lambda x: x * s)
    bfy = (lambda y: y) if small else (lambda y: y * s)
    for mx in (0, 1):
        for my in (0, 1):
            o.append(shape(br, bfx, bfy, mx, my, W, H, ew))
    # bracket dashes + mid stub + outer dashes
    dh = max(1.6, 6 * s)
    for mx in (0, 1):
        for my in (0, 1):
            if small and my:
                continue
            x = 33 * s if not small else 5.5
            w = 43 * s if not small else 9
            y = 31 * s if not small else 4.2
            xx = W - x - w if mx else x
            yy = H - y - dh if my else y
            if not small or True:
                o.append(rrect(xx, yy, w, dh))
    if not small:
        for mx in (0, 1):
            x = 13 * s
            w = max(1.5, 5 * s)
            xx = W - x - w if mx else x
            o.append(
                f'<rect x="{f(xx)}" y="{f(151 * s)}" width="{f(w)}" height="{f(H - 302 * s)}" fill="{RULE[0]}"/>'
            )
        for mx in (0, 1):
            for my in (0, 1):
                x = 212 * s
                w = 42 * s
                xx = W - x - w if mx else x
                if my:
                    continue
                o.append(
                    rrect(
                        xx,
                        H
                        - 6 * s
                        - 3 * s
                        - dh
                        + 6 * s
                        - dh * 0
                        - (318 * s - 318 * s)
                        - (0),
                        w,
                        dh,
                    )
                ) if False else o.append(rrect(xx, 309 * s + (H - 318 * s), w, dh))
    # plates
    for mx in (0, 1):

        def X(x):
            v = px(x)
            return W - v if mx else v

        # top plate
        ty = 2.5 * s - 2.5 * spy
        if small:
            ty = 0.8 - 2.5 * spy
        pts = [(X(x), ty + y * spy) for x, y in TOP_PLATE]
        d = "M" + " L".join(f"{f(a)} {f(b)}" for a, b in pts) + "Z"
        o.append(f'<path d="{d}" fill="url(#m)" {EDGE.format(w=ew)}/>')
        dx0 = X(284.5)
        dx1 = X(357)
        x0 = min(dx0, dx1)
        w = abs(dx1 - dx0)
        o.append(rrect(x0, ty + 14.5 * spy, w, max(1.6, 6.25 * spy), AMBER))
        if not small:
            # bottom plate with window
            by = lambda y: H - (2.5 * s - 2.5 * spy) - (y - 0) * 0 if False else None
            off = H - (318 * s)  # zero when s=H/318
            ty2 = 317.3 * s - 317.3 * spy

            def Yb(y):
                return ty2 + y * spy + off

            pts = [(X(x), Yb(y)) for x, y in BOT_PLATE]
            win = [(X(x), Yb(y)) for x, y in BOT_WIN]
            d = (
                "M"
                + " L".join(f"{f(a)} {f(b)}" for a, b in pts)
                + "Z M"
                + " L".join(f"{f(a)} {f(b)}" for a, b in win)
                + "Z"
            )
            o.append(
                f'<path d="{d}" fill="url(#m)" fill-rule="evenodd" {EDGE.format(w=ew)}/>'
            )
            dx0 = X(395)
            dx1 = X(460)
            o.append(
                rrect(min(dx0, dx1), Yb(290), abs(dx1 - dx0), max(1.6, 6 * spy), AMBER)
            )
    o.append("</svg>")
    return "".join(o)


# Size classes: name -> (width, height). One art file per class, drawn at that
# exact size and used as background 100% 100% on elements near that size.
SIZES = {
    "xs": (140, 44),
    "sm": (360, 48),
    "md": (520, 48),
    "wd": (900, 58),
    "lg": (360, 80),
    "xl": (900, 80),
    "card": (330, 130),
}

if __name__ == "__main__":
    out = sys.argv[1]
    for name, (W, H) in SIZES.items():
        with open(f"{out}/frame-fa-{name}.svg", "w") as fh:
            fh.write(svg(W, H, "faithful") + "\n")
        with open(f"{out}/frame-fa-{name}-am.svg", "w") as fh:
            fh.write(svg(W, H, "faithful", "#2D200B") + "\n")
