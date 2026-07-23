import torch

_toolkit = None
_rxnscribe = None
_device = torch.device('cpu')


def get_toolkit():
    global _toolkit
    if _toolkit is None:
        from miner.intelligence.core.chemietoolkit import ChemIEToolkit
        print("[ModelLoad] Loading ChemIEToolkit (OCSR)...")
        _toolkit = ChemIEToolkit(device=_device)
    return _toolkit


def get_rxnscribe():
    global _rxnscribe
    if _rxnscribe is None:
        from huggingface_hub import hf_hub_download
        from miner.intelligence.core.rxnim import RxnScribe
        ckpt_path = hf_hub_download("yujieq/RxnScribe", "pix2seq_reaction_full.ckpt")
        print(f"[ModelLoad] Loading RxnScribe from {ckpt_path}...")
        _rxnscribe = RxnScribe(ckpt_path, device=_device)
    return _rxnscribe


def preload_models():
    """
    Eagerly load all OCSR models into memory.
    Call this once at the start of a processing session so the models are
    ready before the first label resolution call, avoiding the delay mid-run.
    """
    get_toolkit()
    get_rxnscribe()
