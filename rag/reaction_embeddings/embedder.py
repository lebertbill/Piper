"""
Embeds reaction SMILES strings into fixed-length vectors.

Priority order:
  1. rxnfp via transformers — loads the rxnfp BERT model from HuggingFace directly.
     Works on Python 3.13+. Model is ~400MB, downloaded once to ~/.cache/huggingface/.
     Produces 256-dim embeddings identical to the rxnfp package.

  2. RDKit difference fingerprint — product_fp minus reactant_fp (2048-dim).
     Zero external deps beyond RDKit. Used automatically if transformers unavailable
     or if use_rxnfp=False.

The two modes are compatible within the same index (pick one and stick to it).
"""
from __future__ import annotations

import numpy as np

_RXNFP_MODEL_NAME = "rxnfp/rxnfp"


def _try_load_transformers_rxnfp():
    """
    Attempts to load the rxnfp BERT tokenizer + model via transformers.
    Returns (tokenizer, model) or raises ImportError/OSError.
    """
    from transformers import BertTokenizer, BertModel
    import torch
    tokenizer = BertTokenizer.from_pretrained(_RXNFP_MODEL_NAME)
    model = BertModel.from_pretrained(_RXNFP_MODEL_NAME)
    model.eval()
    return tokenizer, model, torch


class ReactionEmbedder:
    def __init__(self, use_rxnfp: bool = True):
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._mode = "rdkit"

        if use_rxnfp:
            try:
                self._tokenizer, self._model, self._torch = _try_load_transformers_rxnfp()
                self._mode = "rxnfp"
                print("ReactionEmbedder: using rxnfp via transformers (256-dim)")
            except Exception as e:
                print(f"ReactionEmbedder: rxnfp unavailable ({e}), falling back to RDKit (2048-dim)")

        if self._mode == "rdkit":
            from rdkit import Chem
            from rdkit.Chem import AllChem, DataStructs
            self._Chem = Chem
            self._AllChem = AllChem
            self._DataStructs = DataStructs
            print("ReactionEmbedder: using RDKit difference fingerprint (2048-dim)")

    @property
    def dim(self) -> int:
        return 256 if self._mode == "rxnfp" else 2048

    def embed(self, reaction_smiles: str) -> np.ndarray | None:
        """Embed one reaction SMILES. Returns None on failure."""
        if self._mode == "rxnfp":
            return self._embed_rxnfp(reaction_smiles)
        return self._embed_rdkit(reaction_smiles)

    def embed_batch(self, reaction_smiles_list: list[str], batch_size: int = 32) -> list[np.ndarray | None]:
        if self._mode == "rxnfp":
            results = []
            for i in range(0, len(reaction_smiles_list), batch_size):
                batch = reaction_smiles_list[i : i + batch_size]
                results.extend(self._embed_rxnfp_batch(batch))
            return results
        return [self._embed_rdkit(s) for s in reaction_smiles_list]

    # ── rxnfp via transformers ─────────────────────────────────────────────

    def _embed_rxnfp(self, rxn_smiles: str) -> np.ndarray | None:
        results = self._embed_rxnfp_batch([rxn_smiles])
        return results[0]

    def _embed_rxnfp_batch(self, rxn_smiles_list: list[str]) -> list[np.ndarray | None]:
        torch = self._torch
        try:
            encoded = self._tokenizer(
                rxn_smiles_list,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            with torch.no_grad():
                output = self._model(**encoded)
            # CLS token embedding → 256-dim
            cls_vecs = output.last_hidden_state[:, 0, :].cpu().numpy().astype(np.float32)
            return [cls_vecs[i] for i in range(len(rxn_smiles_list))]
        except Exception:
            return [None] * len(rxn_smiles_list)

    # ── RDKit fallback ─────────────────────────────────────────────────────

    def _embed_rdkit(self, rxn_smiles: str) -> np.ndarray | None:
        Chem = self._Chem
        AllChem = self._AllChem
        DataStructs = self._DataStructs
        try:
            parts = rxn_smiles.split(">>")
            if len(parts) != 2:
                return None
            reactant_part, product_part = parts

            def smiles_list_fp(smiles_str: str):
                mols = [Chem.MolFromSmiles(s) for s in smiles_str.split(".") if s]
                fps = []
                for mol in mols:
                    if mol:
                        fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
                        arr = np.zeros(2048, dtype=np.float32)
                        DataStructs.ConvertToNumpyArray(fp, arr)
                        fps.append(arr)
                return np.mean(fps, axis=0) if fps else None

            r_fp = smiles_list_fp(reactant_part)
            p_fp = smiles_list_fp(product_part)
            if r_fp is None or p_fp is None:
                return None
            return (p_fp - r_fp).astype(np.float32)
        except Exception:
            return None
