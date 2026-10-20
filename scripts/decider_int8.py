import os
import resource
import sys
import time
from importlib.metadata import entry_points

import torch
from torch import nn
from torch.ao.nn.quantized.dynamic import Linear as QuantLinear
from torch.ao.quantization import default_dynamic_qconfig

from strands_decider import infer

MODEL = "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"
EMBED_BF16 = "--embed-bf16" in sys.argv


def rss_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def quantize_linears(root):
    targets = []
    for parent_name, parent in root.named_modules():
        for name, child in parent.named_children():
            full = f"{parent_name}.{name}"
            if type(child) is nn.Linear and "lora_" not in full:
                targets.append((parent, name, child))
    for parent, name, child in targets:
        child.float()
        child.qconfig = default_dynamic_qconfig
        setattr(parent, name, QuantLinear.from_float(child))
        del child
    return len(targets)


def to_float(module, inputs, output):
    return output.float()


def shrink_embeddings(root):
    total = 0
    for module in root.modules():
        if isinstance(module, nn.Embedding):
            module.to(torch.bfloat16)
            module.register_forward_hook(to_float)
            total += module.weight.numel()
    return total


def int8_torso(self):
    torso = self.model.torso
    started = time.perf_counter()
    with torch.inference_mode():
        count = quantize_linears(torso)
        torso.float()
        embedded = shrink_embeddings(torso) if EMBED_BF16 else 0
    print(f"[int8] quantized {count} Linear layers, embeddings bf16={EMBED_BF16} ({embedded / 1e6:.0f}M params), {time.perf_counter() - started:.1f}s, peak rss {rss_gb():.2f} GB", flush=True)


def main():
    torch.backends.quantized.engine = "fbgemm" if "fbgemm" in torch.backends.quantized.supported_engines else "qnnpack"
    infer.SystemOneEngine._upcast_torso_for_cpu = int8_torso
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = args[0] if args else "8766"
    sys.argv = ["strands-decider", "serve", MODEL, "--device", "cpu", "--port", port, "--model-name", "strands-decider-2B-int8" + ("-embbf16" if EMBED_BF16 else "")]
    cli = next(e for e in entry_points(group="console_scripts") if e.name == "strands-decider").load()
    print(f"[int8] engine={torch.backends.quantized.engine} torch={torch.__version__} port={port}", flush=True)
    cli()


if __name__ == "__main__":
    main()
