"""
visualheist_worker.py — Subprocess worker script for TF-ID/visualheist table detection.

This script is intentionally self-contained and designed to run inside a SEPARATE
isolated venv (venv_visualheist/) that has transformers==4.49, since Florence-2 
is incompatible with transformers>=4.50 used in the main Piper venv.

Usage (called by visualheist_extractor.py via subprocess):
    python miner/visualheist_worker.py --pdf-path <path> --output-dir <dir>
                                        [--model-id <hf_model_id>]
                                        [--pages <start_page>-<end_page>]

Outputs JSON to stdout:
    [
        {"page": 2, "label": "table", "bbox": [x1, y1, x2, y2], "saved_path": "..."},
        ...
    ]
"""

import argparse
import json
import os
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-id", default="shixuanleong/visualheist-base")
    parser.add_argument("--pages", default=None, help="e.g. '1-5' to limit page range")
    return parser.parse_args()


def pdf_to_images(pdf_path: str, pages=None):
    from pdf2image import convert_from_path  # type: ignore
    kwargs = {"dpi": 150}
    if pages:
        start, end = pages.split("-")
        kwargs["first_page"] = int(start)
        kwargs["last_page"] = int(end)
    return convert_from_path(pdf_path, **kwargs), int(kwargs.get("first_page", 1))


def load_model(model_id: str):
    import warnings
    from transformers import AutoModelForCausalLM, AutoProcessor, GenerationConfig, GenerationMixin  # type: ignore

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True, attn_implementation="eager"
        )
        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)

    # Patch: Florence-2 doesn't inherit GenerationMixin in transformers>=4.49 warning range
    inner_cls = type(model.language_model)
    if not issubclass(inner_cls, GenerationMixin):
        inner_cls.__bases__ = (GenerationMixin,) + inner_cls.__bases__

    lang_cfg = model.language_model.config
    gen_cfg = GenerationConfig(
        decoder_start_token_id=getattr(lang_cfg, "decoder_start_token_id", 2),
        bos_token_id=getattr(lang_cfg, "bos_token_id", 0),
        eos_token_id=getattr(lang_cfg, "eos_token_id", 2),
        pad_token_id=getattr(lang_cfg, "pad_token_id", 1),
    )
    gen_cfg._from_model_config = False
    model.language_model.generation_config = gen_cfg

    return model, processor


def detect(image, model, processor):
    inputs = processor(text="<OD>", images=image, return_tensors="pt")
    generated_ids = model.generate(
        input_ids=inputs["input_ids"],
        pixel_values=inputs["pixel_values"],
        max_new_tokens=1024,
        do_sample=False,
        num_beams=3,
    )
    text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    annotation = processor.post_process_generation(
        text, task="<OD>", image_size=(image.width, image.height)
    )
    return annotation["<OD>"]


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Redirect stderr prints to /dev/null to keep stdout clean for JSON
    # sys.stderr = open(os.devnull, "w")

    pages, page_offset = pdf_to_images(args.pdf_path, args.pages)
    model, processor = load_model(args.model_id)

    results = []
    pad = 10

    for i, img in enumerate(pages):
        page_num = page_offset + i
        try:
            annotation = detect(img, model, processor)
        except Exception as e:
            continue

        for j, (bbox, label) in enumerate(zip(annotation["bboxes"], annotation["labels"])):
            x1, y1, x2, y2 = [int(v) for v in bbox]
            
            # Expand top padding significantly for tables to capture reaction schemes
            pad_top = 250 if label.lower() == "table" else pad
            
            x1 = max(0, x1 - pad)
            y1 = max(0, y1 - pad_top)
            x2 = min(img.width, x2 + pad)
            y2 = min(img.height, y2 + pad)

            safe_label = label.replace(" ", "_").replace("/", "_")
            out_name = f"visualheist_page_{page_num:02d}_{safe_label}_{j+1}.png"
            out_path = str(output_dir / out_name)
            img.crop((x1, y1, x2, y2)).save(out_path)

            results.append({
                "page": page_num,
                "label": label.lower(),
                "bbox": [x1, y1, x2, y2],
                "saved_path": out_path,
            })

    # Output clean JSON to stdout — the extractor reads this
    sys.stderr = sys.__stderr__
    print(json.dumps(results))


if __name__ == "__main__":
    main()
