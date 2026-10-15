"""Build a partial native-weight tree, mimicking the user's interrupted download.

State: transformer/ holds ONE >1GB shard while its index promises three, and
text_encoder/ and vae/ are absent entirely. The old rule called this complete.
"""
from pathlib import Path

T = Path(tempfile.gettempdir()) / "qwen_partial_fixture"
d = T / "Qwen-Image-2.1" / "transformer"
d.mkdir(parents=True, exist_ok=True)

with open(d / "diffusion_pytorch_model-00001-of-00003.safetensors", "wb") as fh:
    fh.truncate(1200 * 1024 * 1024)

(d / "diffusion_pytorch_model.safetensors.index.json").write_text(
    '{"weight_map":{'
    '"a":"diffusion_pytorch_model-00001-of-00003.safetensors",'
    '"b":"diffusion_pytorch_model-00002-of-00003.safetensors",'
    '"c":"diffusion_pytorch_model-00003-of-00003.safetensors"}}',
    encoding="utf-8")

print("  partial tree built:")
for p in sorted((T / "Qwen-Image-2.1").rglob("*")):
    if p.is_file():
        print(f"    {p.relative_to(T / 'Qwen-Image-2.1')}  {p.stat().st_size // 1024 // 1024} MB")
