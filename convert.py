import gzip
import io
import json
import zipfile

from PIL import Image


def tgs_to_json(tgs: bytes) -> bytes:
    return gzip.decompress(tgs)


def tgs_to_dotlottie(tgs: bytes, name: str = "animation") -> bytes:
    json_bytes = tgs_to_json(tgs)
    manifest = {
        "animations": [{
            "id": name,
            "speed": 1,
            "themeColor": "#000000",
            "direction": 1,
            "playMode": "normal",
            "loop": True,
            "autoplay": True,
        }],
        "version": "1.0",
        "generator": "AnySticker",
        "revision": 1,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(f"animations/{name}.json", json_bytes)
    return buf.getvalue()


def tgs_to_png(tgs: bytes) -> bytes | None:
    try:
        from rlottie_python import Animation
        json_str = tgs_to_json(tgs).decode()
        anim = Animation.from_data(json_str, width=512, height=512)
        buf = anim.lottie_animation_render(0, 512, 512)
        img = Image.frombytes("RGBA", (512, 512), bytes(buf))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


def tgs_to_gif(tgs: bytes, size: int = 256) -> bytes | None:
    try:
        from rlottie_python import Animation
        json_str = tgs_to_json(tgs).decode()
        anim = Animation.from_data(json_str, width=size, height=size)
        total = anim.lottie_animation_get_totalframe()
        fps = anim.lottie_animation_get_framerate()
        frames = []
        for i in range(total):
            buf = anim.lottie_animation_render(i, size, size)
            rgba = Image.frombytes("RGBA", (size, size), bytes(buf))
            bg = Image.new("RGB", (size, size), (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            frames.append(bg)
        out = io.BytesIO()
        frames[0].save(
            out, format="GIF",
            save_all=True,
            append_images=frames[1:],
            loop=0,
            duration=int(1000 / fps),
        )
        return out.getvalue()
    except Exception:
        return None


def tgs_meta(tgs: bytes) -> dict | None:
    try:
        from rlottie_python import Animation
        json_str = tgs_to_json(tgs).decode()
        anim = Animation.from_data(json_str, width=64, height=64)
        total = anim.lottie_animation_get_totalframe()
        fps = anim.lottie_animation_get_framerate()
        return {"frames": total, "fps": round(fps, 1), "duration": round(total / fps, 2)}
    except Exception:
        return None


def webp_to_png(webp: bytes) -> bytes:
    img = Image.open(io.BytesIO(webp)).convert("RGBA")
    if img.size != (512, 512):
        img = img.resize((512, 512), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def webp_to_jpg(webp: bytes) -> bytes:
    img = Image.open(io.BytesIO(webp)).convert("RGB")
    if img.size != (512, 512):
        img = img.resize((512, 512), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()
