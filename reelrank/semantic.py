"""Optional: match montage shots to what the narration is saying (CLIP, runs locally).
Needs:  python -m pip install sentence-transformers
Without it the editor falls back to an even spread of shots across the trailer."""
import subprocess
from pathlib import Path
import numpy as np

_model = None


def available():
    try:
        import sentence_transformers  # noqa
        return True
    except ImportError:
        return False


def _clip():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print("  loading CLIP model (first time downloads ~600MB)...")
        _model = SentenceTransformer("clip-ViT-B-32")
    return _model


def shot_embeddings(trailer, shots, crop, cache_npz):
    """One embedding per shot (frame from the middle of the shot)."""
    cache_npz = Path(cache_npz)
    if cache_npz.exists():
        d = np.load(cache_npz)
        if len(d["emb"]) == len(shots): return d["emb"]
    from PIL import Image
    rate = 2
    vf = (f"crop={crop}," if crop else "") + f"fps={rate},scale=224:126"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(trailer), "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 126, 224, 3)
    imgs = []
    for s in shots:
        i = min(len(frames) - 1, int((s["t0"] + s["t1"]) / 2 * rate))
        imgs.append(Image.fromarray(frames[i]))
    emb = _clip().encode(imgs, batch_size=32, convert_to_numpy=True, normalize_embeddings=True)
    np.savez(cache_npz, emb=emb)
    return emb


def text_embeddings(texts):
    return _clip().encode(texts, convert_to_numpy=True, normalize_embeddings=True)
